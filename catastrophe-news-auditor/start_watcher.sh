#!/bin/bash
# start_watcher.sh -- one pass: start a capture for any event folder that has a START file.
# Run every minute by launchd (install_watcher.sh). The START file is written by confirm_event.py --start,
# or by hand:  echo "questions_<slug>.json 2" > events/<slug>/START    (questions file, reps at baseline)
# A START file is renamed to STARTED_<utc> once the run is launched, so it fires once.
DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/watcher.log"
for f in "$DIR"/events/*/START; do
  [ -f "$f" ] || continue
  EVDIR="$(dirname "$f")"; SLUG="$(basename "$EVDIR")"
  read -r QFILE REPS WONLY < "$f"; REPS="${REPS:-2}"     # optional third field: a single wave label, e.g. T24
  [ -f "$DIR/$QFILE" ] || { echo "[$(date -u +%FT%TZ)] $SLUG: questions file '$QFILE' not found; START left in place" >> "$LOG"; continue; }
  if [ -f "$EVDIR/RUNNING" ] && kill -0 "$(cat "$EVDIR/RUNNING" 2>/dev/null)" 2>/dev/null; then
    echo "[$(date -u +%FT%TZ)] $SLUG: already running; START left in place" >> "$LOG"; continue
  fi
  mv "$f" "$EVDIR/STARTED_$(date -u +%Y-%m-%dT%H%MZ)"
  echo "[$(date -u +%FT%TZ)] $SLUG: launching run_event.sh $SLUG $QFILE $REPS" >> "$LOG"
  ( cd "$DIR" && WAVE_ONLY="${WONLY:-}" nohup /bin/bash "$DIR/run_event.sh" "$SLUG" "$QFILE" "$REPS" >> "$LOG" 2>&1 & )
  /usr/bin/osascript -e "display notification \"Capture started for $SLUG\" with title \"Catastrophe News Auditor\"" 2>/dev/null
  TOPIC=$(grep '^NTFY_TOPIC=' "$DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"'"'")
  [ -n "$TOPIC" ] && curl -s -m 15 -H "Title: Catastrophe News Auditor" -d "Capture started for $SLUG" "https://ntfy.sh/$TOPIC" >/dev/null 2>&1
done
# Second duty: run any script dropped into jobs/ (written from Claude through the link), once, in the background.
# jobs/<name>.job -> moved to jobs/done/<name>_<utc>.job when launched; output in jobs/<name>.log
mkdir -p "$DIR/jobs/done"
for j in "$DIR"/jobs/*.job; do
  [ -f "$j" ] || continue
  NAME="$(basename "$j" .job)"; TS="$(date -u +%Y-%m-%dT%H%MZ)"
  mv "$j" "$DIR/jobs/done/${NAME}_$TS.job"
  echo "[$(date -u +%FT%TZ)] job $NAME launched (jobs/done/${NAME}_$TS.job) -> jobs/$NAME.log" >> "$LOG"
  ( cd "$DIR" && echo "[$(date -u +%FT%TZ)] job $NAME start" >> "$DIR/jobs/$NAME.log" && nohup /bin/bash "$DIR/jobs/done/${NAME}_$TS.job" >> "$DIR/jobs/$NAME.log" 2>&1 && echo "[$(date -u +%FT%TZ)] job $NAME end (exit 0)" >> "$DIR/jobs/$NAME.log" || echo "[$(date -u +%FT%TZ)] job $NAME end (exit $?)" >> "$DIR/jobs/$NAME.log" ) &
done
exit 0
