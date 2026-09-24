#!/usr/bin/env python3
"""
Breaking News Auditor -- poll_triggers.py
The trigger feed (design doc 1.1): reads the GDACS alert feed and passes every NEW orange or
red alert episode into the pipeline as a candidate. Gate 1 is passed automatically for these
(design doc 1.2); gates 2 and 3 are judged at confirmation, since GDACS carries no news text.

  python3 poll_triggers.py              # poll once; write candidates; log the poll
  python3 poll_triggers.py --dry-run    # print orange/red alerts, write nothing
  python3 poll_triggers.py --min-level orange|red   (default orange)
  python3 poll_triggers.py --include-drought         (droughts are skipped by default: slow-onset, never breaking)

Reads   https://www.gdacs.org/xml/rss.xml   (GDACS ingests USGS and EMSC for earthquakes)
Writes  pending/candidate_gdacs_<ts>.json   one per new orange/red episode (same folder detect_event.py uses)
        trigger_state.json                  episodes already seen, so nothing fires twice
        detector_log.jsonl                  one line per poll, source "gdacs" -- the denominator
Runs on the Mac (network needed). No key needed.
"""
import argparse, json, pathlib, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent
FEED = "https://www.gdacs.org/xml/rss.xml"
NS = {"gdacs": "http://www.gdacs.org"}
LEVELS = {"green": 0, "orange": 1, "red": 2}
HAZARD = {"EQ": "earthquake", "TC": "cyclone", "FL": "flood", "VO": "volcanic eruption",
          "DR": "drought", "WF": "wildfire", "TS": "tsunami"}


def fetch():
    req = urllib.request.Request(FEED, headers={
        "User-Agent": "IndicatorBreakingNewsAuditor/1.0 (+https://indicator.media)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def g(item, tag):
    e = item.find(f"gdacs:{tag}", NS)
    return (e.text or "").strip() if e is not None and e.text else ""


def parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        lvl = (g(it, "episodealertlevel") or g(it, "alertlevel")).lower()
        out.append({
            "eventid": g(it, "eventid"), "episodeid": g(it, "episodeid"),
            "eventtype": g(it, "eventtype"), "hazard": HAZARD.get(g(it, "eventtype"), g(it, "eventtype")),
            "alertlevel": g(it, "alertlevel").lower(), "episodealertlevel": lvl,
            "alertscore": g(it, "alertscore"), "country": g(it, "country"), "iso3": g(it, "iso3"),
            "eventname": g(it, "eventname"), "severity": g(it, "severity"),
            "population": g(it, "population"), "fromdate": g(it, "fromdate"), "todate": g(it, "todate"),
            "iscurrent": g(it, "iscurrent"), "datemodified": g(it, "datemodified"),
            "title": (it.findtext("title") or "").strip(),
            "description": (it.findtext("description") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "cap": g(it, "cap"),
        })
    return out


def load_state():
    p = ROOT / "trigger_state.json"
    return json.loads(p.read_text()) if p.exists() else {"seen": {}}


def save_state(s):
    (ROOT / "trigger_state.json").write_text(json.dumps(s, indent=2))


def log(rec):
    with (ROOT / "detector_log.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")


def slug_for(a):
    place = (a["country"] or a["iso3"] or "unknown").lower().replace(" ", "-")
    return f"{place}-{a['hazard'].replace(' ', '-')}-{datetime.now(timezone.utc):%Y-%m-%d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-level", default="orange", choices=["orange", "red"])
    ap.add_argument("--include-drought", action="store_true")
    a = ap.parse_args()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    try:
        alerts = parse(fetch())
    except Exception as e:
        print(f"poll {ts} gdacs FAILED {type(e).__name__}: {e}")
        if not a.dry_run:
            log({"ts": ts, "source": "gdacs", "outcome": "instrument_failure", "error": str(e)})
        return 0
    hot = [x for x in alerts if LEVELS.get(x["episodealertlevel"], 0) >= LEVELS[a.min_level]]
    n_dr = sum(1 for x in hot if x["eventtype"] == "DR")
    if not a.include_drought:
        hot = [x for x in hot if x["eventtype"] != "DR"]
    print(f"poll {ts} gdacs: {len(alerts)} alerts, {len(hot)} at {a.min_level} or above"
          + (f" ({n_dr} drought alerts skipped)" if n_dr and not a.include_drought else ""))
    for x in hot:
        print(f"  {x['episodealertlevel'].upper():6} {x['eventtype']} {x['country']:<25} "
              f"{x['title'][:70]}  ev={x['eventid']} ep={x['episodeid']}")
    if a.dry_run:
        return 0

    state = load_state()
    new = []
    for x in hot:
        key = f"{x['eventtype']}{x['eventid']}:{x['episodeid']}"
        if key in state["seen"]:
            continue
        state["seen"][key] = ts
        new.append(x)
    save_state(state)

    (ROOT / "pending").mkdir(exist_ok=True)
    written = []
    for x in new:
        cand = {"candidate_ts_utc": ts, "source": "gdacs", "gate1": {"pass": True,
                "reason": f"GDACS {x['episodealertlevel']} alert (auto-pass, design doc 1.2)"},
                "developing": None, "gate2": None,
                "event_name": x["title"], "slug": slug_for(x), "x_string": "",
                "authority_named": "", "hazard": x["hazard"], "country": x["country"],
                "gdacs": x}
        p = ROOT / "pending" / f"candidate_gdacs_{ts}_{x['eventtype']}{x['eventid']}.json"
        p.write_text(json.dumps(cand, indent=2))
        written.append(p.name)
        print(f"\nCANDIDATE  {x['title']}\n  {x['description'][:200]}\n  {x['link']}\n"
              f"  written: pending/{p.name}")
    log({"ts": ts, "source": "gdacs", "outcome": "candidate" if new else "no_new_alert",
         "n_alerts": len(alerts), "n_hot": len(hot), "drought_skipped": 0 if a.include_drought else n_dr,
         "new": [x["title"] for x in new],
         "candidates": written})
    return 10 if new else 0


if __name__ == "__main__":
    sys.exit(main())
