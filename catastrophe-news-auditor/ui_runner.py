#!/usr/bin/env python3
"""
ui_runner.py — consumer-app capture for the Breaking News Auditor.

Drives a real Chrome (persistent profile, logged in once by the operator) through
Playwright and asks ChatGPT, Gemini and Claude a fixed set of questions, saving every
answer as a raw text file plus a screenshot, before anything is read or scored.

Every rule below comes from RUNNER_NOTES.md (the 2026-07-28 and 2026-08-14 pilots):

  * programmatic insertion into the composer, never simulated typing
  * clear the composer, insert, then verify it holds EXACTLY the prompt before sending
  * claude.ai/chat/new only; abort if the path contains "cowork"
  * Claude and ChatGPT may generate in background tabs; Gemini must be foregrounded
  * settle-poll on the answer node, then bring the tab to front, re-read, screenshot
  * never accept a short or chips-only answer without the foregrounded re-read
  * validator flags are written as rows, never dropped
  * record the chat URL as soon as the send lands, so a crash loses nothing
  * raw file first, manifest second, analysis never (ordering rule)

Usage
  python3 ui_runner.py run --event <slug> --wave <label> --questions questions.json
                           [--providers chatgpt,gemini,claude] [--reps 2]
                           [--profile ~/bna-chrome-profile] [--out events]
  python3 ui_runner.py login   --profile ~/bna-chrome-profile     # opens the three sites; you log in; close the window
  python3 ui_runner.py check   --profile ~/bna-chrome-profile     # are we logged in? no sends
  python3 ui_runner.py selftest                                    # exercises the logic on local mock pages

questions.json: {"Q1": "kathmandu flood", "Q2": "latest news on kathmandu flood", ...}
(event phrase already inserted; the runner never edits question text).
"""
import argparse, datetime as dt, json, os, pathlib, re, sys, time

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("playwright not installed: pip3 install playwright --break-system-packages && python3 -m playwright install chromium")

# --------------------------------------------------------------------------------------
# Site definitions (selectors from RUNNER_NOTES §2–3; send buttons are best-effort with
# an Enter-key fallback). If a site changes, the pre-send check fails loudly.
# --------------------------------------------------------------------------------------
SITES = {
    "chatgpt": {
        "new_chat": "https://chatgpt.com/",
        "composer": ['div[contenteditable="true"]#prompt-textarea', 'div[contenteditable="true"]', 'textarea'],
        "send": ['button[data-testid="send-button"]', 'button[aria-label="Send prompt"]', 'button[aria-label*="Send"]'],
        "answer": ['[data-message-author-role="assistant"]'],
        "busy": ['button[data-testid="stop-button"]', 'button[aria-label*="Stop"]'],
        "background_ok": False,          # 2026-09-22 rehearsal: background send matched a hidden fallback textarea
        "chat_url_re": r"chatgpt\.com/c/",
        "logged_out": ['button[data-testid="login-button"]', 'a[href*="/auth/login"]', 'text=Sign up for free'],
        "insert": "fill",                # ChatGPT's editor ignores execCommand (2026-09-18 smoke test)
        "model_label": ['button[data-testid="model-switcher-dropdown-button"]', 'button[aria-label*="Model selector"]', 'button[aria-haspopup="menu"]:has-text("GPT")'],
    },
    "gemini": {
        "new_chat": "https://gemini.google.com/app",
        "composer": ['div.ql-editor[contenteditable="true"]', 'rich-textarea div[contenteditable="true"]', 'div[contenteditable="true"]'],
        "send": ['button[aria-label="Send message"]', 'button.send-button', 'button[aria-label*="Send"]'],
        "answer": ['message-content'],
        "busy": ['button[aria-label="Stop response"]', 'button[aria-label*="Stop"]'],
        "background_ok": False,          # RUNNER_NOTES §4: stalls in background tabs
        "chat_url_re": r"gemini\.google\.com/app/[0-9a-f]",
        "fade_in_s": 50,                 # RUNNER_NOTES §4: text present but unreadable ~50s
        "logged_out": ['a[href*="accounts.google.com/ServiceLogin"]', 'a[href*="accounts.google.com/AccountChooser"]', 'text=Sign in to save activity'],
        "model_label": ['bard-mode-switcher button', 'button[aria-label*="model"]', '[data-test-id="bard-mode-menu-button"]'],
    },
    "claude": {
        "new_chat": "https://claude.ai/chat/new",   # NOT /new — that opens Cowork (§1b)
        "composer": ['div[contenteditable="true"].ProseMirror', 'div[contenteditable="true"]', 'textarea'],
        "send": ['button[aria-label="Send message"]', 'button[aria-label*="Send"]'],
        "answer": ['.font-claude-response', '.font-claude-message'],
        "busy": ['button[aria-label="Stop response"]', 'button[aria-label*="Stop"]'],
        "background_ok": False,          # 2026-09-22 rehearsal: send did not land in a background tab
        "chat_url_re": r"claude\.ai/chat/[0-9a-f-]{20,}",
        "abort_if_path_contains": "cowork",
        "logged_out": ['text=Continue with Google', 'text=Continue with email'],
        "logged_out_url_re": r"claude\.ai/login",
        "dismiss": ['text=Not now', 'button[aria-label="Close"]', 'button:has-text("Maybe later")'],   # "Upgrade to Pro" modal (2026-09-22)
        "model_label": ['button[data-testid="model-selector-dropdown"]', 'button[aria-haspopup="menu"]:has-text("Sonnet")', 'button[aria-haspopup="menu"]:has-text("Opus")', 'button[aria-haspopup="menu"]:has-text("Haiku")'],
    },
}
# X is not an assistant; it is signed in so the authority snapshot can read timelines (design doc 2.4).
X_SITE = {"new_chat": "https://x.com/home", "logged_out": ['a[href="/login"]', 'a[href="/i/flow/login"]', 'text=Sign in'],
          "logged_out_url_re": r"x\.com/i/flow/login|x\.com/login|x\.com/\?", "abort_if_path_contains": None}

def model_shown(page, site):
    """The model name the app displays for this chat, or 'not exposed in UI'. Best effort, never raises."""
    for sel in site.get("model_label", []):
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                t = norm(loc.first.inner_text())
                if t:
                    return t[:80]
        except Exception:
            pass
    # ChatGPT (2026-09-23): the header button text can be empty; the composer shows a mode pill ("Thinking",
    # "Auto", "Instant") and the switcher carries the model in its aria-label. Gather every candidate string.
    try:
        found = page.evaluate("""() => {
          const out = new Set();
          const kw = /gpt|thinking|auto|instant|fast|pro\b|mini|sonnet|opus|haiku|flash/i;
          for (const el of document.querySelectorAll('button, [role=button], span, div[aria-label]')) {
            const a = (el.getAttribute('aria-label') || '').trim();
            const t = (el.innerText || '').trim();
            if (a && a.length < 80 && kw.test(a)) out.add('aria:' + a);
            if (t && t.length < 40 && !t.includes('\n') && kw.test(t) && el.children.length <= 2) out.add(t);
          }
          return Array.from(out).slice(0, 8);
        }""")
        if found:
            return " | ".join(found)[:200]
    except Exception:
        pass
    return "not exposed in UI"
def chatgpt_model_slug(page, chat_url):
    """The model that produced the last assistant message, as ChatGPT's own conversation record names it
    (metadata.model_slug), read in the signed-in session the way the app reads it. Never raises."""
    import re as _re
    m = _re.search(r"/c/(?:WEB:)?([0-9a-f-]{20,})", chat_url or "")   # 2026-09-23: ids appear as /c/WEB:<uuid>
    if not m:
        return "no chat id"
    try:
        out = page.evaluate("""async (cid) => {
          const s = await fetch('/api/auth/session', {credentials: 'include'});
          const tok = (await s.json()).accessToken;
          if (!tok) return 'no session token';
          let r = await fetch('/backend-api/conversation/' + cid, {headers: {Authorization: 'Bearer ' + tok}, credentials: 'include'});
          if (!r.ok) r = await fetch('/backend-api/conversation/WEB:' + cid, {headers: {Authorization: 'Bearer ' + tok}, credentials: 'include'});
          if (!r.ok) return 'conversation fetch HTTP ' + r.status;
          const j = await r.json();
          const msgs = Object.values(j.mapping || {}).map(n => n.message).filter(m => m && m.author && m.author.role === 'assistant');
          msgs.sort((a, b) => (a.create_time || 0) - (b.create_time || 0));
          const last = msgs[msgs.length - 1];
          if (!last) return 'no assistant message';
          const md = last.metadata || {};
          return [md.model_slug || '', md.default_model_slug ? 'default:' + md.default_model_slug : '', j.default_model_slug ? 'conv default:' + j.default_model_slug : ''].filter(Boolean).join(' | ') || 'no model_slug';
        }""", m.group(1))
        return str(out)[:120]
    except Exception as e:
        return f"unavailable ({type(e).__name__})"


GENERIC_DISMISS = ['button[aria-label="Close"]', 'button[aria-label="Dismiss"]', 'text=Not now', 'text=Maybe later',
                   'text=No thanks', 'button:has-text("Got it")']

SETTLE_S = 20          # answer text unchanged for this long => finished
MAX_WAIT_S = 240       # hard ceiling per answer
POLL_S = 5
SHORT_BODY = 300       # RUNNER_NOTES §6 validator threshold

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def utc_now():
    return dt.datetime.now(dt.timezone.utc)

def ts_label(t=None):
    return (t or utc_now()).strftime("%Y-%m-%dT%H%MZ")

def ts_iso(t=None):
    return (t or utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")

def first_locator(page, selectors, timeout_ms=20000):
    """Return the first selector that resolves to a visible element, or None."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    return sel
            except Exception:
                pass
        time.sleep(0.5)
    return None

def composer_text(page, sel):
    """Text currently held by the composer. Reads the selected element, and if that is
    empty, the focused editor (ChatGPT's editor reports its text there)."""
    return page.evaluate(
        """(sel) => { const read = (el) => !el ? '' : (el.tagName === 'TEXTAREA' ? el.value
                                                   : (el.innerText || el.textContent || ''));
                     let t = read(document.querySelector(sel));
                     if (!t.trim()) t = read(document.activeElement);
                     if (!t.trim()) { const all = document.querySelectorAll(sel);
                                      for (const e of all) { const x = read(e); if (x.trim()) { t = x; break; } } }
                     return t; }""", sel)

def norm(s):
    return (s or "").replace(" ", " ").strip()

def answer_text(page, selectors):
    """innerText of the LAST answer node (RUNNER_NOTES §3), plus link hrefs inside it."""
    for sel in selectors:
        got = page.evaluate(
            """(sel) => { const nodes = document.querySelectorAll(sel);
                          if (!nodes.length) return null;
                          const n = nodes[nodes.length - 1];
                          const links = Array.from(n.querySelectorAll('a[href]')).map(a => a.href);
                          return {text: n.innerText, links: links, count: nodes.length}; }""", sel)
        if got and got.get("text") is not None:
            return got
    return {"text": "", "links": [], "count": 0}

def is_busy(page, selectors):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                return True
        except Exception:
            pass
    return False

def strip_claude_preamble(text):
    """Claude's node includes a thinking/search preamble (§3). Keep from the first
    paragraph that is not a tool/search status line."""
    lines = text.splitlines()
    out, started = [], False
    for ln in lines:
        s = ln.strip()
        if not started and (not s or re.match(r"^(Searched|Searching|Thought|Thinking|Reasoned|Pondered|Fetched)\b", s)):
            continue
        started = True
        out.append(ln)
    return "\n".join(out).strip()

def validate(body, links):
    flags = []
    b = body.strip()
    if len(b) < SHORT_BODY:
        flags.append("short_body")
    prose = re.sub(r"\[[^\]]{1,80}\]|\+\d+", "", b)
    if len(b) > 0 and len(prose.strip()) < 0.3 * len(b):
        flags.append("chips_only")
    if re.search(r"\b(as (I )?mentioned|as noted above|above|earlier in (this|our) (chat|conversation))\b", b, re.I):
        flags.append("prior_turn_reference")
    if re.search(r"\b(I can't|I cannot|I'm unable|I am unable|not able to help)\b", b[:300], re.I):
        flags.append("possible_refusal")
    if re.search(r"\b(could you clarify|which (one|fire|storm|event) do you mean|do you mean)\b", b, re.I):
        flags.append("clarifying_question")
    # 2026-09-23: the app itself failed and printed an error in place of an answer (seen on Gemini during
    # hurricane-polo). Technical failure, not a refusal: flagged so it is excluded from accuracy and can be re-asked.
    if len(b) < 400 and re.search(r"(encounter(ed|ing) an error|something went wrong|hit a snag|try again later|an error occurred|couldn't (load|complete))", b, re.I):
        flags.append("app_error")
    return flags

def domains(links):
    out = []
    for u in links:
        m = re.match(r"https?://([^/]+)/?", u)
        if m:
            d = m.group(1).lower().removeprefix("www.")
            if d not in out:
                out.append(d)
    return out

# --------------------------------------------------------------------------------------
# core steps
# --------------------------------------------------------------------------------------
def profile_in_use(profile):
    """True if another Chrome has the audit profile open (Chrome keeps a SingletonLock symlink while running)."""
    d = os.path.expanduser(profile)
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        if os.path.lexists(os.path.join(d, name)):
            return True
    return False

def launch_ctx(p, profile):
    """Real Chrome, audit profile, no automation markers (login was throttled with them)."""
    if profile_in_use(profile):
        raise SystemExit(f"PROFILE IN USE: another run (or a Chrome window) has {profile} open. "
                         "Wait for it to finish or quit that Chrome window, then retry.")
    return p.chromium.launch_persistent_context(
        user_data_dir=os.path.expanduser(profile), channel="chrome", headless=False,
        viewport=None, no_viewport=True,
        ignore_default_args=["--enable-automation", "--use-mock-keychain"],   # real keychain: share cookies with plain Chrome
        args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"])

def logged_out(page, site):
    """True if the page shows a login control or a login URL (ChatGPT and Gemini
    accept anonymous use, so a visible composer does NOT prove a session)."""
    if site.get("logged_out_url_re") and re.search(site["logged_out_url_re"], page.url):
        return True
    for sel in site.get("logged_out", []):
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                return True
        except Exception:
            pass
    return False

class SendError(Exception):
    pass

def dismiss_overlays(page, site, rounds=2):
    """Close upgrade prompts, cookie bars and similar modals that cover the composer. Records what it closed."""
    closed = []
    for _ in range(rounds):
        hit = False
        for sel in site.get("dismiss", []) + GENERIC_DISMISS:
            try:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=3000)
                    closed.append(sel); hit = True
                    time.sleep(0.8)
            except Exception:
                pass
        if not hit:
            break
    if not closed:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    return closed

def open_fresh_chat(page, site):
    page.goto(site["new_chat"], wait_until="domcontentloaded", timeout=60000)
    page.bring_to_front()
    time.sleep(3)
    bad = site.get("abort_if_path_contains")
    if bad and bad in page.url:
        raise SendError(f"ABORT: landed on '{bad}' path: {page.url}")
    closed = dismiss_overlays(page, site)
    if closed:
        print(f"[{ts_iso()}] {site['new_chat']}: dismissed overlay via {closed}", flush=True)
    # wait until the same composer selector is visible on two checks 1.5 s apart (editors mount late;
    # ChatGPT shows a fallback textarea first, then replaces it)
    first_locator(page, site["composer"], timeout_ms=20000)
    time.sleep(1.5)

def send_prompt(page, site, prompt, foreground):
    if foreground or not site["background_ok"]:
        page.bring_to_front()
    sel = first_locator(page, site["composer"])
    if not sel:
        raise SendError("composer not found (not logged in, or selectors changed)")
    try:
        page.click(sel, timeout=8000)
    except Exception:
        dismiss_overlays(page, site)
        page.click(sel, timeout=15000)
    # clear, insert, verify (RUNNER_NOTES §1b)
    page.evaluate("""(sel) => { const el = document.querySelector(sel); el.focus();
                                document.execCommand('selectAll', false, null);
                                document.execCommand('delete', false, null); }""", sel)
    time.sleep(0.3)
    if site.get("insert") == "fill":
        try:
            page.locator(sel).first.fill(prompt, timeout=8000)
        except Exception:
            time.sleep(2)
            sel = first_locator(page, site["composer"]) or sel
            page.click(sel)
            page.locator(sel).first.fill(prompt, timeout=8000)
    else:
        page.evaluate("""([sel, txt]) => { const el = document.querySelector(sel); el.focus();
                                          document.execCommand('insertText', false, txt); }""", [sel, prompt])
    time.sleep(0.7)
    held = norm(composer_text(page, sel))
    if held != norm(prompt):
        # fallback 1: Playwright fill (works on most contenteditable editors)
        try:
            page.locator(sel).first.fill(prompt)
        except Exception:
            pass
        time.sleep(0.7)
        held = norm(composer_text(page, sel))
    if held != norm(prompt):
        # fallback 2: real keystrokes after a hard clear
        page.locator(sel).first.click()
        page.keyboard.press("Meta+A"); page.keyboard.press("Backspace")
        page.keyboard.type(prompt, delay=15)
        time.sleep(0.7)
        held = norm(composer_text(page, sel))
    if held != norm(prompt) and held.replace(" ", "") == (norm(prompt) * 2).replace(" ", ""):
        page.locator(sel).first.click()
        page.keyboard.press("Meta+A"); page.keyboard.press("Backspace")
        page.keyboard.type(prompt, delay=15)
        time.sleep(0.7)
        held = norm(composer_text(page, sel))
    if held != norm(prompt):
        raise SendError(f"composer mismatch before send: held={held[:80]!r} want={prompt[:80]!r}")
    url_before = page.url
    # click the send button; fall back to Enter
    btn = first_locator(page, site["send"], timeout_ms=3000)
    if btn:
        page.click(btn)
    else:
        page.keyboard.press("Enter")
    # verify the send landed: composer empty or URL changed (§2)
    for attempt in range(2):
        deadline = time.time() + 12
        while time.time() < deadline:
            if page.url != url_before or norm(composer_text(page, sel)) == "":
                return
            time.sleep(0.5)
        if attempt == 0:                      # fallback: Enter in the composer
            page.locator(sel).first.click()
            page.keyboard.press("Enter")
    raise SendError("send did not land (composer still holds text, URL unchanged)")

def wait_for_answer(page, site):
    """Settle-poll until the answer node text is stable and no stop button is visible."""
    start = time.time()
    last, stable_since = None, None
    while time.time() - start < MAX_WAIT_S:
        got = answer_text(page, site["answer"])
        txt = got["text"]
        busy = is_busy(page, site["busy"])
        if txt and txt == last and not busy:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= SETTLE_S:
                return True
        else:
            stable_since = None
        last = txt
        time.sleep(POLL_S)
    return False

def foreground_reread(page, site):
    """RUNNER_NOTES §1 mechanisms 3–4: the only trustworthy read is foregrounded, after
    Gemini's fade-in, confirmed by a screenshot."""
    page.bring_to_front()
    fade = site.get("fade_in_s", 0)
    if fade:
        deadline = time.time() + fade
        prev = None
        while time.time() < deadline:
            got = answer_text(page, site["answer"])
            if got["text"] and got["text"] == prev:
                break
            prev = got["text"]
            time.sleep(5)
    time.sleep(2)
    return answer_text(page, site["answer"])

# --------------------------------------------------------------------------------------
# raw file + manifest
# --------------------------------------------------------------------------------------
def write_raw(path, hdr, body):
    lines = [f"{k}: {v}" for k, v in hdr.items()]
    path.write_text("\n".join(lines) + "\n---RESPONSE---\n" + body.strip() + "\n---END---\n", encoding="utf-8")

def append_manifest(path, entry):
    data = {"captures": []}
    if path.exists():
        data = json.loads(path.read_text())
    data["captures"].append(entry)
    data["last_update_utc"] = ts_iso()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# --------------------------------------------------------------------------------------
# one wave
# --------------------------------------------------------------------------------------
def run_wave(args, mock=None):
    questions = json.loads(pathlib.Path(args.questions).read_text())
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    wave_dir = pathlib.Path(args.out) / args.event / args.wave
    raw_dir, shot_dir = wave_dir / "raw", wave_dir / "screenshots"
    raw_dir.mkdir(parents=True, exist_ok=True); shot_dir.mkdir(parents=True, exist_ok=True)
    manifest = wave_dir / "manifest.json"
    append_manifest(manifest, {"start_utc": ts_iso(), "event": args.event, "wave": args.wave,
                               "channel": "ui", "providers": providers, "reps": args.reps,
                               "questions": questions})

    with sync_playwright() as p:
        if mock:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            sites = mock
        else:
            ctx = launch_ctx(p, args.profile)
            sites = SITES
        pages = {prov: ctx.new_page() for prov in providers}

        for qid, prompt in questions.items():
            for rep in range(1, args.reps + 1):
                t0 = utc_now()
                state = {}
                # 1. fresh chats everywhere; 2. send to background-capable sites first, Gemini last, foregrounded
                order = sorted(providers, key=lambda pr: 0 if sites[pr]["background_ok"] else 1)
                for prov in order:
                    page, site = pages[prov], sites[prov]
                    st = {"prov": prov, "qid": qid, "rep": rep, "sent_utc": None, "chat_url": None, "error": None}
                    try:
                        open_fresh_chat(page, site)
                        if logged_out(page, site):
                            raise SendError("LOGGED OUT: run `ui_runner.py login` and sign in")
                        send_prompt(page, site, prompt, foreground=not site["background_ok"])
                        st["sent_utc"] = ts_iso()
                        time.sleep(1.5)
                        st["chat_url"] = page.url
                        append_manifest(manifest, {"sent": st})        # URL on disk immediately (§5)
                    except (SendError, PWTimeout) as e:
                        st["error"] = str(e)
                        append_manifest(manifest, {"send_error": st})
                        print(f"[{ts_iso()}] {prov} {qid} r{rep}: SEND FAILED: {str(e).splitlines()[0][:120]}", flush=True)
                    state[prov] = st
                # 3. one shared wait, then 4. foreground each and extract
                for prov in providers:
                    page, site, st = pages[prov], sites[prov], state[prov]
                    hdr = {"PROVIDER": prov, "MODEL_SHOWN": "not exposed in UI", "EVENT": args.event,
                           "QID": qid, "REP": rep, "CHANNEL": "ui", "WAVE": args.wave,
                           "CHAT_URL": st["chat_url"] or "", "TS_UTC": st["sent_utc"] or ts_iso(),
                           "PROMPT": prompt}
                    fname = f"{prov}_ui_{qid}_r{rep}.txt"
                    if st["error"]:
                        hdr.update({"ERROR": st["error"], "FLAGS": "send_failed", "CITATIONS_SHOWN": "CITATIONS_UNCAPTURED:", "SCREENSHOT": ""})
                        write_raw(raw_dir / fname, hdr, "")
                        append_manifest(manifest, {"capture": {**st, "file": fname, "flags": ["send_failed"]}})
                        continue
                    finished = wait_for_answer(page, site)
                    got = foreground_reread(page, site)
                    hdr["MODEL_SHOWN"] = model_shown(page, site)
                    if prov == "chatgpt":
                        hdr["MODEL_SLUG"] = chatgpt_model_slug(page, st["chat_url"] or page.url)
                    shot = shot_dir / f"{prov}_ui_{qid}_r{rep}.png"
                    try:
                        page.screenshot(path=str(shot), full_page=True)
                    except Exception:
                        shot = None
                    body = got["text"]
                    if prov == "claude":
                        body = strip_claude_preamble(body)
                    flags = validate(body, got["links"])
                    if not finished:
                        flags.append("no_settle_before_timeout")
                    if not shot:
                        flags.append("no_screenshot")
                    doms = domains(got["links"])
                    hdr.update({
                        "LATENCY_MS": int((utc_now() - t0).total_seconds() * 1000),
                        "CITATIONS_SHOWN": "; ".join(doms) if doms else "none",
                        "CITATION_LINKS": "; ".join(got["links"]),
                        "REFUSED": "yes" if "possible_refusal" in flags else "no",
                        "ASKED_CLARIFYING_Q": "yes" if "clarifying_question" in flags else "no",
                        "FLAGS": ",".join(flags),
                        "SCREENSHOT": shot.name if shot else "",
                        "CHAR_COUNT": len(body.strip()),
                    })
                    write_raw(raw_dir / fname, hdr, body)
                    append_manifest(manifest, {"capture": {**st, "file": fname, "flags": flags, "chars": len(body.strip())}})
                    print(f"[{ts_iso()}] {prov} {qid} r{rep}: {len(body.strip())} chars, flags={flags or '-'}")
        ctx.close()
    append_manifest(manifest, {"end_utc": ts_iso()})
    print(f"wave written to {wave_dir}")

# --------------------------------------------------------------------------------------
# login / check
# --------------------------------------------------------------------------------------
def cmd_login(args):
    with sync_playwright() as p:
        ctx = launch_ctx(p, args.profile)
        for prov, site in list(SITES.items()) + [("x", X_SITE)]:
            pg = ctx.new_page()
            try:
                pg.goto(site["new_chat"], wait_until="domcontentloaded", timeout=60000)
            except PWTimeout:
                print(f"{prov}: page still loading after 60s; continuing (sign in there anyway)")
        print("Sign in on all four tabs in the window that opened: ChatGPT, Gemini, Claude, and X (any X account).")
        print("The window stays open until you press Enter HERE in Terminal.")
        try:
            input("Press Enter when all four tabs show you signed in... ")
        except (EOFError, KeyboardInterrupt):
            pass
        try:
            ctx.close()
        except Exception:
            pass

def cmd_check(args):
    with sync_playwright() as p:
        ctx = launch_ctx(p, args.profile)
        ok = True
        for prov, site in SITES.items():
            page = ctx.new_page()
            try:
                open_fresh_chat(page, site)
                sel = first_locator(page, site["composer"], timeout_ms=25000)
                out = logged_out(page, site)
                status = "LOGGED OUT" if out else ("OK" if sel else "NOT FOUND (bot check? selectors?)")
                print(f"{prov:8s} url={page.url}  {status}")
                ok = ok and bool(sel) and not out
            except SendError as e:
                print(f"{prov:8s} {e}"); ok = False
        page = ctx.new_page()
        try:
            page.goto(X_SITE["new_chat"], wait_until="domcontentloaded", timeout=60000)
            time.sleep(4)
            out = logged_out(page, X_SITE)
            print(f"{'x':8s} url={page.url}  {'LOGGED OUT (authority snapshot cannot read X timelines)' if out else 'OK'}")
        except Exception as e:
            print(f"{'x':8s} {type(e).__name__}: {e}")
        ctx.close()
        sys.exit(0 if ok else 1)

# --------------------------------------------------------------------------------------
# self-test on local mock pages
# --------------------------------------------------------------------------------------
MOCK_HTML = """<!doctype html><html><body>
<div id="app">
 <div class="answers"></div>
 <div id="composer" contenteditable="true" style="border:1px solid #999;min-height:40px"></div>
 <button id="send" aria-label="Send message">Send</button>
</div>
<script>
 const wrap = %(wrap)s;   // extra outer tag for the answer node
 let n = 0;
 document.getElementById('send').onclick = () => {
   const c = document.getElementById('composer');
   const q = c.innerText; c.innerText = '';
   history.pushState({}, '', '/c/' + Math.random().toString(16).slice(2));
   const node = document.createElement(wrap);
   node.setAttribute('data-message-author-role','assistant');
   node.className = 'font-claude-response';
   document.querySelector('.answers').appendChild(node);
   let i = 0; const full = 'Answer to: ' + q + '. ' + 'Orange alert in five departements; no red alerts. '.repeat(8)
             + '<a href="https://vigilance.meteofrance.fr/x">meteofrance</a> <a href="https://apnews.com/y">AP</a>';
   const stop = document.createElement('button'); stop.setAttribute('aria-label','Stop response'); document.body.appendChild(stop);
   const iv = setInterval(() => { i += 40; node.innerHTML = full.slice(0, i); if (i >= full.length) { clearInterval(iv); stop.remove(); } }, 100);
 };
</script></body></html>"""

def cmd_selftest(args):
    import http.server, socketserver, threading, tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / "chatgpt.html").write_text(MOCK_HTML % {"wrap": "'div'"})
    (tmp / "gemini.html").write_text(MOCK_HTML % {"wrap": "'message-content'"})
    (tmp / "claude.html").write_text(MOCK_HTML % {"wrap": "'div'"})
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp), **k)
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    mock = {}
    for prov in ("chatgpt", "gemini", "claude"):
        s = dict(SITES[prov]); s["new_chat"] = f"http://127.0.0.1:{port}/{prov}.html"
        s["composer"] = ['#composer']; s["send"] = ['#send']; s.pop("abort_if_path_contains", None); s["fade_in_s"] = 0
        mock[prov] = s
    global SETTLE_S, MAX_WAIT_S, POLL_S
    SETTLE_S, MAX_WAIT_S, POLL_S = 3, 40, 1
    qfile = tmp / "q.json"; qfile.write_text(json.dumps({"Q1": "kathmandu flood", "Q2": "latest news on kathmandu flood"}))
    ns = argparse.Namespace(event="selftest-event", wave="wave_selftest", questions=str(qfile),
                            providers="chatgpt,gemini,claude", reps=1, out=str(tmp / "events"), profile="")
    run_wave(ns, mock=mock)
    raws = sorted((tmp / "events/selftest-event/wave_selftest/raw").glob("*.txt"))
    print(f"\nselftest: {len(raws)} raw files");
    for r in raws:
        t = r.read_text(); print("  ", r.name, "|", re.search(r"FLAGS: (.*)", t).group(1) or "-", "|", re.search(r"CITATIONS_SHOWN: (.*)", t).group(1))
    assert len(raws) == 6, "expected 6 raw files"
    assert all("send_failed" not in r.read_text() for r in raws), "a send failed in selftest"
    print("selftest OK")

# --------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--event", required=True); r.add_argument("--wave", default=None)
    r.add_argument("--questions", required=True)
    r.add_argument("--providers", default="chatgpt,gemini,claude")
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--profile", default="~/bna-chrome-profile")
    r.add_argument("--out", default="events")
    for name in ("login", "check"):
        s = sub.add_parser(name); s.add_argument("--profile", default="~/bna-chrome-profile")
    sub.add_parser("selftest")
    args = ap.parse_args()
    if args.cmd == "run":
        args.wave = args.wave or f"wave_{ts_label()}"
        run_wave(args)
    elif args.cmd == "login":
        cmd_login(args)
    elif args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "selftest":
        cmd_selftest(args)

if __name__ == "__main__":
    main()
