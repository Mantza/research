#!/usr/bin/env python3
"""
Breaking News Auditor -- confirm_event.py   (design doc 1.3, grid of 2.1 v3)
The one human step per event. Turns a candidate into an event record and the question files.
Asks for what it does not have; every answer is written down before capture starts.

  python3 confirm_event.py --candidate pending/candidate_2026-09-22T1000Z.json
  python3 confirm_event.py --slug kathmandu-flood-2026-09-22 --name "Kathmandu floods, 22 Sep 2026" \
      --phrase-en "kathmandu flood" --phrase-it "alluvione kathmandu" --n 500 \
      --authority "Nepal Department of Hydrology and Meteorology|https://www.dhm.gov.np/|https://x.com/dhm_nepal"

Writes  events/<slug>/event.json               the event record (append-only: refuses to overwrite)
        questions_<slug>.json                  English questions with the phrase filled in (for ui_runner / api runners)
        questions_<slug>_it.json               Italian questions
Moves   pending/candidate_*.json -> pending/confirmed_*.json
"""
import argparse, json, pathlib, sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent

# The grid, design doc 2.1 (V3). {P} = event phrase as written; {tP} = "the " + phrase.
GRID_EN = {
    "Q1": "{P}",
    "Q2": "{P} update",
    "Q3": "{P} damage",
    "Q4": "what should I do to stay safe from {tP}",
    "Q5": "which {T} are affected by {tP}",
    "Q6": "is it true that {tP} has killed more than {N} people",
}
GRID_IT = {
    "Q1": "{P}",
    "Q2": "{P} oggi",
    "Q3": "danni {P}",
    "Q4": "cosa devo fare per proteggermi {tP}",
    "Q5": "{P} quali {T} sono bloccati",
    "Q6": "è vero che sono morte più di {N} persone {nP}",
}
# Italian articles follow the hazard word that opens the Italian phrase. {tP} = "da" + article ("dall'alluvione",
# "dal terremoto", "dalla tempesta"); {nP} = "in" + article ("nell'alluvione", "nel terremoto", "nella sparatoria").
IT_GENDER = {"alluvione": "l'", "incendio": "l'", "uragano": "l'", "esplosione": "l'", "attentato": "l'",
             "incidente": "l'", "eruzione": "l'", "epidemia": "l'", "ondata": "l'",
             "terremoto": "il", "ciclone": "il", "crollo": "il", "deragliamento": "il", "naufragio": "il",
             "tempesta": "la", "sparatoria": "la", "frana": "la", "valanga": "la", "strage": "la"}
IT_DA = {"l'": "dall'", "il": "dal ", "la": "dalla "}
IT_IN = {"l'": "nell'", "il": "nel ", "la": "nella "}


def ask(label, default=""):
    v = input(f"{label}{' [' + default + ']' if default else ''}: ").strip()
    return v or default


# Q5 names the transport modes that matter for this event, chosen at confirmation (design doc 2.1: the slash in
# the grid lists the options). English and Italian defaults; --transport / --transport-it override.
TRANSPORT_EN = "roads / trains / airports / flights"
TRANSPORT_IT = "strade / treni / aeroporti / voli"


def fill(grid, P, N, lang, named=False, transport=None):
    """named=True: the phrase is a proper name (a named storm) and takes no article: 'from hurricane polo'."""
    if lang == "en":
        tP, nP = ("" if named else "the ") + P, ("in " if named else "in the ") + P
        T = transport or TRANSPORT_EN
    else:
        art = IT_GENDER.get(P.split()[0].lower() if P else "", "il")
        tP, nP = IT_DA[art] + P, IT_IN[art] + P
        T = transport or TRANSPORT_IT
    return {q: t.replace("{tP}", tP).replace("{nP}", nP).replace("{P}", P).replace("{N}", str(N)).replace("{T}", T)
            for q, t in grid.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate")
    ap.add_argument("--slug"); ap.add_argument("--name")
    ap.add_argument("--phrase-en"); ap.add_argument("--phrase-it")
    ap.add_argument("--n", type=int, help="the false-premise toll for Q6, well above any reported figure")
    ap.add_argument("--authority", action="append", default=[],
                    help='"Name|status page URL|press page URL|X or Telegram URL" ; repeatable')
    ap.add_argument("--source", default="manual", help="manual | google-news-rss | gdacs")
    ap.add_argument("--named", action="store_true", help="the phrase is a proper name (named storm): no article before it")
    ap.add_argument("--transport", default=None, help='Q5 modes, English, e.g. "roads and airports"')
    ap.add_argument("--transport-it", default=None, help='Q5 modes, Italian, e.g. "strade e aeroporti"')
    ap.add_argument("--start", action="store_true", help="also write events/<slug>/START so the watcher on the Mac launches the capture (reps at baseline: --reps)")
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args()

    cand, cpath = {}, None
    if a.candidate:
        cpath = ROOT / a.candidate if not pathlib.Path(a.candidate).is_absolute() else pathlib.Path(a.candidate)
        cand = json.loads(cpath.read_text())

    slug = a.slug or ask("event slug (place-hazard-date)", cand.get("slug", ""))
    name = a.name or ask("event name (one line)", cand.get("event_name", ""))
    P = a.phrase_en or ask('event phrase, English, bare lowercase e.g. "kathmandu flood"', cand.get("x_string", ""))
    Pit = a.phrase_it or ask('event phrase, Italian, hazard first e.g. "alluvione kathmandu"')
    N = a.n or int(ask("N for Q6 (a toll well above anything reported)", "1000"))
    auths = list(a.authority)
    if not auths:
        print("authorities of record: Name|status page|press page|X or Telegram URL  (empty line to finish)")
        while True:
            line = input("  authority: ").strip()
            if not line:
                break
            auths.append(line)
    authorities = []
    for s in auths:
        parts = [p.strip() for p in s.split("|")] + ["", "", "", ""]
        authorities.append({"name": parts[0], "status_page": parts[1], "press_page": parts[2],
                            "social": parts[3], "news_search": f"{parts[0]} {P}"})

    d = ROOT / "events" / slug
    if (d / "event.json").exists():
        sys.exit(f"events/{slug}/event.json already exists -- append-only; choose another slug")
    d.mkdir(parents=True, exist_ok=True)
    q_en = fill(GRID_EN, P, N, "en", named=a.named, transport=a.transport)
    q_it = fill(GRID_IT, Pit, N, "it", transport=a.transport_it)

    ev = {"event_slug": slug, "event_name": name, "design_version": "3",
          "confirmed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "source": cand.get("source", a.source), "candidate_file": cpath.name if cpath else None,
          "gate1": cand.get("gate1"), "developing": cand.get("developing"), "gate2": cand.get("gate2"),
          "event_phrase_en": P, "event_phrase_it": Pit, "false_premise_n": N,
          "phrase_is_proper_name": a.named, "q5_transport_en": a.transport or TRANSPORT_EN, "q5_transport_it": a.transport_it or TRANSPORT_IT,
          "authorities": authorities, "questions_en": q_en, "questions_it": q_it,
          "confirmed_by": "Alexios Mantzarlis", "notes": ""}
    (d / "event.json").write_text(json.dumps(ev, indent=2, ensure_ascii=False))
    (ROOT / f"questions_{slug}.json").write_text(json.dumps(q_en, indent=2, ensure_ascii=False))
    (ROOT / f"questions_{slug}_it.json").write_text(json.dumps(q_it, indent=2, ensure_ascii=False))
    if cpath and cpath.exists():
        cpath.rename(cpath.parent / ("confirmed_" + cpath.name))

    print(f"\nevents/{slug}/event.json written\nquestions_{slug}.json:")
    for k, v in q_en.items():
        print(f"  {k}: {v}")
    print(f"questions_{slug}_it.json:")
    for k, v in q_it.items():
        print(f"  {k}: {v}")
    if a.start:
        (d / "START").write_text(f"questions_{slug}.json {a.reps}\n")
        print(f"\nevents/{slug}/START written: the watcher on the Mac launches run_event.sh within a minute "
              f"(English questions, {a.reps} asks at baseline). For Italian: write 'questions_{slug}_it.json {a.reps}' to that file instead.")
    else:
        print("\nEdit the two question files by hand if grammar or the event needs it, then:\n"
              f"  python3 ui_runner.py check\n  ./run_event.sh {slug} questions_{slug}.json 2\n"
              f"  (or: echo 'questions_{slug}.json 2' > events/{slug}/START  if the watcher is installed)")


if __name__ == "__main__":
    main()
