(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const LANG = { en: "English", es: "Spanish", pt: "Portuguese", tr: "Turkish", pl: "Polish", ar: "Arabic", hi: "Hindi", bn: "Bengali", zh: "Chinese", de: "German", fr: "French", it: "Italian", id: "Indonesian", tl: "Filipino" };
  const VERDICT = { TRUE: "True", FALSE: "False", MIXED: "Mixed", CANNOT: "Can't tell" };
  const VIEWS = {
    main: { label: "Open web vs. blocked", rows: [[1, "open", "Open web", "Nothing blocked"], [1, "blocked", "Fact-checkers blocked", "Up to 100 fact-checking sites blocked"]] },
    app: { label: "Consumer apps", rows: [[1, "app", "Consumer app", "Web app, default settings"]] },
    image: { label: "With image (Nov.)", rows: [[2, "img_open", "Open web", "Frame of the post attached"], [2, "img_blocked", "Fact-checkers blocked", "Same, sites blocked"], [2, "img_blocked_plus", "Republishers blocked too", "Plus sites that copied the fact checks"]] },
  };
  const SYS = [["claude", "Claude"], ["chatgpt", "ChatGPT"]];
  const REASON = { agrees: "reasoning matches", partly: "reasoning partly matches", disagrees: "reasoning doesn't match", no_engagement: "no reasoning given" };
  const MAIN = { main: "fact check is the main source", several: "fact check is one of several sources" };
  const LINKTYPE = { fc_roster: "Fact-checker", fc_other: "Other fact check", news: "News", institution: "Institution", social: "Social media", other: "Other" };

  const state = { show: "", lang: "", q: "", sel: null, view: "main" };
  let claims = [], byClaim = {}, changedSet = new Set();

  DOMPurify.addHook("afterSanitizeAttributes", (n) => { if (n.tagName === "A") { n.setAttribute("target", "_blank"); n.setAttribute("rel", "noopener noreferrer"); } });
  const md = (t) => DOMPurify.sanitize(marked.parse(String(t || ""), { breaks: true }));
  const names = (s) => String(s || "").replace(/\s*[|;]\s*/g, ", ").replace(/^, |, $/g, "");

  function verdictClass(a) {
    if (a.verdict === "CANNOT") return "c-ct";
    if (String(a.correct) === "1") return "c-ok";
    return a.verdict === "MIXED" ? "c-mx" : "c-bad";
  }
  function dotClass(a) {
    if (!a || a.verdict === "CANNOT") return "";
    if (String(a.correct) === "1") return "ok";
    return a.verdict === "MIXED" ? "mx" : "bad";
  }
  const find = (cid, w, arm, sys) => (byClaim[cid] || []).find((a) => a.wave === w && a.arm === arm && a.system === sys);
  const hasW2 = (cid) => (byClaim[cid] || []).some((a) => a.wave === 2);

  function summary(a) {
    const n = names(a.fc_named), parts = [];
    if (a.credit === "both") parts.push("Names and links " + (n || "a fact-checker"));
    else if (a.credit === "links") parts.push("Links " + (n || "a fact-checker"));
    else if (a.credit === "cites") parts.push("Names " + (n || "a fact-checker") + ", no link");
    else if (String(a.fc_followup_only) === "1") parts.push("Names " + (names(a.fc_followup) || "a fact-checker") + " only when asked");
    else parts.push("No fact-checker credited");
    if (MAIN[a.main_source]) parts.push(MAIN[a.main_source]);
    if (REASON[a.reasoning]) parts.push(REASON[a.reasoning]);
    return esc(parts.join(" · "));
  }

  function filtered() {
    const q = state.q.trim().toLowerCase();
    return claims.filter((c) =>
      (!state.show || (state.show === "changed" ? changedSet.has(c.claim) : c.type === state.show)) &&
      (!state.lang || c.lang === state.lang) &&
      (!q || (c.claim + " " + c.claim_text + " " + c.english + " " + c.source).toLowerCase().includes(q)));
  }

  function renderList() {
    const list = filtered();
    $("#count").textContent = list.length + (list.length === 1 ? " claim" : " claims");
    if (!list.find((c) => c.claim === state.sel)) state.sel = list.length ? list[0].claim : null;
    $("#claims").innerHTML = list.map((c) => {
      const dots = ["open", "blocked"].map((arm) => SYS.map(([s]) => `<i class="dot ${dotClass(find(c.claim, 1, arm, s))}"></i>`).join("")).join('<i class="gap"></i>');
      return `<li tabindex="0" data-c="${esc(c.claim)}" aria-selected="${c.claim === state.sel}">
        <div class="t">${esc(c.english || c.claim_text)}</div>
        <div class="m"><span>${esc(c.source)}${c.type === "media" ? " · image/video" : ""}</span><span class="dots" title="Open web, then blocked (Claude, ChatGPT)">${dots}</span></div></li>`;
    }).join("");
    renderMain();
    const s = document.querySelector('#claims li[aria-selected="true"]');
    if (s) { const ul = $("#claims"); ul.scrollTop = Math.max(0, s.offsetTop - ul.offsetTop - 60); }
  }

  function card(a, sysLabel) {
    if (!a) return `<div class="card empty" data-model="${sysLabel}">No answer recorded.</div>`;
    const id = a.id.replace(/[^a-zA-Z0-9]/g, "_");
    const n = (a.cites || []).length;
    const links = n ? `<ul class="links">${a.cites.map(([u, h, t]) => `<li><span class="k ${t === "fc_roster" || t === "fc_other" ? "fc" : ""}">${esc(LINKTYPE[t] || "Link")}</span><a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(h || u)}</a></li>`).join("")}</ul>` : `<div class="small">No links cited.</div>`;
    return `<div class="card" data-model="${sysLabel}">
      <div class="head"><span class="chip ${verdictClass(a)}">${VERDICT[a.verdict] || esc(a.verdict)}</span><span class="sum">${summary(a)}</span></div>
      <div class="ans clip" id="t-${id}">${md(a.turn1)}</div>
      ${a.turn1_en ? `<div class="ans clip" id="e-${id}" hidden>${md(a.turn1_en)}</div>` : ""}
      <div class="acts"><button data-x="${id}">Full answer</button>${a.turn1_en ? `<button data-tr="${id}">English</button>` : ""}<button data-p="l-${id}">Links (${n})</button>${a.turn2 ? `<button data-p="f-${id}">How do you know?</button>` : ""}</div>
      <div class="panel" id="l-${id}" hidden>${links}</div>
      ${a.turn2 ? `<div class="panel ans" id="f-${id}" hidden>${md(a.turn2)}</div>` : ""}
    </div>`;
  }

  function renderMain() {
    const m = $("#main");
    const c = claims.find((x) => x.claim === state.sel);
    if (!c) { m.innerHTML = `<p class="empty">No claims match these filters.</p>`; return; }
    if (state.view === "image" && !hasW2(c.claim)) state.view = "main";
    const views = Object.entries(VIEWS).filter(([k]) => k !== "image" || hasW2(c.claim));
    const tabs = `<div class="tabs">${views.map(([k, v]) => `<button data-v="${k}" aria-pressed="${state.view === k}">${v.label}</button>`).join("")}</div>`;
    let grid = `<div class="grid"><div></div>${SYS.map(([, l]) => `<div class="colh">${l}</div>`).join("")}`;
    for (const [w, arm, label, sub] of VIEWS[state.view].rows) {
      grid += `<div class="armh">${label}<small>${sub}</small></div>`;
      for (const [s, l] of SYS) grid += card(find(c.claim, w, arm, s), l + " · " + label);
    }
    grid += "</div>";
    const rating = (c.truth_basis || "").replace(/^[^:]*:\s*/, "");
    m.innerHTML = `<div class="claim-h"><div class="id">${esc(LANG[c.lang] || c.lang)}${c.type === "media" ? " · image/video claim" : ""}</div>
      <h2>${esc(c.claim_text)}</h2>${c.english ? `<div class="en">${esc(c.english)}</div>` : ""}
      <div class="fc"><a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.source)} fact check</a>, ${esc(c.date)}: rated “${esc(rating)}”</div></div>${tabs}${grid}`;
    history.replaceState(null, "", "#" + c.claim + (state.view !== "main" ? "/" + state.view : ""));
  }

  function bind() {
    $("#q").addEventListener("input", (e) => { state.q = e.target.value; renderList(); });
    $("#f-show").addEventListener("change", (e) => { state.show = e.target.value; renderList(); });
    $("#f-lang").addEventListener("change", (e) => { state.lang = e.target.value; renderList(); });
    const pick = (li) => { if (!li) return; state.sel = li.dataset.c; document.querySelectorAll("#claims li").forEach((x) => x.setAttribute("aria-selected", x === li)); renderMain(); if (window.innerWidth < 900) $("#main").scrollIntoView({ behavior: "smooth" }); };
    $("#claims").addEventListener("click", (e) => pick(e.target.closest("li")));
    $("#claims").addEventListener("keydown", (e) => { if (e.key === "Enter") pick(e.target.closest("li")); });
    $("#main").addEventListener("click", (e) => {
      const v = e.target.closest("[data-v]"); if (v) { state.view = v.dataset.v; renderMain(); return; }
      const x = e.target.closest("[data-x]");
      if (x) { const id = x.dataset.x; for (const p of ["t-", "e-"]) { const el = document.getElementById(p + id); if (el) el.classList.toggle("clip"); } x.textContent = x.textContent === "Full answer" ? "Shorter" : "Full answer"; return; }
      const t = e.target.closest("[data-tr]");
      if (t) { const id = t.dataset.tr; const o = document.getElementById("t-" + id), en = document.getElementById("e-" + id); const show = en.hidden; en.hidden = !show; o.hidden = show; t.textContent = show ? "Original" : "English"; return; }
      const p = e.target.closest("[data-p]");
      if (p) { const el = document.getElementById(p.dataset.p); el.hidden = !el.hidden; p.classList.toggle("on", !el.hidden); }
    });
  }

  fetch("data/data.json").then((r) => r.json()).then((d) => {
    claims = d.claims;
    for (const a of d.answers) (byClaim[a.claim] = byClaim[a.claim] || []).push(a);
    for (const c of claims) for (const [s] of SYS) {
      const o = find(c.claim, 1, "open", s), b = find(c.claim, 1, "blocked", s);
      if (o && b && (o.verdict !== b.verdict || String(o.correct) !== String(b.correct))) changedSet.add(c.claim);
    }
    const langs = [...new Set(claims.map((c) => c.lang))].sort((a, b) => (LANG[a] || a).localeCompare(LANG[b] || b));
    $("#f-lang").insertAdjacentHTML("beforeend", langs.map((l) => `<option value="${esc(l)}">${esc(LANG[l] || l)}</option>`).join(""));
    const [cid, view] = decodeURIComponent(location.hash.slice(1)).split("/");
    if (claims.find((c) => c.claim === cid)) { state.sel = cid; if (VIEWS[view]) state.view = view; }
    bind();
    renderList();
  }).catch(() => { $("#main").innerHTML = `<p class="empty">Could not load the data.</p>`; });
})();
