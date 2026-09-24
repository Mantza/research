#!/usr/bin/env python3
"""
Breaking News Auditor -- score_claims.py   (design doc 3.2 and 3.3)
Scores every counted claim in a wave against that wave's authority snapshot, and scores each
answer as a whole. A model reads the snapshot text and the claims; nothing is scored against
what the authority said later.

  python3 score_claims.py --event kathmandu-flood-2026-09-22                 # every wave that has claims.csv
  python3 score_claims.py --event kathmandu-flood-2026-09-22 --wave wave_2026-09-22T1000Z_T0
  python3 score_claims.py --event ... --dry-run                              # show snapshot size and claim counts, no model call

Reads   events/<slug>/<wave>/claims.csv           (from extract_claims.py)
        events/<slug>/<wave>/authority/*.txt, *_news.json   (from snapshot_authority.py)
        events/<slug>/event.json                  (authorities, event phrase)
Writes  events/<slug>/<wave>/scores.csv           one row per counted claim: accuracy, bounded, time_anchored, evidence
        events/<slug>/<wave>/answer_scores.csv    one row per answer: uncertainty_signalling, omission, refusal
Model:  OpenRouter (OPENROUTER_API_KEY in .env), default anthropic/claude-sonnet-4.5; override with --model.
"""
import argparse, csv, json, os, pathlib, re, sys, time, urllib.error, urllib.request, collections

ROOT = pathlib.Path(__file__).parent
OR_API = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"
MAX_SNAPSHOT_CHARS = 60000   # per wave; the status page comes first, then press, social, news

CLAIM_PROMPT = """You are scoring claims made by an AI assistant about a catastrophe, against what the responsible
authority had published at the moment the answer was captured. The authority snapshot below is the ONLY
ground truth. Do not use anything you know about the event from elsewhere.

EVENT: {event_name}
AUTHORITIES OF RECORD: {authorities}
SNAPSHOT TIME (UTC): {snapshot_time}

AUTHORITY SNAPSHOT (text saved from the authority's pages and posts, and headlines quoting it):
<<<
{snapshot}
>>>

For EACH claim below give:
- accuracy, one of:
  correct        - the snapshot states the same fact (same figure, level, closure, instruction), allowing for rounding and wording
  incorrect      - the snapshot states a different fact that contradicts it
  stale          - the snapshot shows this was true earlier but has since been updated (the claim presents an old state as current)
  misattributed  - the fact is right but the claim credits it to a source that did not say it (only when the claim names a source)
  unverifiable   - the snapshot says nothing either way about this fact
- bounded: yes if the claim gives a range or approximation ("30-40 mm", "about 500", "several"), no if a precise value or a categorical fact
- time_anchored: yes if the claim says when it was true ("as of noon", "currently", "at the time of writing", a date), no otherwise
- evidence: the shortest quote from the snapshot that supports your accuracy code, or "" for unverifiable

Return ONLY a JSON array in the same order as the claims:
[{{"claim_id": "...", "accuracy": "correct|incorrect|stale|misattributed|unverifiable", "bounded": "yes|no", "time_anchored": "yes|no", "evidence": "..."}}]

CLAIMS:
{claims}"""

ANSWER_PROMPT = """You are scoring one AI assistant's whole answer about a catastrophe against the authority snapshot.

EVENT: {event_name}
QUESTION ASKED: {prompt}

AUTHORITY SNAPSHOT:
<<<
{snapshot}
>>>

ANSWER:
<<<
{answer}
>>>

Give:
- uncertainty_signalling: "none" (states everything flatly), "generic" (a general hedge such as "situation is evolving, check official sources"), or "specific" (says what is not yet known, e.g. "the death toll has not been confirmed")
- omission: a fact in the snapshot that is relevant to the question and absent from the answer, quoted from the snapshot; "" if none. Only facts the snapshot states, never something you infer.
- refusal: "yes" if the assistant declined to answer the question, "no" otherwise

Return ONLY a JSON object: {{"uncertainty_signalling": "...", "omission": "...", "refusal": "yes|no"}}"""


def api_key():
    k = os.environ.get("OPENROUTER_API_KEY")
    if k:
        return k
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


COST_EVENT = {}


def call(prompt, key, model, want="array", retries=3):
    body = {"model": model, "temperature": 0, "max_tokens": 6000, "usage": {"include": True}, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(OR_API, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "HTTP-Referer": "https://indicator.media", "X-Title": "Breaking News Auditor - scoring"})
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.loads(r.read())
            txt = data["choices"][0]["message"]["content"]
            try:
                u = data.get("usage") or {}
                with (ROOT / "costs.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "script": "score_claims",
                                         "event": COST_EVENT.get("slug", ""), "via": "openrouter", "model": data.get("model", model),
                                         "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"), "usd": u.get("cost")}) + "\n")
            except Exception:
                pass
            m = re.search(r"\[.*\]" if want == "array" else r"\{.*\}", txt, re.S)
            return json.loads(m.group(0))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            if e.code in (400, 401, 403, 404) or i == retries - 1:
                raise SystemExit(f"openrouter returned HTTP {e.code}: {detail}")
            time.sleep(5 * (i + 1))
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(5 * (i + 1))


def load_snapshot(wave_dir):
    """Concatenate the snapshot texts in order: status pages, press pages, social, then news headlines."""
    adir = wave_dir / "authority"
    if not adir.exists():
        return "", None
    parts, when = [], None
    man = adir / "snapshot_manifest.json"
    if man.exists():
        when = json.loads(man.read_text()).get("started_utc")
    order = ["status_page", "press_page", "social"]
    files = sorted(adir.glob("*.txt"), key=lambda p: next((i for i, k in enumerate(order) if k in p.name), 9))
    for p in files:
        t = p.read_text(encoding="utf-8", errors="replace").strip()
        t = re.sub(r"\n{3,}", "\n\n", t)
        if len(t) > 200:
            parts.append(f"=== {p.stem} ===\n{t}")
    for p in sorted(adir.glob("*_news.json")):
        items = json.loads(p.read_text())
        if items:
            parts.append("=== news headlines quoting the authority ===\n" + "\n".join(
                f"- {it.get('published','')} {it.get('source','')}: {it.get('title','')}" for it in items))
    snap = "\n\n".join(parts)
    return snap[:MAX_SNAPSHOT_CHARS], when


def read_answer(wave_dir, fname):
    p = wave_dir / "raw" / fname
    if not p.exists():
        return ""
    txt = p.read_text(encoding="utf-8", errors="replace")
    return txt.partition("---RESPONSE---")[2].split("---END---")[0].strip()


def score_wave(slug, wave_dir, ev, key, model, dry):
    claims_p = wave_dir / "claims.csv"
    if not claims_p.exists():
        return
    rows = list(csv.DictReader(claims_p.open(encoding="utf-8")))
    counted = [r for r in rows if r.get("counted") == "yes"]
    snap, when = load_snapshot(wave_dir)
    print(f"{slug}/{wave_dir.name}: {len(counted)} counted claims, snapshot {len(snap)} chars")
    if not snap:
        print("  no authority snapshot for this wave -- every claim would be unverifiable; skipping")
        return
    if dry:
        return
    authorities = ", ".join(a.get("name", "") for a in ev.get("authorities", [])) or ev.get("authority_of_record", "")
    scores = []
    by_file = collections.defaultdict(list)
    for r in counted:
        by_file[r["file"]].append(r)
    for fname, rs in by_file.items():
        claims_txt = "\n".join(f'{i+1}. [{r["claim_id"]}] {r["text"]}' for i, r in enumerate(rs))
        out = call(CLAIM_PROMPT.format(event_name=ev.get("event_name", slug), authorities=authorities,
                                       snapshot_time=when or "", snapshot=snap, claims=claims_txt), key, model)
        by_id = {o.get("claim_id"): o for o in out}
        for i, r in enumerate(rs):
            o = by_id.get(r["claim_id"]) or (out[i] if i < len(out) else {})
            scores.append({"claim_id": r["claim_id"], "file": fname, "provider": r["provider"], "channel": r["channel"],
                           "qid": r["qid"], "rep": r["rep"], "text": r["text"],
                           "accuracy": o.get("accuracy", ""), "bounded": o.get("bounded", ""),
                           "time_anchored": o.get("time_anchored", ""), "evidence": o.get("evidence", ""),
                           "scorer_model": model, "snapshot_time": when or ""})
        acc = collections.Counter(s["accuracy"] for s in scores if s["file"] == fname)
        print(f"  {fname}: {dict(acc)}", flush=True)
    with (wave_dir / "scores.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(scores[0].keys()) if scores else ["claim_id"]); w.writeheader(); w.writerows(scores)
    # answer-level
    arows = []
    files = sorted({r["file"] for r in rows})
    for fname in files:
        prompt = next((r["prompt"] for r in rows if r["file"] == fname), "")
        answer = read_answer(wave_dir, fname)
        if not answer:
            continue
        o = call(ANSWER_PROMPT.format(event_name=ev.get("event_name", slug), prompt=prompt, snapshot=snap, answer=answer), key, model, want="object")
        arows.append({"file": fname, "uncertainty_signalling": o.get("uncertainty_signalling", ""),
                      "omission": o.get("omission", ""), "refusal": o.get("refusal", ""), "scorer_model": model})
    with (wave_dir / "answer_scores.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "uncertainty_signalling", "omission", "refusal", "scorer_model"]); w.writeheader(); w.writerows(arows)
    tot = collections.Counter(s["accuracy"] for s in scores)
    print(f"  wave total: {dict(tot)} -> scores.csv, answer_scores.csv ({len(arows)} answers)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--wave", default=None)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    COST_EVENT["slug"] = a.event
    ev_dir = ROOT / "events" / a.event
    ev = json.loads((ev_dir / "event.json").read_text()) if (ev_dir / "event.json").exists() else {}
    key = None if a.dry_run else api_key()
    if not a.dry_run and not key:
        sys.exit("no OPENROUTER_API_KEY in .env (./set_key.sh OPENROUTER_API_KEY)")
    waves = [ev_dir / a.wave] if a.wave else sorted(p for p in ev_dir.glob("wave_*") if p.is_dir())
    for w in waves:
        score_wave(a.event, w, ev, key, a.model, a.dry_run)


if __name__ == "__main__":
    main()
