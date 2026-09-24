#!/bin/bash
# run_event.sh — run the four waves for one event: baseline now, then +1h, +6h, +24h.
# Each wave: authority snapshot first (design doc 2.4), then the consumer-app capture, then the API capture.
# Usage: ./run_event.sh <event-slug> <questions.json> [reps_at_baseline=2]
#        REHEARSAL=1 ./run_event.sh ...   -> waits 2 minutes between waves instead of 1h/5h/18h
# One run at a time: a lock file (events/<slug>/RUNNING) refuses a second start while the first is alive.
# The API channel (api_runner.py) runs only when OPENAI_API_KEY, GEMINI_API_KEY and ANTHROPIC_API_KEY
# are all in .env; otherwise it is skipped and logged as skipped (design doc 2.2: secondary, TBC).
# Keeps the Mac awake with caffeinate. Log: events/<slug>/run_event.log
set -u
EVENT="$1"; QFILE="$2"; REPS="${3:-2}"
DIR="$(cd "$(dirname "$0")" && pwd)"
EVDIR="$DIR/events/$EVENT"; LOG="$EVDIR/run_event.log"; LOCK="$EVDIR/RUNNING"
mkdir -p "$EVDIR"
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "ALREADY RUNNING for $EVENT (pid $(cat "$LOCK")). Not starting a second run."; exit 2
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT
stamp () { date -u +%FT%TZ; }
wave () {  # $1 = label suffix, $2 = reps
  W="wave_$(date -u +%Y-%m-%dT%H%MZ)_$1"
  echo "[$(stamp)] $W start" >> "$LOG"
  python3 "$DIR/snapshot_authority.py" --event "$EVENT" --wave "$W" --out "$DIR/events" >> "$LOG" 2>&1; rc=$?
  echo "[$(stamp)] $W snapshot done (exit $rc)" >> "$LOG"
  python3 "$DIR/ui_runner.py" run --event "$EVENT" --wave "$W" --questions "$QFILE" --reps "$2" --out "$DIR/events" >> "$LOG" 2>&1; rc=$?
  echo "[$(stamp)] $W app capture done (exit $rc)" >> "$LOG"
  if grep -q '^OPENAI_API_KEY=' "$DIR/.env" 2>/dev/null && grep -q '^GEMINI_API_KEY=' "$DIR/.env" && grep -q '^ANTHROPIC_API_KEY=' "$DIR/.env"; then
    python3 "$DIR/api_runner.py" --event "$EVENT" --wave "$W" --questions "$QFILE" --reps "$2" --out "$DIR/events" >> "$LOG" 2>&1; rc=$?
    echo "[$(stamp)] $W api capture done (exit $rc)" >> "$LOG"
  else
    echo "[$(stamp)] $W api capture skipped (keys not all present in .env)" >> "$LOG"
  fi
  echo "[$(stamp)] $W end" >> "$LOG"
}
if [ "${REHEARSAL:-0}" = "1" ]; then S1=120; S6=120; S24=120; echo "[$(stamp)] REHEARSAL: 2-minute gaps" >> "$LOG"; else S1=3600; S6=18000; S24=64800; fi
echo "[$(stamp)] run started, pid $$" >> "$LOG"
echo "started; watching the log: tail -f \"$LOG\""
caffeinate -d -i -w $$ &
# WAVE_ONLY=T24 ./run_event.sh <slug> <questions> 1  -> run just that wave now (e.g. after the Mac slept through it)
if [ -n "${WAVE_ONLY:-}" ]; then
  echo "[$(stamp)] single wave $WAVE_ONLY requested" >> "$LOG"
  wave "$WAVE_ONLY" "$REPS"
  echo "[$(stamp)] single wave $WAVE_ONLY complete" >> "$LOG"
  exit 0
fi
wave T0 "$REPS"
sleep $S1;  wave T1  1
sleep $S6;  wave T6  1
sleep $S24; wave T24 1
echo "[$(stamp)] event $EVENT complete" >> "$LOG"
