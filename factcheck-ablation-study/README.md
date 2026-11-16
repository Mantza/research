# Chatbots and fact checks: the answers

Static site (no build step) showing every answer from the ablation experiment.

- `index.html`, `style.css`, `app.js`: the page.
- `data/data.json`: the 40 fact-checked claims and their 312 coded answers (240 wave 1, 72 wave 2), built 16 Nov 2026 from the review dashboard's live data (your review calls applied). Fact-check texts are not included; claims link to them.
- `js/`: marked 12.0.2 and DOMPurify 3.1.6, vendored with their licences.

To preview locally: `python3 -m http.server` in this folder, then open http://localhost:8000.
To publish: push this folder to a GitHub repo and turn on Pages (Settings > Pages > Deploy from branch, root).
