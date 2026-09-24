#!/usr/bin/env python3
"""
Breaking News Auditor -- event detector (DEFINITION.md).

Replaces WORKFLOW section 1 step 1 ("Launch -- name the event"): proposes candidates, a
human confirms, then the wave fires. It does not launch waves and it does not interpret
responses.

  python3 detect_event.py --dry-run     # print the editions. THIS IS THE MANUAL CHECK.
  python3 detect_event.py               # + model pass over the two gates; may write a candidate
  python3 detect_event.py --list
  python3 detect_event.py --confirm pending/candidate_<ts>.json

Exit codes: 0 nothing fired | 10 candidate written (use in cron to trigger a notification)

Setup: pip install httpx pyyaml
       OPENROUTER_API_KEY in .env -- only for the model pass. --dry-run needs no key.

------------------------------------------------------------------------------------------
REWRITTEN 2026-08-14 after the live pilot. Read this before "improving" it.

v1 hand-rolled the clustering: RSS from ten outlets, a token-overlap clusterer, a
hand-built cross-language concept lexicon, and a character-prefix bridge to reunite
Turkey/Turquie/Turchia. Run against 34 real headlines it merged a US aircraft carrier
story with an Italian stabbing, and "Morning news brief" with an Italian sewage death.
About 300 lines that produced garbage.

Google News Top Stories already does this clustering, across outlets and languages, and
does it well. The RSS endpoint is the useful surface:

  * It IGNORES the Google account. Verified in a signed-in browser: the web page redirects
    hl=it&gl=IT back to en-US, but /rss?hl=it&gl=IT returns Italian titles from Italian
    outlets. No incognito, no signing out, no personalization leak.
  * Each locale edition is a separately-ranked front page, so "the same story leads in two
    or more language editions" -- gate G1.3 -- becomes a plain observation instead of a
    natural-language-processing problem.
  * Every item carries its source outlet in the title suffix and a real pubDate.

So the detector's job shrinks to: fetch both editions, and ask whether one story leads in
several of them. THE CROSS-EDITION MATCHING IS NOT DONE IN CODE. Five ranked lists of six
headlines is something a person reads in twenty seconds and a model reads for a fraction
of a cent, in four languages, without a lexicon. --dry-run prints exactly those lists;
that IS the under-engineered detector, and it is a legitimate way to run this forever.

THE COST, which must be declared wherever results are published (DEFINITION.md 6.6): we
are outsourcing "what counts as a major story" to Google's ranking. That is a third-party
editorial judgement we do not control, cannot inspect, and which may change without
notice. v1's thresholds were arguable but they were ours and they were written down.
------------------------------------------------------------------------------------------
"""

import argparse, json, os, pathlib, re, sys
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

try:
    import httpx, yaml
except ImportError:
    sys.exit("pip install httpx pyyaml")

ROOT = pathlib.Path(__file__).parent

# Any change to editions, TOP_N or the gates increments this AND is dated in
# DEFINITION.md section 7. Every candidate records the version that produced it.
DEFINITION_VERSION = "3.0"

# Locale editions. Each is an independently ranked front page. Latin-script only: the
# comparison is done by a model reading the titles, so scripts it reads work fine, but
# the declared limitation in DEFINITION.md 6.5 stands until that is actually tested.
# Two editions, deliberately. Narrowed from five on 2026-08-20.
#
# The reason is verification, not convenience. Every checkable claim is scored against
# the authority of record IN ITS OWN LANGUAGE at scoring time. The operator reads
# English and Italian natively, so US and IT are the two editions where a claim can be
# checked against NWS, USGS, INGV or the Protezione Civile without a translation layer
# standing between the finding and the source. GB, FR and ES were dropped because a
# mis-scored claim in a language the scorer half-reads is worse than no claim at all.
#
# The cost is declared, not solved: one Anglophone and one Western European country is
# a narrow window on a hazard distribution that is mostly neither. See SOURCES.md --
# the hazard-authority feeds are the intended remedy, because USGS and GDACS see events
# no Google News edition leads with.
EDITIONS = [
    ("US", "en", "hl=en-US&gl=US&ceid=US:en"),
    ("IT", "it", "hl=it&gl=IT&ceid=IT:it"),
]
RSS = "https://news.google.com/rss?"

TOP_N            = 6      # items read per edition. National prominence IS this cut.
G1_MIN_OUTLETS   = 3      # distinct outlets inside the story's own cluster
G1_MAX_AGE_HOURS = 24     # rupture
FAST_HOURS       = 3      # "newest item this fresh" counts as a developing signal

# Live-coverage markers, all four editions' languages. Outlets self-declare a rolling
# story, which is the cheapest available signal that a story is still moving.
# CAUTION (found in the 2026-08-14 pull): the Italian edition permanently carries
# "Guerra Ucraina-Russia, le notizie in diretta" -- a rolling war live-blog. A live marker
# is therefore necessary but NEVER sufficient; it must clear the ongoing-crisis exclusion.
LIVE_RE = __import__("re").compile(
    r"\b(live|liveblog|breaking|diretta|in diretta|ultim'ora|ultima ora|"
    r"en direct|direct|derni\u00e8re minute|en vivo|en directo|"
    r"\u00faltima hora|minuto a minuto|in tempo reale)\b", __import__("re").I)

COOLDOWN_HOURS   = 72     # per fired slug
GLOBAL_GAP_HOURS = 72     # min gap between any two fired events
FLOOR_DAYS       = 14     # force a wave if nothing has fired in this long

CLASSIFIER_MODEL = "anthropic/claude-haiku-4.5"   # VERIFY this slug exists on OpenRouter
OR_API = "https://openrouter.ai/api/v1/chat/completions"


# ======================================================================================
# fetch
# ======================================================================================

def fetch_editions(client, verbose=True):
    """Top TOP_N items per edition. Each item: rank, title, source, published, age_hours."""
    now = datetime.now(timezone.utc)
    out, failures = {}, []
    for code, lang, q in EDITIONS:
        try:
            r = client.get(RSS + q, timeout=25, follow_redirects=True, headers={
                "User-Agent": "IndicatorBreakingNewsAuditor/2.0 (+https://indicator.media)"})
            r.raise_for_status()
            items = []
            for block in r.text.split("<item>")[1:TOP_N + 1]:
                t = re.search(r"<title>(.*?)</title>", block, re.S)
                s = re.search(r"<source[^>]*>(.*?)</source>", block, re.S)
                d = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
                # <description> embeds the cluster's other articles as an <ol> of links,
                # each with the outlet name in a <font> tag. This is how many newsrooms
                # in THIS country are on the story -- national breadth, no second country
                # required. Google caps the list at 5, so the count saturates: read it as
                # a floor ("at least n"), never as a total.
                desc = re.search(r"<description>(.*?)</description>", block, re.S)
                members = []
                if desc:
                    dd = (desc.group(1).replace("&lt;", "<").replace("&gt;", ">")
                          .replace("&amp;", "&"))
                    members = list(dict.fromkeys(
                        re.findall(r"<font[^>]*>([^<]{2,40})</font>", dd)))
                if not t:
                    continue
                title = re.sub(r"\s+", " ", t.group(1)).strip()
                src = re.sub(r"\s+", " ", s.group(1)).strip() if s else ""
                # Google appends " - Outlet" to every title; the <source> tag has it too.
                if src and title.endswith(" - " + src):
                    title = title[: -(len(src) + 3)]
                age = None
                if d:
                    try:
                        age = round((now - parsedate_to_datetime(d.group(1))
                                     ).total_seconds() / 3600, 1)
                    except Exception:
                        pass
                outlets = list(dict.fromkeys(([src] if src else []) + members))
                items.append({"rank": len(items) + 1, "title": title, "source": src,
                              "age_hours": age, "outlets": outlets,
                              "n_outlets": len(outlets),
                              "live": bool(LIVE_RE.search(title))})
            if not items:
                failures.append((code, "no items parsed"))
                continue
            out[code] = {"lang": lang, "items": items}
            if verbose:
                print(f"  {code} ({lang})  {len(items)} items")
        except Exception as e:
            failures.append((code, f"{type(e).__name__}: {e}"))
            if verbose:
                print(f"  {code} ({lang})  FAILED {type(e).__name__}")
    return out, failures


def render(editions):
    """The ranked lists, one per edition. This is what a human reads in --dry-run and what the model
    reads in the live pass -- deliberately the same text, so the automated judgement and
    the manual one are made from identical evidence."""
    L = []
    for code, d in editions.items():
        L.append(f"== {code} ({d['lang']})")
        for it in d["items"]:
            age = f"{it['age_hours']}h" if it["age_hours"] is not None else "?"
            live = " LIVE" if it["live"] else ""
            L.append(f"{it['rank']}. [{it['source']} · {age} · "
                     f"{it['n_outlets']} outlets{live}] {it['title']}")
            if len(it["outlets"]) > 1:
                L.append(f"     also: {', '.join(it['outlets'][1:])}")
        L.append("")
    return "\n".join(L)


# ======================================================================================
# the two gates -- both judged by the model, from the text above
# ======================================================================================

PROMPT = """You are applying a written research protocol to today's Google News front
pages. Five country editions, each independently ranked. Answer ONLY with JSON.

Each line is: rank. [lead outlet · age of newest item · how many outlets are on the story
· LIVE if an outlet is running rolling coverage] headline
{previous}
{editions}

STEP 1 -- GATE 1, MAJOR. Judged WITHIN A SINGLE COUNTRY. A story does not need to cross
borders; a story that only Italy is leading with is a major Italian story. All must hold:
  G1.1 rupture     -- newest item under {max_age} hours old
  G1.2 prominence  -- in the top {top_n} of at least ONE edition (already true of
                      everything shown; prefer rank 1-3, and say what the best rank is)
  G1.3 breadth     -- at least {min_outlets} distinct outlets on the story. The outlet
                      count is capped at 5 by the source, so read it as "at least n".
                      A story carried by ONE outlet is not major, however high it ranks.
If the same story appears in several editions, note that as an attribute -- it is
interesting, but it is NOT required and NOT a gate.

STEP 2 -- GATE D, DEVELOPING. This is the gate that matters most and the easiest to get
wrong. We audit MOVING TARGETS, not settled facts. The test is whether what is known is
still changing.

  DEVELOPING   "Zelensky and Trump fall out at the G7"   -- an unforeseen turn; the account
               of what happened is still being assembled and will be revised.
  NOT          "The G7 summit opens today"               -- a scheduled OCCASION. Fully
               factual on arrival. Nothing to revise. A chatbot answering it is doing
               retrieval, not breaking-news retrieval.

The unit we audit is a DEVELOPMENT, not an OCCASION. Ask: will the facts of this story be
materially different in six hours? If the honest answer is no, it fails.

Evidence that it is developing -- at least ONE must be present, and say which:
  D1 an outlet is running rolling coverage of it (LIVE on the line)
  D2 the newest item is under {fast} hours old while several outlets are already on it
  D3 it appeared in the PREVIOUS POLL above and the headline has since CHANGED --
     the strongest signal there is, because you are watching the account get rewritten

Two exclusions, both of which will otherwise pass:
  X1 PERMANENT LIVE BLOGS. An ongoing war or long-running crisis carried as a standing
     rolling page ("Guerra Ucraina-Russia, le notizie in diretta") is LIVE every single
     day. That is not a rupture. A discrete dated development inside the conflict is
     eligible; the standing blog is not.
  X2 SCHEDULED OCCASIONS with a known outcome shape -- summits opening, fixtures,
     data releases, awards, ceremonies, routine votes whose result is a formality.

STEP 3 -- GATE 2, AUDITABLE. A claim about our research design, not about importance.
Judge strictly; a "no" on any one fails:
  G2.1 contested_number    -- a quantity material to the story that varies across sources
                              or is being revised (deaths, hectares, evacuees, arrests,
                              outage hours, vote margins). Editions or outlets disagreeing
                              on a figure is STRONG evidence -- say so if you see it.
  G2.2 actionable_population -- a real group facing a decision a wrong answer would damage
                              (travel, evacuation, schooling, money, legal status,
                              locating a relative). Abstract public interest is NOT this.
  G2.3 authority_of_record -- a NAMED institution issuing the facts (civil protection,
                              court, electoral commission, police, regulator, operator).
                              Competing news outlets alone are NOT this.

STEP 4 -- for the single best candidate, classify and fill the ONE slot:
  class A physical hazard (fire, flood, quake, storm, outbreak, industrial accident)
        B violence / unrest      C institutional shock (ruling, policy, sanctions, rates)
        D election / count       E person status (death, arrest, resignation)
        F infrastructure / market (outage, cyberattack, crash, bank or platform failure)

  x_string -- the ONLY slot. A full event reference in ENGLISH, lowercase, phrased as an
    ordinary person would type it, and grammatical as the OBJECT of a sentence: every
    template composes it as "...about {{x}}", "how bad are things with {{x}} right now",
    "is {{x}} confirmed". Start with "the" where natural. Prefer the SINGULAR where it is
    natural. No dates, no outlet names, no quotes.
      good: the wildfire in the landes / the cloudflare outage / the dutch general election
      bad:  wildfire / Landes Fire 2026 / France is burning / died

  event_name -- neutral one-line title; formal register is fine here
  slug       -- kebab-case ending -YYYY-MM

Return exactly:
{{"candidates_found": int,
 "best": {{"headline": "", "editions": ["IT"], "best_rank": 1, "n_outlets": 0,
   "newest_age_hours": 0,
   "gate1": {{"rupture": bool, "prominence": bool, "breadth": bool, "pass": bool}},
   "developing": {{"D1_live": bool, "D2_fast": bool, "D3_headline_changed": bool,
                  "pass": bool, "reasoning": "one sentence — why the facts will move"}},
   "excluded": bool, "exclusion_reason": "",
   "gate2": {{"contested_number": bool, "actionable_population": bool,
              "authority_of_record": bool, "pass": bool, "reasoning": "one sentence"}},
   "class": "A", "class_alternative": "", "class_confidence": 0.0, "planned": bool,
   "x_string": "", "event_name": "", "slug": "", "authority_named": "",
   "known_contested_figures": ["each figure sources disagree on, with both values"]}},
 "runners_up": [{{"headline": "", "why_not": ""}}]}}"""


def classify(text, previous, key, model):
    prev = ""
    if previous:
        prev = ("\n=== PREVIOUS POLL (" + previous.get("ts", "?") + ") — use for D3, "
                "headline churn ===\n" + previous.get("text", "") + "\n=== CURRENT POLL "
                "===\n")
    body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content":
            PROMPT.format(editions=text, previous=prev, top_n=TOP_N,
                          max_age=G1_MAX_AGE_HOURS, min_outlets=G1_MIN_OUTLETS,
                          fast=FAST_HOURS)}]}
    r = httpx.post(OR_API, json=body, timeout=120, headers={
        "Authorization": f"Bearer {key}", "HTTP-Referer": "https://indicator.media",
        "X-Title": "Breaking News Auditor - detector"})
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    out = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    out["_classifier_model"] = r.json().get("model", model)
    return out


# ======================================================================================
# state
# ======================================================================================

def load_state():
    p = ROOT / "detector_state.json"
    return json.loads(p.read_text()) if p.exists() else {"fired": [], "last_fire_utc": None}


def save_state(s):
    (ROOT / "detector_state.json").write_text(json.dumps(s, indent=2))


def log_check(rec):
    with (ROOT / "detector_log.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")


def api_key():
    k = os.environ.get("OPENROUTER_API_KEY")
    if not k and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                k = line.split("=", 1)[1].strip()
    return k


# ======================================================================================
# commands
# ======================================================================================

def cmd_check(a):
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H%MZ")
    print(f"poll {ts} | definition v{DEFINITION_VERSION}")

    with httpx.Client() as client:
        editions, failures = fetch_editions(client)

    if not editions:
        print("\nABORT: no editions reachable. Not a null result -- an instrument failure.")
        log_check({"ts": ts, "definition_version": DEFINITION_VERSION,
                   "outcome": "instrument_failure", "failures": failures})
        return 0

    text = render(editions)
    print("\n" + text)

    if a.dry_run:
        print("[dry run] no model pass, no candidate, no state written.\n"
              "Read the lists above and apply three gates in order (DEFINITION.md):\n"
              "  MAJOR      top of ANY ONE edition, >=3 outlets on it, newest item <24h.\n"
              "             It does NOT need to cross borders.\n"
              "  DEVELOPING will the facts be materially different in six hours?\n"
              "             LIVE marker, or very fresh with several outlets, or the\n"
              "             headline changed since the last poll. Exclude standing live\n"
              "             blogs for ongoing wars, and scheduled occasions -- \n"
              "             \"the G7 opens today\" is out, \"they fell out at the G7\" is in.\n"
              "  AUDITABLE  a contested moving number, a population facing a decision,\n"
              "             a named authority of record. All three, or log and move on.")
        return 0

    state = load_state()
    previous = state.get("previous_poll")
    key = api_key()
    if not key:
        print("no OPENROUTER_API_KEY -- use --dry-run and judge the lists yourself")
        return 0

    print(f"\napplying gates via {a.classifier_model} ...")
    try:
        v = classify(text, state.get("previous_poll"), key, a.classifier_model)
    except Exception as e:
        print(f"classifier failed: {type(e).__name__}: {e}")
        log_check({"ts": ts, "definition_version": DEFINITION_VERSION,
                   "outcome": "classifier_error", "error": str(e)})
        return 0

    state["previous_poll"] = {"ts": ts, "text": text}
    save_state(state)

    rec = {"ts": ts, "definition_version": DEFINITION_VERSION,
           "editions_ok": sorted(editions), "failures": failures,
           "candidates_found": v.get("candidates_found"),
           "runners_up": v.get("runners_up", [])}
    best = v.get("best") or {}

    for label, cond, extra in (
        ("no_candidate_story", not best, {}),
        ("gate1_fail", not (best.get("gate1") or {}).get("pass"), {"gate1": best.get("gate1")}),
        ("excluded", best.get("excluded"), {"reason": best.get("exclusion_reason")}),
        # Major but already settled -- a scheduled occasion, or a standing live blog.
        # Logged like any other outcome: how much of the news is static is worth knowing.
        ("not_developing", not (best.get("developing") or {}).get("pass"),
         {"developing": best.get("developing")}),
        # Passing gate 1 and failing gate 2 is a RESULT, not a miss: it measures the share
        # of major news this method structurally cannot audit (DEFINITION.md s1).
        ("gate1_pass_gate2_fail", not (best.get("gate2") or {}).get("pass"),
         {"gate2": best.get("gate2")}),
        # Design doc V4 (2026-09-23): scope is catastrophes only (class A: physical harm), and Gate 3 needs
        # all three conditions, whatever the model wrote in its own "pass" field. A speech with a named
        # speaker passed on 2026-09-23 09:43 because neither was enforced in code.
        ("not_a_catastrophe", (best.get("class") or "").upper() != "A",
         {"class": best.get("class"), "reason": "scope is catastrophes only (class A)"}),
        ("gate3_condition_missing", not all((best.get("gate2") or {}).get(k) for k in
                                            ("contested_number", "actionable_population", "authority_of_record")),
         {"gate2": best.get("gate2"), "reason": "all three Gate 3 conditions must hold"}),
    ):
        if cond:
            rec["outcome"] = label
            rec["headline"] = best.get("headline", "")
            rec.update(extra)
            log_check(rec)
            print(f"\n{label}: {best.get('headline','(none)')}")
            if label == "not_developing":
                print(f"  {(best.get('developing') or {}).get('reasoning','')}")
            if label == "gate1_pass_gate2_fail":
                print(f"  {(best.get('gate2') or {}).get('reasoning','')}")
                print("  Logged. This population is a finding, not a miss.")
            return 0

    slug = best.get("slug") or "candidate"
    for f in state["fired"]:
        if f["slug"] == slug and (now - datetime.fromisoformat(f["ts"])).total_seconds() / 3600 < COOLDOWN_HOURS:
            rec["outcome"] = "suppressed_cooldown"
            log_check(rec)
            print(f"\nsuppressed: {slug} fired within {COOLDOWN_HOURS}h")
            return 0
    if state.get("last_fire_utc"):
        gap = (now - datetime.fromisoformat(state["last_fire_utc"])).total_seconds() / 3600
        if gap < GLOBAL_GAP_HOURS:
            rec["outcome"] = "suppressed_global_gap"
            log_check(rec)
            print(f"\nsuppressed: only {gap:.0f}h since the last wave "
                  f"(min {GLOBAL_GAP_HOURS}h)")
            return 0

    (ROOT / "pending").mkdir(exist_ok=True)
    cand = {"candidate_ts_utc": ts, "definition_version": DEFINITION_VERSION,
            "source": "google-news-rss", "editions_text": text, **best}
    path = ROOT / "pending" / f"candidate_{ts}.json"
    path.write_text(json.dumps(cand, indent=2))
    rec["outcome"] = "candidate"
    rec["slug"] = slug
    rec["class"] = best.get("class")
    log_check(rec)

    print(f"\nCANDIDATE  {best.get('event_name')}")
    d = best.get("developing") or {}
    sig = ", ".join(k[:2] for k in ("D1_live", "D2_fast", "D3_headline_changed") if d.get(k))
    print(f"  class {best.get('class')} (conf {best.get('class_confidence')}) · "
          f"rank {best.get('best_rank')} in {best.get('editions')} · "
          f"{best.get('n_outlets')} outlets · {best.get('newest_age_hours')}h old")
    print(f"  developing [{sig or 'none'}]: {d.get('reasoning','')}")
    print(f"  gate2: {(best.get('gate2') or {}).get('reasoning')}")
    print(f"  X={best.get('x_string')} | X2={best.get('x2_string')} | A={best.get('a_string')}")
    for f in best.get("known_contested_figures") or []:
        print(f"  contested: {f}")
    print(f"\nwritten: pending/{path.name}")
    print(f"REVIEW, THEN CONFIRM:\n  python3 detect_event.py --confirm pending/{path.name}")
    return 10


def cmd_list(a):
    d = ROOT / "pending"
    files = sorted(d.glob("candidate_*.json")) if d.exists() else []
    if not files:
        print("no pending candidates")
        return 0
    for p in files:
        c = json.loads(p.read_text())
        print(f"{p.name}  class {c.get('class')}  {c.get('event_name')}")
    return 0


def cmd_confirm(a):
    p = pathlib.Path(a.confirm)
    if not p.is_absolute():
        p = ROOT / p
    c = json.loads(p.read_text())
    slug = a.slug or c.get("slug")
    cls = a.event_class or c.get("class")
    if not slug or not cls:
        sys.exit("candidate is missing slug or class; pass --slug / --event-class")

    q = yaml.safe_load((ROOT / "questions.yaml").read_text())
    if cls not in q.get("modulator", {}):
        sys.exit(f"class {cls} has no modulator in questions.yaml")

    d = ROOT / "events" / slug
    if (d / "event.json").exists():
        sys.exit(f"events/{slug}/event.json already exists -- append-only (WORKFLOW s4).")
    d.mkdir(parents=True, exist_ok=True)

    ev = {
        "event_slug": slug,
        "event_name": c.get("event_name", ""),
        "event_class": cls,
        "event_class_name": q["modulator"][cls]["name"],
        "event_start_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event_start_is_detection_time": True,
        "planned": bool(c.get("planned")),
        "x_string": c.get("x_string", ""),
        "questions_version": q.get("version"),
        "detector": {"definition_version": c.get("definition_version"),
                     "source": c.get("source"), "candidate_file": p.name,
                     "editions": c.get("editions"), "best_rank": c.get("best_rank"),
                     "n_outlets": c.get("n_outlets"),
                     "gate1": c.get("gate1"), "developing": c.get("developing"),
                     "gate2": c.get("gate2"),
                     "classifier_model": c.get("_classifier_model"),
                     "class_confidence": c.get("class_confidence"),
                     "class_alternative": c.get("class_alternative"),
                     "editions_text": c.get("editions_text")},
        "authority_of_record": c.get("authority_named", ""),
        "notes": "",
        "unresolved_source_conflicts": [
            "TODO BEFORE THE WAVE: check each figure below against a PRIMARY source and "
            "record any that sources genuinely disagree on. Models are NOT scored against "
            "entries in this list. Writing it after the wave is unfalsifiable (WORKFLOW s4).",
        ] + [f"CANDIDATE CONTESTED FIGURE (verify): {x}"
             for x in (c.get("known_contested_figures") or [])],
        "dry_run": False,
    }
    (d / "event.json").write_text(json.dumps(ev, indent=2))

    state = load_state()
    now = datetime.now(timezone.utc)
    state["fired"].append({"slug": slug, "class": cls, "ts": now.isoformat()})
    state["last_fire_utc"] = now.isoformat()
    save_state(state)
    p.rename(p.parent / ("confirmed_" + p.name))

    qs = dict(q["universal"])
    qs[cls + "9"] = q["modulator"][cls]["template"]
    print(f"events/{slug}/event.json written -- class {cls} "
          f"({q['modulator'][cls]['name']})\n")
    print("GRID:")
    for qid, tpl in qs.items():
        print(f"  {qid}: {tpl.format(X=ev['x_string'])}")
    print(f"\nWhat {cls}9 targets:\n  {q['modulator'][cls]['targets'].strip()}\n")
    print("BEFORE THE WAVE:")
    print("  1. pre-flight checklist (WORKFLOW s4) -- personalization is not just memory")
    print("  2. fill unresolved_source_conflicts from PRIMARY sources")
    print("  3. record account tiers and the IP-derived location in the manifest")
    print(f"\nTHEN:\n  python3 openrouter_runner.py --event {slug} --reps 2")
    print("  ...and the UI wave, both reps back-to-back per question-triple "
          "(RUNNER_NOTES s5a)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print the editions and stop -- the manual detector")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--confirm", metavar="CANDIDATE_JSON")
    ap.add_argument("--slug")
    ap.add_argument("--event-class")
    ap.add_argument("--classifier-model", default=CLASSIFIER_MODEL)
    a = ap.parse_args()
    if a.list:
        return cmd_list(a)
    if a.confirm:
        return cmd_confirm(a)
    return cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
