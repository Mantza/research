#!/usr/bin/env python3
"""
Breaking News Auditor -- api_runner.py   (design doc 2.2, direct API channel, secondary)
Sends the event's questions to each company's own API with web search on, and saves every
answer in the same raw file format as the consumer-app runner, with CHANNEL: api.
Nothing is injected: no system prompt, no search instruction, no citation instruction.

  python3 api_runner.py --event kathmandu-flood-2026-09-22 --questions questions_kathmandu-flood-2026-09-22.json --reps 2
  python3 api_runner.py --event smoketest --questions questions_smoketest.json --providers openai --dry-run

Keys (environment or .env next to this file):
  OPENAI_API_KEY      https://platform.openai.com/api-keys
  GEMINI_API_KEY      https://aistudio.google.com/apikey
  ANTHROPIC_API_KEY   https://console.anthropic.com/settings/keys
Writes  events/<slug>/<wave>/raw/<provider>_api_<QID>_r<rep>.txt  and  manifest_api.json
Models: MODELS below -- the version served to most consumer users; verify against each company's
        model page before an event and change here, never per run.
"""
import argparse, json, os, pathlib, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent
MODELS = {"openai": "gpt-5.6-sol",          # same as openai_direct.py (pilot)
          "google": "gemini-2.5-flash",      # VERIFY: the model behind the free Gemini app
          "anthropic": "claude-sonnet-4-5"}  # VERIFY: the model behind the free Claude app
PROVIDER_LABEL = {"openai": "chatgpt", "google": "gemini", "anthropic": "claude"}
ANTHROPIC_VERSION = "2023-06-01"


def env_key(name):
    k = os.environ.get(name)
    if k:
        return k
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def post(url, body, headers, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ---- one function per provider: returns (text, [ (url, title) ], model_used, searches) ----

def call_openai(prompt, key, model):
    d = post("https://api.openai.com/v1/responses",
             {"model": model, "input": prompt, "tools": [{"type": "web_search"}]},
             {"Authorization": f"Bearer {key}"})
    text, cites, searches = "", [], 0
    for item in d.get("output", []):
        if item.get("type") in ("web_search_call", "web_search_preview_call"):
            searches += 1
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")
                    for an in c.get("annotations", []):
                        if an.get("type") == "url_citation":
                            cites.append((an.get("url", ""), an.get("title", "")))
    return text, cites, d.get("model", model), searches


def call_google(prompt, key, model):
    d = post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
             {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]},
             {"x-goog-api-key": key})
    cand = (d.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    gm = cand.get("groundingMetadata", {}) or {}
    cites = [(c.get("web", {}).get("uri", ""), c.get("web", {}).get("title", ""))
             for c in gm.get("groundingChunks", [])]
    searches = len(gm.get("webSearchQueries", []) or [])
    return text, cites, d.get("modelVersion", model), searches


def call_anthropic(prompt, key, model):
    d = post("https://api.anthropic.com/v1/messages",
             {"model": model, "max_tokens": 4096,
              "messages": [{"role": "user", "content": prompt}],
              "tools": [{"type": "web_search_20250305", "name": "web_search"}]},
             {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION})
    text, cites, searches = "", [], 0
    for b in d.get("content", []):
        if b.get("type") == "text":
            text += b.get("text", "")
            for c in b.get("citations", []) or []:
                if c.get("url"):
                    cites.append((c["url"], c.get("title", "")))
        elif b.get("type") == "server_tool_use":
            searches += 1
    return text, cites, d.get("model", model), searches


CALLS = {"openai": call_openai, "google": call_google, "anthropic": call_anthropic}
KEYS = {"openai": "OPENAI_API_KEY", "google": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def capture(provider, key, qid, rep, prompt, event, wave, out_dir):
    label, model = PROVIDER_LABEL[provider], MODELS[provider]
    t0 = time.monotonic()
    text, cites, used, searches, err = "", [], model, 0, ""
    try:
        text, cites, used, searches = CALLS[provider](prompt, key, model)
        if not text:
            err = "empty_body"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        try:
            err += " | " + e.read().decode()[:300]  # HTTPError body
        except Exception:
            pass
    latency = int((time.monotonic() - t0) * 1000)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen, links, titles = set(), [], []
    for u, t in cites:
        if u and u not in seen:
            seen.add(u); links.append(u); titles.append(t or "")
    p = out_dir / f"{label}_api_{qid}_r{rep}.txt"
    p.write_text(
        f"PROVIDER: {label}\nMODEL_SHOWN: {used}\nMODEL_REQUESTED: {model}\nEVENT: {event}\n"
        f"QID: {qid}\nREP: {rep}\nCHANNEL: api\nAPI_ROUTE: {provider}-direct\n"
        f"SEARCHES_PERFORMED: {searches}\nSEARCH_PROMPT_INJECTED: none\n"
        f"TS_UTC: {ts}\nLATENCY_MS: {latency}\n"
        f"CITATIONS_SHOWN: {'; '.join(links) or 'none'}\n"
        f"CITATION_TITLES: {'; '.join(titles)}\n"
        f"REFUSED: \nERROR: {err}\nPROMPT: {prompt}\n---RESPONSE---\n{text}\n---END---\n",
        encoding="utf-8")
    print(f"  {label:8} {qid} r{rep}: {len(text):>6} chars  {len(links):>2} cites  "
          f"{searches} searches  {latency:>6}ms  {err[:80]}", flush=True)
    return {"provider": label, "qid": qid, "rep": rep, "chars": len(text), "cites": len(links),
            "searches": searches, "latency_ms": latency, "error": err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--questions", required=True, help='JSON {"Q1": "...", ...}')
    ap.add_argument("--wave", default=None)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--providers", default="openai,google,anthropic")
    ap.add_argument("--out", default="events")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    grid = json.loads(pathlib.Path(a.questions).read_text())
    provs = [p.strip() for p in a.providers.split(",") if p.strip()]
    wave = a.wave or f"wave_{datetime.now(timezone.utc):%Y-%m-%dT%H%MZ}_api"
    out_dir = ROOT / a.out / a.event / wave / "raw"
    print(f"event {a.event} | wave {wave} | channel api | providers {provs}")
    for k, v in grid.items():
        print(f"  {k}: {v}")
    keys = {}
    for p in provs:
        if p not in CALLS:
            sys.exit(f"unknown provider {p}")
        keys[p] = env_key(KEYS[p])
        print(f"  {p}: model {MODELS[p]}, key {'found' if keys[p] else 'MISSING (' + KEYS[p] + ')'}")
    if a.dry_run:
        print("[dry run] no calls made"); return
    missing = [p for p in provs if not keys[p]]
    if missing:
        sys.exit(f"missing keys for {missing} -- add them to .env")

    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(p, keys[p], qid, rep, prompt, a.event, wave, out_dir)
            for rep in range(1, a.reps + 1) for p in provs for qid, prompt in grid.items()]
    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        results = list(ex.map(lambda j: capture(*j), jobs))
    ok = sum(1 for r in results if not r["error"])
    (out_dir.parent / "manifest_api.json").write_text(json.dumps({
        "event": a.event, "wave": wave, "channel": "api", "models": {p: MODELS[p] for p in provs},
        "reps": a.reps, "attempted": len(results), "succeeded": ok,
        "end_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Secondary channel. Never pooled with the consumer-app channel.",
        "results": results}, indent=2))
    print(f"\n{ok}/{len(results)} ok -> {out_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
