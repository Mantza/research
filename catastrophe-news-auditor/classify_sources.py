#!/usr/bin/env python3
"""
Breaking News Auditor -- classify_sources.py   (design doc 3.4)
Attributes every cited link in every saved answer to a publisher and one of four kinds:
news media, institutions, social media, other. Uses sources_allowlist.csv; anything not on
the list goes to a review queue and is never dropped.

  python3 classify_sources.py --event kathmandu-flood-2026-09-22        # one event, all waves
  python3 classify_sources.py --all                                     # every event
  python3 classify_sources.py --all --queue                             # print the review queue only

Reads   events/<slug>/wave_*/raw/*.txt  (CITATION_LINKS / CITATIONS_SHOWN lines; never modified)
        sources_allowlist.csv           (host, publisher, kind, ...)
Writes  events/<slug>/wave_*/sources.csv   one row per cited link: file, provider, qid, rep, host, publisher, kind, hidden
        sources_review_queue.csv           hosts not on the allowlist, with counts and an example URL
"""
import argparse, csv, glob, pathlib, re, sys, urllib.parse, collections

ROOT = pathlib.Path(__file__).parent
ALLOW = ROOT / "sources_allowlist.csv"
GEMINI_REDIRECT = "vertexaisearch.cloud.google.com"


def load_allowlist():
    al = {}
    with ALLOW.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            al[r["host"].strip().lower()] = r
    return al


def host_of(url):
    h = urllib.parse.urlparse(url).netloc.lower()
    return re.sub(r"^(www|m|amp)\.", "", h)


def lookup(host, al):
    """Exact host, then parent domains (news.bbc.co.uk -> bbc.co.uk)."""
    parts = host.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in al:
            return al[cand]
    return None


def parse_header(path):
    h = {}
    for line in path.read_text(encoding="utf-8", errors="replace").split("---RESPONSE---")[0].splitlines():
        if ":" in line:
            k, v = line.split(":", 1); h[k.strip()] = v.strip()
    return h


def classify_event(slug, al, queue):
    out_rows = 0
    for wave in sorted((ROOT / "events" / slug).glob("wave_*")):
        rows = []
        for f in sorted((wave / "raw").glob("*.txt")):
            h = parse_header(f)
            links = re.findall(r"https?://[^\s;]+", h.get("CITATION_LINKS", "") + " " + h.get("CITATIONS_SHOWN", ""))
            titles = [t.strip() for t in h.get("CITATION_TITLES", "").split(";")] if h.get("CITATION_TITLES") else []
            hidden = 0
            m = re.search(r"\+\s?(\d+)", h.get("CITATIONS_SHOWN", ""))
            if m:
                hidden = int(m.group(1))
            seen = set()
            for i, u in enumerate(links):
                if u in seen:
                    continue
                seen.add(u)
                host = host_of(u)
                title = titles[i] if i < len(titles) else ""
                if host == GEMINI_REDIRECT:
                    # publisher is in the title Gemini returned, not the URL
                    host = title.lower().strip() if title else host
                rec = lookup(host, al)
                if rec and rec.get("kind"):
                    pub, kind = rec["publisher"], rec["kind"]
                else:
                    pub, kind = "", "unresolved"
                    queue[host]["n"] += 1; queue[host].setdefault("example", u)
                rows.append({"file": f.name, "provider": h.get("PROVIDER", ""), "channel": h.get("CHANNEL", ""),
                             "qid": h.get("QID", ""), "rep": h.get("REP", ""), "url": u, "host": host,
                             "title": title, "publisher": pub, "kind": kind, "hidden_count": hidden})
            if not links and hidden:
                rows.append({"file": f.name, "provider": h.get("PROVIDER", ""), "channel": h.get("CHANNEL", ""),
                             "qid": h.get("QID", ""), "rep": h.get("REP", ""), "url": "", "host": "", "title": "",
                             "publisher": "", "kind": "hidden", "hidden_count": hidden})
        if rows:
            with (wave / "sources.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
            out_rows += len(rows)
            kinds = collections.Counter(r["kind"] for r in rows)
            print(f"{slug}/{wave.name}: {len(rows)} citations  {dict(kinds)}")
    return out_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--queue", action="store_true", help="print the review queue only")
    a = ap.parse_args()
    al = load_allowlist()
    slugs = a.event or ([p.name for p in sorted((ROOT / "events").iterdir()) if p.is_dir()] if a.all else [])
    if not slugs:
        sys.exit("give --event <slug> or --all")
    queue = collections.defaultdict(lambda: {"n": 0})
    total = 0
    for s in slugs:
        total += classify_event(s, al, queue)
    with (ROOT / "sources_review_queue.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["host", "count", "example_url", "publisher", "kind"])
        for host, d in sorted(queue.items(), key=lambda kv: -kv[1]["n"]):
            w.writerow([host, d["n"], d.get("example", ""), "", ""])
    print(f"\n{total} citations classified; {len(queue)} hosts in sources_review_queue.csv "
          f"(fill publisher and kind, append the rows to sources_allowlist.csv, re-run)")
    if a.queue:
        for host, d in sorted(queue.items(), key=lambda kv: -kv[1]["n"]):
            print(f"  {d['n']:>3}  {host}  {d.get('example','')[:80]}")


if __name__ == "__main__":
    main()
