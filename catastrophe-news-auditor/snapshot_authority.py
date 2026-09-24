#!/usr/bin/env python3
"""
Breaking News Auditor -- snapshot_authority.py   (design doc 2.4)
At the start of a wave, before any question is sent, saves what each authority of record is
publishing at that moment. Claims in that wave are scored against these files only.

  python3 snapshot_authority.py --event kathmandu-flood-2026-09-22 --wave wave_2026-09-22T1000Z_T0
  python3 snapshot_authority.py --event kathmandu-flood-2026-09-22 --dry-run     # list what would be saved

Reads   events/<slug>/event.json  -> "authorities": [{name, status_page, press_page, social, news_search}]
Writes  events/<slug>/<wave>/authority/<n>_<kind>.html  .txt  .png   (page HTML, visible text, full-page screenshot)
        events/<slug>/<wave>/authority/<n>_news.xml  .json             (Google News RSS search, last 6 hours, top 10)
        events/<slug>/<wave>/authority/snapshot_manifest.json          (every URL, UTC time, outcome)
Uses the same audit Chrome profile as ui_runner.py (needed for X timelines). Do not run it while
ui_runner.py has the profile open; run_event.sh runs it first, then the wave.
"""
import argparse, json, pathlib, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))
KINDS = ["status_page", "press_page", "social"]
NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
PAGE_WAIT_S = 8


def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_news(query, n=10):
    url = NEWS_RSS.format(q=urllib.parse.quote(f"{query} when:6h"))
    req = urllib.request.Request(url, headers={"User-Agent": "IndicatorBreakingNewsAuditor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        xml = r.read().decode("utf-8", "replace")
    items = []
    for block in xml.split("<item>")[1:n + 1]:
        t = re.search(r"<title>(.*?)</title>", block, re.S)
        l = re.search(r"<link>(.*?)</link>", block, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
        s = re.search(r"<source[^>]*>(.*?)</source>", block, re.S)
        items.append({"title": (t.group(1) if t else "").strip(), "link": (l.group(1) if l else "").strip(),
                      "published": (d.group(1) if d else "").strip(), "source": (s.group(1) if s else "").strip()})
    return url, xml, items


def save_page(page, url, base):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(PAGE_WAIT_S)
    try:
        page.mouse.wheel(0, 2000); time.sleep(2); page.mouse.wheel(0, -2000)
    except Exception:
        pass
    html = page.content()
    text = page.evaluate("document.body ? document.body.innerText : ''")
    pathlib.Path(str(base) + ".html").write_text(html, encoding="utf-8")
    pathlib.Path(str(base) + ".txt").write_text(text, encoding="utf-8")
    page.screenshot(path=str(base) + ".png", full_page=True)
    return len(text), page.url, page.title()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--wave", default=None, help="wave folder name; default wave_<UTC>_snap")
    ap.add_argument("--out", default="events")
    ap.add_argument("--profile", default="~/bna-chrome-profile")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    ev_path = ROOT / a.out / a.event / "event.json"
    ev = json.loads(ev_path.read_text())
    auths = ev.get("authorities") or []
    if not auths:
        sys.exit(f"{ev_path} has no 'authorities' list -- add it (confirm_event.py writes it)")
    wave = a.wave or f"wave_{datetime.now(timezone.utc):%Y-%m-%dT%H%MZ}_snap"
    out = ROOT / a.out / a.event / wave / "authority"

    plan = []
    for i, au in enumerate(auths, 1):
        for kind in KINDS:
            if au.get(kind):
                plan.append((i, au["name"], kind, au[kind]))
        plan.append((i, au["name"], "news", au.get("news_search") or f"{au['name']} {ev.get('event_phrase_en','')}"))
    print(f"{a.event} / {wave}: {len(plan)} items")
    for i, name, kind, target in plan:
        print(f"  {i}_{kind:<12} {name}: {target}")
    if a.dry_run:
        return

    out.mkdir(parents=True, exist_ok=True)
    manifest = {"event": a.event, "wave": wave, "started_utc": utc(), "items": []}
    from playwright.sync_api import sync_playwright
    from ui_runner import launch_ctx
    with sync_playwright() as p:
        ctx = launch_ctx(p, a.profile)
        page = ctx.new_page()
        for i, name, kind, target in plan:
            base = out / f"{i}_{kind}"
            rec = {"authority": name, "kind": kind, "target": target, "ts_utc": utc()}
            try:
                if kind == "news":
                    url, xml, items = fetch_news(target)
                    (out / f"{i}_news.xml").write_text(xml, encoding="utf-8")
                    (out / f"{i}_news.json").write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
                    rec.update(url=url, n_items=len(items), outcome="ok")
                    print(f"  {i}_news: {len(items)} items")
                else:
                    n, final_url, title = save_page(page, target, base)
                    rec.update(final_url=final_url, title=title, text_chars=n, outcome="ok" if n > 200 else "short")
                    print(f"  {i}_{kind}: {n} chars  {title[:60]}")
            except Exception as e:
                rec.update(outcome="error", error=f"{type(e).__name__}: {e}")
                print(f"  {i}_{kind}: ERROR {type(e).__name__}: {e}")
            manifest["items"].append(rec)
        ctx.close()
    manifest["finished_utc"] = utc()
    (out / "snapshot_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"snapshot written to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
