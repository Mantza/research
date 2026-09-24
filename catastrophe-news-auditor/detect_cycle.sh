#!/bin/bash
# detect_cycle.sh -- one detection cycle: news detector + trigger feed, then notify if a candidate appeared.
# Run by launchd every hour (install_detector.sh) or by hand:  ./detect_cycle.sh
# Log: detector_cycle.log next to this script. Notification: macOS banner, plus a push to your phone
# if NTFY_TOPIC=<topic> is set in .env (install the free ntfy app and subscribe to the same topic).
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
LOG="$DIR/detector_cycle.log"
echo "[$(date -u +%FT%TZ)] cycle start" >> "$LOG"

/usr/bin/env python3 "$DIR/detect_event.py" >> "$LOG" 2>&1; rc1=$?
/usr/bin/env python3 "$DIR/poll_triggers.py" >> "$LOG" 2>&1; rc2=$?

# Drop a new candidate that repeats a story already confirmed or already waiting (same event phrase or headline).
/usr/bin/env python3 - <<'PY' >> "$LOG" 2>&1
import json, glob, pathlib, os
pend = pathlib.Path("pending"); dup = pend / "duplicates"; dup.mkdir(exist_ok=True)
def key(d): return ((d.get("x_string") or d.get("headline") or "").strip().lower(), (d.get("headline") or "").strip().lower())
known = []
# events confirmed in the last 72 hours: their event phrase counts as a known story too
import time
for ej in glob.glob("events/*/event.json"):
    try:
        e = json.load(open(ej))
        if time.time() - os.path.getmtime(ej) < 72 * 3600 and e.get("event_phrase_en"):
            known.append((e["event_phrase_en"].strip().lower(), ""))
    except Exception: pass
def matches(k, j):
    return (k[0] and k[0] == j[0]) or (k[1] and k[1] == j[1]) or (j[0] and (j[0] in k[0] or j[0] in k[1]))
for f in sorted(glob.glob("pending/confirmed_*.json")) + sorted(glob.glob("pending/candidate_*.json")):
    try: d = json.load(open(f))
    except Exception: continue
    k = key(d)
    if any(matches(k, j) for j in known) and f.startswith("pending/candidate_"):
        os.replace(f, dup / pathlib.Path(f).name); print(f"duplicate of a known story, moved to pending/duplicates: {f}")
    else:
        known.append(k)
PY
N_NEW=$(ls "$DIR/pending"/candidate_*.json 2>/dev/null | wc -l | tr -d ' ')
if { [ "$rc1" -eq 10 ] || [ "$rc2" -eq 10 ]; } && [ "$N_NEW" -gt 0 ]; then
  N=$(ls "$DIR/pending"/candidate_*.json 2>/dev/null | wc -l | tr -d ' ')
  MSG="Breaking News Auditor: $N candidate(s) waiting in pending/. Review and confirm."
  echo "[$(date -u +%FT%TZ)] $MSG" >> "$LOG"
  /usr/bin/osascript -e "display notification \"$MSG\" with title \"Breaking News Auditor\"" 2>/dev/null
  TOPIC=$(grep '^NTFY_TOPIC=' "$DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"'"'")
  if [ -n "$TOPIC" ]; then
    curl -s -m 15 -H "Title: Breaking News Auditor" -H "Priority: high" -d "$MSG" "https://ntfy.sh/$TOPIC" >/dev/null 2>&1
  fi
fi
echo "[$(date -u +%FT%TZ)] cycle end (news rc=$rc1, gdacs rc=$rc2)" >> "$LOG"
