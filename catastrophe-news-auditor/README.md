# Catastrophe News Auditor

Measures how ChatGPT, Gemini and Claude answer questions about a catastrophe while it is unfolding: which claims they make, whether those claims match what the responsible authority was publishing at that moment, and which sources they cite. Built by Alexios Mantzarlis ([Indicator](https://indicator.media)) as a GAIN fellowship project with Nick Diakopoulos and Jeremy Gilbert.

The design document (V4) is the reference for every definition used here. This file says what the code does and how to run it.

## The pipeline

Three stages, each a set of scripts in this folder. Every stage reads and writes plain files under `events/<slug>/`; nothing is stored anywhere else.

**Detect.** `detect_cycle.sh` runs once an hour (installed as a macOS launchd job by `install_detector.sh`). It calls `detect_event.py`, which reads the Google News top-stories feeds for the US and Italian editions and applies the gates in the design document, and `poll_triggers.py`, which reads the GDACS disaster-alert feed (orange and red alerts; droughts excluded). A candidate that passes is written to `pending/candidate_*.json` and announced with a macOS notification (and a phone push if `NTFY_TOPIC` is set in `.env`). Every check is logged to `detector_log.jsonl`, including near misses, so the week's denominator is on disk.

**Confirm.** `confirm_event.py` turns a candidate into an event: `events/<slug>/event.json` (event phrase, authorities of record with their status page, press page and social account, language), and the six question files from the fixed grid, English and Italian.

**Capture.** `run_event.sh <slug> <questions.json> <reps>` runs the four waves (baseline, +1 h, +6 h, +24 h). At the start of each wave `snapshot_authority.py` saves what the authorities are publishing (`events/<slug>/<wave>/authority/`); then `ui_runner.py` asks each question in a real Chrome signed in to dedicated audit accounts on the three apps, saving one text file per answer under `raw/` plus a screenshot. `api_runner.py` (the direct-API channel) exists but is not run by default; see the design document. `REHEARSAL=1 ./run_event.sh ...` compresses the wave gaps to two minutes.

**Analyze.** `extract_claims.py` splits each answer into whole-sentence statements and marks which are claims under the written definition (`claims.csv` per wave). `score_claims.py` grades each claim against that wave's authority snapshot with five codes (correct, incorrect, stale, misattributed, unverifiable) and two flags (bounded, time-anchored), and each answer for uncertainty signalling, omission and refusal (`scores.csv`, `answer_scores.csv`). `classify_sources.py` attributes every cited link to a publisher and one of four kinds using `sources_allowlist.csv`; unknown hosts go to `sources_review_queue.csv`, never dropped. `sample_review.py sample` draws the 10% human-review sample; `sample_review.py agree` computes raw agreement and Krippendorff's alpha between the model's codes and a human's on any sheet with `M_*` and `A_*` columns.

## Setup (macOS)

```
pip3 install -r requirements.txt --break-system-packages
python3 -m playwright install chromium      # used only by `ui_runner.py selftest`
./set_key.sh OPENROUTER_API_KEY             # analysis model; prompts for the key, writes .env
./install_detector.sh                       # hourly detection job
python3 ui_runner.py login                  # sign the audit accounts in, once
python3 ui_runner.py check                  # OK on all three apps before every event
```

`ui_runner.py` drives the installed Google Chrome with its own profile at `~/bna-chrome-profile`, separate from the user's own browser. The macOS privacy system blocks launchd jobs from the Desktop folder by default; grant Full Disk Access to `/bin/bash` once (System Settings, Privacy & Security) or the detector job exits with code 126.

Keys live only in `.env` (git-ignored): `OPENROUTER_API_KEY` for extraction and scoring; `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` only if the direct-API channel is switched on; `NTFY_TOPIC` optional.

## Running one event

```
python3 confirm_event.py --candidate pending/candidate_<ts>.json   # or by hand: see the docstring
./run_event.sh <slug> questions_<slug>.json 2                    # 24 hours; Mac awake and unlocked
python3 extract_claims.py --event <slug>
python3 score_claims.py --event <slug>
python3 classify_sources.py --event <slug>
python3 sample_review.py sample --event <slug>                   # -> events/<slug>/review_sample.xlsx
python3 sample_review.py agree --file events/<slug>/review_sample.xlsx
```

Every model-calling script has `--dry-run`. `extract_claims.py --resume` reuses claim tables already extracted under the current rule version and continues a run that was interrupted.

## Calibration

`extract_claims.py --golden --resume --sample 100 --min-no 25` re-extracts the pilot captures under the current rules into `golden_set.csv` and draws `golden_sample.xlsx` for human coding. Round one (22 Sep 2026, 133 statements): 80% raw agreement, alpha 0.50, which produced the whole-sentence rule and four others now in the prompt. The rule version is recorded on every extracted row (`rules` column).

## Layout

```
events/<slug>/event.json                     the event record
events/<slug>/questions_*.json               the six questions, with the event phrase filled in
events/<slug>/<wave>/authority/              authority snapshot: text, page images, news headlines, manifest
events/<slug>/<wave>/raw/*.txt               one answer per file: header (provider, model shown, question,
                                             timestamps, citations, flags) then the answer text
events/<slug>/<wave>/claims.csv              statements and claims
events/<slug>/<wave>/scores.csv              claim scores; answer_scores.csv for answer-level codes
events/<slug>/<wave>/sources.csv             cited links, publisher, kind
sources_allowlist.csv                        publisher -> kind lookup
golden_set.csv, golden_sample.xlsx           calibration set
```

Screenshots and saved authority pages are not committed (size); the text of both is.

## Documents

`PDD_v4.md` is the design document as of 22 Sep 2026 (the Google Doc is the version of record). `PIPELINE_v8.png` is the diagram. `HANDOVER.md` is the running log of decisions and gotchas; `DEFINITION.md`, `WORKFLOW.md`, `CITATIONS.md`, `RUNNER_NOTES.md` and `MEMO_2026-08-20.md` document the pilot phase (July–August 2026) and are superseded where they disagree with the design document. `README_ui_runner.md` covers the capture runner in detail.

## Pilot-phase scripts

`openrouter_runner.py`, `openai_direct.py`, `citations.py`, `resolve_gemini.py` and `build_sheet.py` produced the pilot events (`france-fires`, `spain-fires`, `etna-eruption`, `lewes-derailment`, `france-storms`, `us-canada-tariff`, `scotus-immigration`) through the API channels. They are kept for reproducibility and are not part of the current pipeline. `wiki_stream_demo.py` is a detection experiment (Wikipedia edit stream), not used.
