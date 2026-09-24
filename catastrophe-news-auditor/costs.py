#!/usr/bin/env python3
"""
Catastrophe News Auditor -- costs.py
Summarises every model call the scripts have logged to costs.jsonl (one line per call, with OpenRouter's
own charge in USD), by day, by script and by event, and prints the running total.

  python3 costs.py            # summary
  python3 costs.py --csv      # also write costs_summary.csv

Fixed costs (subscriptions, credit top-ups) are kept by hand in COSTS.md next to this file.
"""
import argparse, collections, csv, json, pathlib

ROOT = pathlib.Path(__file__).parent
LOG = ROOT / "costs.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true")
    a = ap.parse_args()
    if not LOG.exists():
        print("no costs.jsonl yet (no model call has been logged)"); return
    rows = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    usd = lambda rs: sum(float(r.get("usd") or 0) for r in rs)
    tok = lambda rs: (sum(int(r.get("prompt_tokens") or 0) for r in rs), sum(int(r.get("completion_tokens") or 0) for r in rs))
    print(f"{len(rows)} model calls logged, total ${usd(rows):.2f}\n")
    for title, key in (("by day", lambda r: r["utc"][:10]), ("by script", lambda r: r["script"]),
                       ("by event", lambda r: r.get("event") or "-"), ("by model", lambda r: r.get("model") or "-")):
        g = collections.defaultdict(list)
        for r in rows:
            g[key(r)].append(r)
        print(title)
        for k in sorted(g):
            pt, ct = tok(g[k])
            print(f"  {k:<40} {len(g[k]):>5} calls  {pt:>9,} in  {ct:>8,} out  ${usd(g[k]):7.2f}")
        print()
    if a.csv:
        with (ROOT / "costs_summary.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["day", "script", "event", "model", "calls", "prompt_tokens", "completion_tokens", "usd"])
            g = collections.defaultdict(list)
            for r in rows:
                g[(r["utc"][:10], r["script"], r.get("event") or "-", r.get("model") or "-")].append(r)
            for k in sorted(g):
                pt, ct = tok(g[k]); w.writerow([*k, len(g[k]), pt, ct, f"{usd(g[k]):.4f}"])
        print("wrote costs_summary.csv")


if __name__ == "__main__":
    main()
