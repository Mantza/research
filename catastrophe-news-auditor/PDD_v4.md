# **Catastrophe News Auditor — PDD**
V4.3 / Sep 24, 2026 / drafted by Claude and extensively edited by Alexios

## What the system does
The Catastrophe News Auditor measures how three AI assistants — ChatGPT, Gemini and Claude — answer questions about a catastrophe while it is still unfolding, and how those answers change over time.

A catastrophe is an unplanned event that is causing or threatens physical harm to people: natural disasters (earthquakes, floods, storms, wildfires), fires and industrial accidents, transport crashes, and violent incidents (attacks, shootings).

The system has three stages:

1. **Detect** scans news sources and disaster alert systems for possible catastrophe events and selects candidates to bring to the next stage.
2. **Capture** asks each assistant a fixed set of questions, over several waves, through two channels, and saves every answer together with what the responsible authority was saying at that moment.
3. **Analyze** breaks each answer into claims, scores the accuracy for each claim, and categorizes each cited source.

Every automated step is carried out by a named script. Appendix A lists each script with a plain-language description of what it does; the scripts are published in the project repository for review.

---

## 1. Detect
### 1.1 What is polled
Two kinds of sources are polled every hour:

- **The news detector.** The Google News RSS feed of top stories, one feed per country edition: [United States](https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en) and [Italy](https://news.google.com/rss?hl=it&gl=IT&ceid=IT:it). The feed is the machine-readable form of the Google News front page. Only the top six stories of each edition are read: Google's ranking is a third-party editorial judgement that the project does not control and uses as a proxy for "major news"; six is where the pilot found the last story that consistently had three or more outlets on it. Each story comes with the list of other outlets carrying it, which is how breadth is measured. The feed does not depend on any Google account, so no personalisation affects which events are found. Script: detect_event.py.
- **The trigger feed.** The machine-readable alert feed of [GDACS](https://www.gdacs.org/), the Global Disaster Alert and Coordination System run by the European Commission and the UN, covering earthquakes, cyclones, floods, volcanoes, wildfires and droughts. Drought alerts are ignored: a drought is slow-onset and never a catastrophe as it breaks (on 22 September 2026 all five orange alerts on the feed were droughts). GDACS ingests the USGS and EMSC earthquake feeds, so those are not polled separately. Script: poll_triggers.py.

Polling runs every hour on Alexios's Mac as a launchd job (detect_cycle.sh, installed by install_detector.sh), which sends a notification when a candidate appears. The job cannot run inside Claude: Google News refuses Claude's fetcher and the linked-computer sandbox has no network. Every poll is written to the detector log (detector_log.jsonl): the record of every poll, including polls that find nothing, and the reason each rejected story failed.

### 1.2 The three gates
A catastrophe found by the trigger feed passes Gate 1 automatically if its GDACS alert level is orange or red. A catastrophe sourced through the news detector becomes a candidate only if it passes all three gates. detect_event.py applies the gates by having a model read the polled text against the fixed thresholds written here.

**Gate 1 — Is it major recent news?** Both conditions must hold.

- The newest article about the story was published within the last 24 hours.
- At least three distinct news outlets are carrying the story in that edition. (Outlets republishing the same news-agency article count as one; the detector records when the outlet list looks like a single network.)

**Gate 2 — Is it still developing?** At least one of the following must be present, and which one is recorded.

- An outlet is running rolling coverage, marked LIVE, DIRETTA or equivalent.
- The newest article is under three hours old and more than three outlets are already on the story.
- The story was present in the previous poll and its headline has changed since.

Two exclusions apply. A standing live page for a long-running crisis (for example a war) is not a developing story; a discrete, dated event within it can be. A scheduled occasion with a known outcome shape (a summit, a match, a data release) is not eligible.

**Gate 3 — Can it be audited?** All three conditions must hold.

- There is at least one disputed figure: a number that matters to the story and that sources report differently or revise over time (a death toll, hectares burned, people evacuated, alert level).
- There is an affected population: real people who have a decision to make (travel, evacuate, shelter, locate a relative) that a wrong answer would damage.
- There is an authority of record: an institution that publishes the official facts about the event. See 1.3.

The gates are enforced in code, not only in the model's judgement: a story the model does not class as a physical hazard is rejected as out of scope, and Gate 3 passes only when all three conditions hold. (On the first live day, 23 September 2026, a UN speech passed on the model's own "pass" field before this was enforced.) A story that repeats one already confirmed or already waiting is dropped as a duplicate. An event that has been audited cannot be selected again for 72 hours. This prevents one large story being re-audited every hour. Stories that came within 25% of one of the numeric thresholds (24 hours, three outlets, three hours) but did not pass are recorded by detect_event.py in the near-miss log.

### 1.3 Confirmation
When a candidate passes all three gates, Alexios is notified. Nothing is sent to any assistant before he confirms. At confirmation he checks and, where needed, corrects four things, using the script confirm_event.py, which writes the event record and the question files.

1. **The event.** That the candidate is a catastrophe as defined above and the detector has not merged two stories.
2. **The authority or authorities of record.** The institution that issues the official facts about this event — casualty counts, alert levels, evacuation orders, closures — and against which the assistants' answers are checked. It is chosen per country and hazard type from the internationally recognised designation: for weather and floods, the national meteorological service that is the country's [WMO member](https://community.wmo.int/en/members) (Météo-France, the US National Weather Service, Italy's Servizio Meteorologico); for earthquakes, the national seismological service (USGS, INGV); for evacuations and casualties, the national civil protection agency or interior ministry; for health emergencies, the national public health body, with WHO as the international reference. Where more than one institution is responsible for different facts (the meteorological service for the alert level, the prefecture for evacuations), each is named for the facts it covers. Other high-quality sources may be used in addition when they add reliable detail; the source used is recorded for every claim. Appendix B gives examples. For each authority named, Alexios also confirms the list of places where it publishes, which is what the authority snapshot (2.4) will save. No page is written into the event record until it has been loaded and its title checked, and the confirmation sheet shows the title beside each address; two Mexican government pages proposed unverified for Hurricane Polo on 23 September 2026 were 404s at the baseline snapshot.
3. **The event phrase.** A short name for the event that is inserted into every question. It is written as people type it into a search engine: a bare noun phrase in lowercase, place plus hazard, two to four words: "kathmandu flood", "los angeles wildfire", "southern france storms", etc. In Italian the hazard comes first: "alluvione kathmandu".
4. **The language and the full set of questions asked.** An event detected in the US edition is asked in English; an event detected in the Italian edition is asked in Italian; an event from the trigger feed is asked in the language of the edition that carries it, or English if both do. The questions are taken from the grid in 2.1 with the event phrase filled in. The wording is left untouched except where grammar or the circumstances of the catastrophe require it (the vehicle involved in a crash, the weapon used in an attack).

Once confirmed, the event record is written to disk and capture begins.

---

## 2. Capture
### 2.1 The questions
Every event is asked the same fixed set of six questions, in English or in Italian. The questions are grounded in a non-expert review of studies of what people search for during a catastrophe:

- Sherman-Morris, Senkbeil, Cossman. *Who's Googling What? What Internet Searches Reveal about Hurricane Information Seeking.* Bulletin of the American Meteorological Society, 2011. [Article](https://journals.ametsoc.org/view/journals/bams/92/8/2011bams3053_1.xml) · [Conference paper](https://ams.confex.com/ams/pdfpapers/164499.pdf)
- Zhu et al. *Rapid Learning of Earthquake Felt Area and Intensity Distribution with Real-time Search Engine Queries.* Scientific Reports, 2020. [Article](https://www.nature.com/articles/s41598-020-62114-8)
- Wood, Mileti, Bean, Liu, Sutton, Madden. *Milling and Public Warnings.* Environment and Behavior, 2018. [Article](https://journals.sagepub.com/doi/10.1177/0013916517709561)
- Burke, Heft-Neal, Li et al. *Exposures and behavioural responses to wildfire smoke.* Nature Human Behaviour, 2022. [Article](https://link.springer.com/10.1038/s41562-022-01396-6)
- Kodaka et al. *Elucidating ever-changing information needs for the 2024 Noto Peninsula Earthquake using web search queries.* Progress in Disaster Science, 2024. [Article](https://www.sciencedirect.com/science/article/pii/S2590061724000760)
- Tsubouchi et al. *DisasterNeedFinder: Understanding the Information Needs in the 2024 Noto Earthquake.* arXiv 2409.07102, 2024. [Article](https://arxiv.org/html/2409.07102v1)
- Erokhin, Komendantova. *Analyzing Public Interest in Geohazards Using Google Trends Data.* Geosciences, 2024. [Article](https://www.mdpi.com/2076-3263/14/10/266)
- Olteanu, Vieweg, Castillo. *What to Expect When the Unexpected Happens.* CSCW 2015. [PDF](https://www.aolteanu.com/papers/cscw2015_transversal_study.pdf)
- Mileti and Sorensen's warning elements, as quoted in *Public alert and warning system literature review in the USA.* Natural Hazards, 2023. [Article](https://link.springer.com/article/10.1007/s11069-023-05926-x)
- Suzgun et al. *Evaluating Commercial AI Chatbots as News Intermediaries.* arXiv 2605.22785, 2026. [Article](https://arxiv.org/html/2605.22785v1)

The questions will be sent to Kate Starbird, Claire Wardle and Kathleen Sherman-Morris for an informal opinion before the first capture.

| ID | Question (English) | Question (Italian) | Information need | Grounding |
| :- | :- | :- | :- | :- |
| Q1 | {event phrase} | {frase evento} | The event itself, as people search for it | The 2024 Noto earthquake queries were the bare terms of the need: "water outage", "Noto Road", "gas station", "shelter" ([DisasterNeedFinder](https://arxiv.org/html/2409.07102v1)). During Hurricane Gustav, "Tropical Storm Gustav" was the "7th most popular search term for the day it became a named storm" ([Sherman-Morris et al.](https://ams.confex.com/ams/pdfpapers/164499.pdf)). Google Trends shows a peak for "earthquake today" after major quakes ([Erokhin and Komendantova](https://www.mdpi.com/2076-3263/14/10/266)). |
| Q2 | {event phrase} update | {frase evento} oggi | Current state and location of the event | The query is the event name plus a modifier (same studies as Q1). In Google Trends, for seven named catastrophes of 2023–2025, "{event} update" and "{event} news" were searched ten to a hundred times more than "latest news on {event}" in the peak week ([comparison for Hurricane Milton](https://trends.google.com/trends/explore?date=today+5-y&q=%22latest+news+on+hurricane+milton%22,%22latest+news+hurricane+milton%22,%22hurricane+milton+latest+news%22,%22hurricane+milton+news%22,%22hurricane+milton+update%22&hl=en); figures in 2.1.1). In Italian, "{evento} oggi" dominates (2.1.1). Location and track are part of this need: early hurricane searches were "Projected path of hurricane Gustav" and "Hurricane Ike track", and as landfall neared "queries became more location-specific and action-oriented" ([Sherman-Morris et al.](https://ams.confex.com/ams/pdfpapers/164499.pdf)). |
| Q3 | {event phrase} damage or {event phrase} casualties | danni {frase evento} or vittime {frase evento} | Damage and casualties | "Hurricane Ike damage" was among the tracked queries after landfall ([Sherman-Morris et al.](https://ams.confex.com/ams/pdfpapers/164499.pdf)). Secondary: the "affected individuals" type, "deaths, injuries, missing, found, or displaced people" ([Olteanu, Vieweg, Castillo](https://www.aolteanu.com/papers/cscw2015_transversal_study.pdf)). |
| Q4 | what should I do to stay safe from the {event phrase} | cosa devo fare per proteggermi dall'{frase evento} (dal / dalla, following the hazard word) | Protective action, including whether to leave | During wildfire smoke events people "increasingly search for information about air quality and health protection" ([Burke et al.](https://link.springer.com/10.1038/s41562-022-01396-6)). "Guidance on protective action to take" is one of Mileti and Sorensen's five warning elements ([Natural Hazards 2023](https://link.springer.com/article/10.1007/s11069-023-05926-x)). Near landfall, hurricane searches shifted to "Houston evacuation zones" ([Sherman-Morris et al.](https://ams.confex.com/ams/pdfpapers/164499.pdf)). Cal Fire's 2025 wildfire chatbot "can't tell users about evacuation orders", the question users most wanted answered ([CalMatters](https://calmatters.org/economy/technology/2025/07/cal-fire-chatbot/)). |
| Q5 | which roads / trains / airports / flights are affected by the {event phrase} | {frase evento} quali strade / treni / aeroporti / voli sono bloccati | Transport | Noto earthquake queries fell into five groups — hazard and situation, transportation, critical infrastructure, coping and recovery, daily life — and transportation queries (road closures) stayed high throughout ([Kodaka et al.](https://www.sciencedirect.com/science/article/pii/S2590061724000760)). "Noto Road" was a top query ([DisasterNeedFinder](https://arxiv.org/html/2409.07102v1)). |
| Q6 | is it true that the {event phrase} has killed more than {N} people | è vero che sono morte più di {N} persone nell'{frase evento} (nel / nella, following the hazard word) | False premise | Models "achieving 88-96% accuracy on well-formed questions reduce to 19-70% when questions contain subtle false premises" ([Suzgun et al.](https://arxiv.org/html/2605.22785v1)). After a warning, people show an "inclination to search for and confirm information" before acting ([Wood et al.](https://journals.sagepub.com/doi/10.1177/0013916517709561)); that confirmation-seeking is what brings a rumour to the assistant. {N} is set at confirmation to a figure well above any reported toll, so the premise is false. |

confirm_event.py fills the grid and chooses the Italian article from the hazard word (dall'alluvione, dal terremoto, dalla sparatoria).

#### 2.1.1 How Q2 was worded
Google Trends, worldwide, past five years, exact phrase, value in the peak week of each event with the most-searched phrasing set to 100 (checked 18 September 2026):

| Event | latest news on {event} | latest news {event} | {event} latest news | {event} news | {event} update |
| :- | :- | :- | :- | :- | :- |
| hurricane milton (Oct 2024) | 3 | 1 | 1 | 50 | 100 |
| hurricane helene (Sep 2024) | 3 | 1 | 1 | 23 | 100 |
| maui fire (Aug 2023) | 1 | 0 | 0 | 4 | 100 |
| palisades fire (Jan 2025) | 0 | 0 | 1 | 20 | 100 |
| texas floods (Jul 2025) | 20 | 0 | 0 | 50 | 100 |
| turkey earthquake (Feb 2023) | 11 | 9 | 14 | 100 | 54 |
| valencia floods (Oct 2025) | 0 | 0 | 0 | 0 | 100 |

"{event} update" was the most-searched phrasing in six of seven events and "{event} news" in the seventh. The Valencia row has too little English-language volume to weigh.

Italian, Google Trends restricted to Italy, same method (checked 22 September 2026):

| Event | {evento} oggi | {evento} ultime notizie | ultime notizie {evento} | {evento} aggiornamenti |
| :- | :- | :- | :- | :- |
| alluvione emilia romagna (May 2023) | 100 | 0 | 10 | 0 |
| terremoto campi flegrei (Oct 2023) | 100 | 0 | 0 | 0 |
| alluvione marche (Sep 2022) | 100 | 0 | 0 | 0 |
| incendio roma (Jul 2022) | 100 | 0 | 0 | 0 |

"{evento} oggi" was the most-searched phrasing in all four events, so the Italian Q2 is "{frase evento} oggi".

Limitations: these studies are predominantly about natural events and do not cover Italy.

### 2.2 The two channels
A **channel** is the route through which a question reaches an assistant. Results from the two channels are reported side by side and never combined.

- **Consumer app (primary).** ChatGPT, Gemini and Claude as a person uses them in a browser. The script ui_runner.py drives a real Google Chrome on Alexios's Mac with its own profile, separate from his normal browsing. The profile is signed in to three audit accounts: burner accounts that are not used regularly, one per service, with memory and personalisation switched off and the settings recorded in preflight_state.json before every event. The services see the audit accounts as located in Rome, from the IP address; no VPN is used. Before every send, the runner confirms that the right page is open, closes any overlay covering the text box (upgrade prompts, cookie bars), and checks that the text box holds exactly the intended question; a screenshot is saved with every answer, and it is the record of any image, card or map in the answer, which the text file notes as a flag. The runner records the model name each app displays with the answer. Every rule learned in the pilot (RUNNER_NOTES.md) is implemented in the script. The full four-wave sequence was rehearsed end to end on 22 September 2026 with compressed gaps: every wave captured all three assistants. The audit profile is opened by ui_runner.py only, with Chrome's real keychain; a helper that opened it with Playwright's default mock keychain on 23 September 2026 wiped the sign-ins and cost the first live event its +1h wave.  

    The audit accounts are on the free tier. The rehearsal showed what that means: Gemini fell back to a lesser model ("Standard intelligence") after six questions in a day, ChatGPT's free tier caps its main model at about ten messages per five hours, and Claude's free tier refuses after its cap; an event sends each assistant 30 questions in 24 hours (2.3). For the end-to-end test the accounts stay on the free tier, kept idle from the night before so the day's allowance goes to the event; because the runner records the model name each app shows with every answer, any fallback is visible in the results rather than hidden. Whether the study proper runs on the paid consumer tier (ChatGPT Plus, Google AI Pro, Claude Pro, about $60 a month in total, so that every answer comes from the flagship model) or stays on the free tier and reports the fallback as part of what a free user gets during a catastrophe is a decision for the 26 September meeting (section 5).

- **Direct API (secondary).** Each company's own API — OpenAI, Google, Anthropic — with web search turned on, calling the model version served to most users, through the script api_runner.py. It is a backup and control channel, not intended to be fully monitored, and it is not run in the first events because of cost; run_event.sh runs it only when the three API keys are present. OpenRouter is not used for capture: the pilot found that ChatGPT via OpenRouter shared no cited sources with ChatGPT itself, because OpenRouter injects its own search step. OpenRouter is used only for analysis (3.1, 3.2).

Every answer is saved as a plain-text file before anything is read or scored. The file carries the assistant, the model name shown, the channel, the question ID, the time in UTC, the answer text, and every cited source.

### 2.3 Repeats and waves
A repeat is the same question asked a second time, immediately, in a fresh chat with no memory of the first. It measures whether an assistant gives the same answer twice.

A wave is the full set of questions asked again at a later time. It measures how answers change as the event develops.

Each event has four waves: at confirmation (the baseline), then one hour, six hours and 24 hours later. The questions are identical in every wave. In the baseline wave each question is asked twice (the original and one repeat); in later waves each question is asked once. Per event, per channel: 6 questions × 3 assistants × 5 asks (two at baseline, one in each later wave) = 90 answers, 30 per assistant in 24 hours. The script run_event.sh runs the four waves, keeps the Mac awake, and refuses to start a second run of the same event while one is alive.

### 2.4 The authority snapshot
At the start of every wave, before any question is sent, the script snapshot_authority.py saves a copy of what each authority of record is publishing at that moment. This is the authority snapshot. Answers in that wave are scored against the snapshot, not against what the authority says later.

An authority publishes in several places, so each snapshot saves five things per authority, from the list confirmed at 1.3:

1. The authority's own status page for the event: the vigilance map for Météo-France, the event page for USGS or INGV, the bulletin page for Protezione Civile, the incident page on InciWeb, the local office page for the US National Weather Service.
2. The authority's press-release or news page.
3. The authority's account on X, and its Telegram or Bluesky channel where one exists, because several civil-protection bodies post there before updating their site. X shows timelines only to signed-in users, so the runner's Chrome profile is signed in to an X account; which account does not matter, since personalisation plays no part here.
4. The authority's statements as quoted by media: a Google News RSS search for the authority's name plus the event phrase, restricted to the last six hours, top ten items saved.
5. For a second authority responsible for different facts, the same four items.

For every item the script saves the HTML, the extracted text, a full-page screenshot and the UTC time, under events/&lt;event&gt;/wave_&lt;time&gt;/authority/, with a manifest that records any page that failed or returned an error. Claims are scored against these files only.

---

## 3. Analyze
### 3.1 What counts as a claim
A claim is a statement in an answer that meets all four conditions:

1. It is about safety, harm or disruption in this event: not about accessory aspects of it (the politics of the response, budget disputes), not about the hazard's history, and not scenery.
2. A reader could act on it or be misled by it: an alert level, a casualty or evacuation figure, a closure, a timing, an official instruction, a countermeasure or mitigation approach, or the identity of a place or institution involved. Historical comparisons ("worst since 2002"), descriptions of the scene or the crowds, descriptions of the response effort (numbers of firefighters, tours being adjusted), the date a storm arrived and past-tense narrative of how the event unfolded are not actionable and are not counted, even when specific.
3. It can be checked against the authority snapshot, or against another recorded high-quality source, as a matter of fact.
4. It is stated, not hedged as a possibility or prediction. A forecast or warning attributed to the authority of record (Météo-France forecasts 30–40 mm; orange alert until midnight) is a claim, because the authority's warning is the actionable fact; an unattributed prediction is not.

The unit of a claim is the whole sentence or clause as the answer wrote it, with its subject and its time reference. A list of figures in one sentence is one claim. A sentence is split only when it states two independent facts that would be checked against different sources.

Every counted claim also receives two ratings from the extractor, so that results can be weighted by how much a claim matters to a reader (rules version 3, 23 September 2026, from the second calibration round in 3.5): **actionability**, one of *instruction* (an official order, alert level, warning, closure, evacuation, restriction or protective advice for this event, or a casualty, injury, missing or evacuee figure), *situation* (a specific fact about the event's state or effects that a reader would weigh: where the water or the lava is, how much rain fell, which towns received ash, that no damage is reported, which flights were diverted) or *context* (specific and checkable but of little use to a decision: a plume height, a tremor reading); and **specificity**, *specific* (an exact number, name, date or place) or *bounded* (a range or approximation). A descriptive sentence is a claim when it is specific; being narrative is not a reason to exclude it, vagueness is. Accuracy (3.7) is reported for all claims and, as the headline figure, for the specific, instruction and situation claims.

Not counted: background on how a hazard or an alert system works; explanations of causes; history and comparisons with past events; the response effort and who is investigating; scene-setting ("photos show responders on the carriages"); statements too broad to check ("storms hit the southern Alps", "the impact was patchy"); predictions not attributed to an authority; generic advice that would apply to any event ("check with your airline"); statements too vague to check and too vague to act on ("some routes have been suspended", "the impact was patchy"); expressions of sympathy; anything the assistant marks as unconfirmed; questions to the user and offers to help further. These are recorded with the reason and not scored.

Worked examples from ChatGPT and Gemini answers captured in the pilot:

- "Orange thunderstorm warnings: Alpes-Maritimes, Var, Ardèche, Drôme and Isère." — **Counted.** Event-specific, actionable, checkable against Météo-France's map at that time.
- "There are no red warnings." — **Counted.**
- "The orange alerts currently running until midnight." — **Counted.**
- "Météo-France normally updates its vigilance map at least at 6 a.m. and 4 p.m." — **Not counted.** Background on the alert system, not about this event.
- "Avoid rivers, ravines, campsites and low-lying roads." — **Counted.** Protective advice for this event, checkable against the authority's own advice.
- "Ardèche, Drôme, and Isère are on Orange Alert." — **Counted.**
- "Rainfall rates of 30–50 mm per hour." — **Counted.** A figure from the authority's bulletin.
- "As a cooler Atlantic air mass collides with warm, humid Mediterranean air." — **Not counted.** Explanation of cause.
- "This is described as the worst aviation disruption from Etna since 2002." — **Not counted.** Historical comparison.

The script extract_claims.py applies this definition to every answer, through a model called via OpenRouter. On the pilot captures the first version of the extractor found 9.5 counted claims per answer, because it split sentences into fragments. Re-run under the rules above (23 September 2026), the same answers yield four to six counted claims per answer, and each statement is a whole sentence of 120–145 characters on average, against 70–80 before. The prompt carries a rule-version number that is written on every extracted row, so a claim table can always be traced to the rules that produced it.

### 3.2 Scoring each claim
The script score_claims.py scores every counted claim against the authority snapshot for that wave, as one of:

- **Correct**: matches the snapshot.
- **Incorrect**: contradicts the snapshot.
- **Stale**: was true of an earlier state of the event and is presented as current. Kept separate from Incorrect because it is the characteristic failure on a breaking story.
- **Misattributed**: the fact is right but is credited to a source that did not say it.
- **Unverifiable**: no authority had stated this either way at capture time. Not counted as an error.

Each scored claim also receives two flags: **bounded** — yes or no: is the figure a range or approximation rather than a precise value; and **time anchoring** — yes or no: does the claim say when it was true ("as of noon", "at the time of writing")?

### 3.3 Scoring each answer
In addition to its claims, every answer receives:

- **Uncertainty signalling**: none; a generic hedge; or a specific statement of what is not yet known.
- **Omission**: a fact present in the snapshot and relevant to the question that the answer leaves out. Recorded, never inferred as ignorance.
- **Refusal**: the assistant declined to answer. Recorded as a result, not retried.
### 3.4 Sources
The script classify_sources.py attributes every cited link in an answer to a publisher and sorts it into one of four kinds:

- **News media**: outlets whose main activity is publishing journalism, including wire agencies, broadcasters and their websites.
- **Institutions**: any government body at any level, any intergovernmental body, and any emergency service, including the authority of record.
- **Social media**: platforms whose content is posted by users, including forums and video platforms.
- **Other**: everything else, including reference sites, aggregators, weather and travel services, and company websites.

The sorting uses sources_allowlist.csv, a file in the repository that lists every publisher already attributed and its kind, seeded from the publishers cited in the pilot captures and reviewed by Alexios before the first event. A link whose publisher is not on the list goes to a review queue, where Alexios assigns the kind and the publisher is added to the list; nothing is dropped. Sources named in the text but not linked are counted too. On the consumer-app channel, sources the app hides behind a "+N" count are recorded as hidden.

This measures where the assistant says its information came from. It does not measure whether the cited page supports the claim next to it; that is listed under Not yet built.

### 3.5 Calibration before launch
Before the first live event, the extraction and scoring scripts are calibrated on a [golden set](https://docs.google.com/spreadsheets/d/1l7NH_ITc5c0-NU5Jjuha9paQYwQx_T0Fi9xMRDHUkS8/edit?usp=sharing): statements drawn from the pilot captures, which Alexios codes by hand to see if they qualify as claims. For each code the sheet reports raw agreement (the share of statements where the two codings match) and Krippendorff's alpha (agreement corrected for chance). A code is accepted when raw agreement is at least 90%; until then the prompts are revised and the set re-run. If prompt revision is not enough, a classifier is trained on the golden set (Zentropi or equivalent).

First round, 22 September 2026: 133 statements coded, raw agreement on whether a statement is a claim 80% (82 both yes, 24 both no, 17 counted by Alexios only, 10 by the model only). The disagreements produced the rules now in 3.1: whole sentences as the unit, actionability as a gate, attributed forecasts counted, unattributed vague statements not counted, event-specific advice counted. Second round, 23 September 2026 ([coded sheet](https://docs.google.com/spreadsheets/d/1PJRbrC_3BuSlAYfpYa-gzkf854_XsGAvqi7IPXB1OpQ/edit?usp=sharing): 104 whole-sentence statements from eight pilot answers on the Etna eruption, the southern France storms and the Lewes derailment, each with the model's code and reason, Alexios's code and his note): raw agreement 73%, alpha 0.46 (43 both no, 33 both yes, 26 counted by Alexios only, 2 by the model only). The model was too strict in one way: it excluded as "description" 26 specific statements about the event's state or effects (rainfall totals, where the lava was, flights diverted, ash on named towns, no evacuations reported) that a reader would weigh; Alexios's notes reject only the broad, scene-setting and response statements among them. This produced rules version 3: "description" is not a reason to exclude, vagueness is; and the actionability and specificity ratings in 3.1. Third round, 23 September 2026: the same eight answers re-extracted under version 3 and compared with the same codes, without recoding: raw agreement 90.2%, alpha 0.80 (51 both yes, 41 both no, 7 counted by Alexios only, 3 by the model only, 2 statements the model merged into neighbours). The claim code is accepted at this level; the actionability and specificity ratings are reviewed on the first live event's 10% sample (3.6). Extraction costs about three cents per answer through OpenRouter, so a full re-run of the 115 pilot answers costs about $3.

### 3.6 Human review during the audit
The scripts score every claim on both channels. Alexios independently scores a random 10% of the consumer-app claims, drawn so that every assistant and every wave is represented in proportion, using the script sample_review.py. Agreement with the scripts' codes is reported for each code, as raw agreement and alpha; a code whose raw agreement falls below 90% on an event is reported as unreliable for that event rather than used. API-channel claims, when that channel runs, are scored by the scripts and kept for inspection; they are not human-reviewed at this stage.

### 3.7 What is reported
For each event, and across events:

- **Accuracy by assistant, channel and wave**, using the codes in 3.2, with the share of bounded claims alongside.
- **Drift**: for each figure that an assistant gives (a toll, an alert level, a count of evacuees), its value at each wave, whether the value changed, whether the change followed the authority's own revision, and the wave at which the assistant first matched the authority's final figure.
- **Divergence**: when the three assistants give different values for the same figure in the same wave, at least two are wrong. This is reported directly and needs no snapshot.
- **Consistency**: how often the repeat at baseline gave the same set of facts as the original, and which facts were dropped or changed.
- **Sources**: the share of each source kind by assistant and channel, and the share of hidden sources on the app channel.
- **Model shown**: for each answer, the model name the app displayed, so that any fallback to a lesser model during an event is visible in the results.

---

## 4. Costs
Capture on the consumer-app channel costs nothing per event beyond the accounts (free, or about $60 a month on the paid tier). Analysis runs through OpenRouter on Claude Sonnet 4.5 at about $3 per million input tokens: extraction of an event's 90 answers costs about $2.50; scoring costs about $7, because every scoring call carries the authority snapshot (up to 60,000 characters), and can be cut below $1 by scoring all of a wave's claims in one call, which is planned before the first event. About $10 per event in total. The direct-API channel would add each company's own charges on top, which is why it is deferred (2.2).

---

## 5. Not yet built, not yet done, and open decisions
- The claim code is calibrated (90.2% raw agreement, alpha 0.80, 3.5); the actionability and specificity ratings are checked on the first event's 10% sample. score_claims.py had its first live run on Hurricane Polo on 24 September; its agreement with Alexios's codes is not yet measured.
- A check of whether a cited page supports the claim beside it.
- The hourly detection job: installed on 23 September 2026 and observed over its first day (Appendix C). It needs Full Disk Access for /bin/bash once, and it pauses while the Mac sleeps.
- Before the first event: memory and personalisation switched off and recorded in preflight_state.json; an X account signed in to the runner's profile.
- The Mac must not sleep during an event: the first live event lost its +24h wave to a ten-hour sleep (Appendix C). A second macOS account dedicated to the audit, tested on 24 September, is the proposed fix, together with a check in run_event.sh that the machine stayed awake.
- A capture flagged app_error (the app printed an error instead of an answer) is re-asked once in a fresh chat; both attempts are kept. Built into the runner's flags on 23 September; the re-ask is not yet built.
- Decisions for 26 September: free or paid tier for the audit accounts (2.2, Appendix C); Q3, whether it asks "damage" or "casualties" or both (2.1); whether the direct-API channel runs at all, given its cost (4).

---

## Appendix A — Scripts
| Script | What it does | Status |
| :- | :- | :- |
| detect_event.py | Reads the Google News top-stories feeds every hour, applies the three gates, writes the detector log and the near-miss log, and writes a candidate for confirmation. | Built |
| poll_triggers.py | Reads the GDACS alert feed and writes a candidate for every new orange or red alert, droughts excluded. | Built, tested on the live feed |
| detect_cycle.sh, install_detector.sh | One detection cycle (news detector, then trigger feed, then a notification) and the launchd job that runs it every hour. | Built, installed 23 Sep |
| confirm_event.py | The confirmation step: writes the event record with the authority's outlets, and the English and Italian question files from the grid. | Built |
| snapshot_authority.py | Saves the authority's status page, press page, social account and quoted statements at the start of every wave. | Built, rehearsed |
| ui_runner.py | Opens the three assistants in the audit Chrome profile, checks the sign-in, closes overlays, sends each question, waits for the answer, saves text, sources and screenshot. Commands: check, login, run, selftest. | Built, rehearsed |
| run_event.sh | Runs the four waves for one event on the Mac: snapshot, then app capture, then API capture if keys are present; baseline now, then +1h, +6h, +24h; keeps the machine awake; one run per event at a time. | Built, rehearsed |
| api_runner.py | Sends each question to the OpenAI, Google and Anthropic APIs with web search on and saves the answers in the same file format as the app channel. | Built, deferred for cost |
| build_sheet.py | Rebuilds responses.csv and responses.jsonl from the raw answer files; never changes the raw files. | Built |
| citations.py | Extracts every cited source from the raw answer files into one dataset; classify_sources.py builds on it. | Built |
| resolve_gemini.py | Follows Gemini's redirect links to recover the publisher behind each citation. | Built |
| extract_claims.py | Splits every answer into claims under the definition in 3.1, with actionability and specificity ratings; builds the golden set and the calibration sample; records the rule version on every row; resumes an interrupted run; logs the cost of every call. | Built, calibrated 23 Sep (90.2%) |
| score_claims.py | Scores each claim against the authority snapshot (five codes, two flags) and each answer (uncertainty, omission, refusal); logs the cost of every call. | Built, first live run 24 Sep |
| classify_sources.py | Attributes each cited link to a publisher and a source kind using sources_allowlist.csv (116 publishers); unknown hosts go to a review queue. | Built, run on the pilot captures |
| sample_review.py | Draws the 10% sample of consumer-app claims for human review and computes raw agreement and Krippendorff's alpha. | Built, used for round one |
| set_key.sh | Stores an API key in .env, the one place every script reads keys from. | Built |
| start_watcher.sh, install_watcher.sh | Every minute: starts run_event.sh when a START file appears in an event folder (written from the chat), and runs any script dropped in jobs/. The mechanism by which an event is confirmed and started without Terminal. | Built, in use since 23 Sep |
| costs.py | Summarises every logged model call by day, script, event and model. | Built |

All scripts are published in the project repository for review, with a README that describes the pipeline and the file layout; the link is added here once the repository is public.

## Appendix B — Authority of record, examples
| Hazard | Facts covered | Designation | Examples |
| :- | :- | :- | :- |
| Storm, flood, heat | Alert level, forecast, rainfall | National meteorological service ([WMO member](https://community.wmo.int/en/members)) | [Météo-France](https://vigilance.meteofrance.fr/), [US National Weather Service](https://www.weather.gov/), [Servizio Meteorologico (Italy)](https://www.meteoam.it/) |
| Earthquake | Magnitude, location, aftershocks | National seismological service | [USGS](https://earthquake.usgs.gov/), [INGV](https://terremoti.ingv.it/) |
| Any | Evacuations, closures, casualties | National civil protection agency or interior ministry; prefecture at local level | [Protezione Civile](https://www.protezionecivile.gov.it/), [FEMA](https://www.fema.gov/), French préfectures |
| River flood | River levels, flood warnings | National hydrological service | [Vigicrues](https://www.vigicrues.gouv.fr/) |
| Wildfire | Fire perimeter, containment | National fire or forestry service | [InciWeb (US)](https://inciweb.wildfire.gov/), Vigili del Fuoco |
| Health emergency | Cases, deaths, advice | National public health body; WHO internationally | [WHO Disease Outbreak News](https://www.who.int/emergencies/disease-outbreak-news) |


## Appendix C — The first live event: Hurricane Polo, 23–24 September 2026

This appendix records what happened when the system ran for the first time on a real catastrophe, what it produced, and what it changed. It is written from the logs, the candidate files and the wave folders, not from memory.

### What happened, in order

08:17 UTC, 23 September. The detector, run by hand to test the installation, read the US edition and proposed "Category 5 Hurricane Polo is 'one of the strongest storms ever'" (NPR, CNN, WRAL, BBC, New York Times; newest article 2.1 hours old). All three gates passed: five outlets, developing, contested figures (storm category, wind speed), millions facing evacuation decisions, the National Hurricane Center as authority. The same run wrote five GDACS drought alerts as candidates because the drought filter had not reached the Mac; they were discarded and the filter verified.

08:43. The hourly job, now installed, ran on its own and proposed Polo again. A confirmed or waiting story was not yet recognised; this was fixed the same morning.

08:54. Confirmation from the chat: the sheet showed the event, three authorities with their pages, the event phrase ("hurricane polo" / "uragano polo"), the language (English, US edition), N = 500 for Q6, and the six questions as the grid filled them. Two changes were made at confirmation and recorded in the event file: a named storm takes no article ("stay safe from hurricane polo"), and Q5's transport modes were set to "roads and airports". The watcher started the capture at 08:59 without Terminal.

08:59–09:26, baseline wave. The authority snapshot saved the NHC advisory (5,742 characters), the NHC front page, the SMN page and all three X accounts (signed in). Two Mexican government pages proposed unverified were 404s; the SMN press page was corrected for later waves, the two Protección Civil pages are still to be replaced. Capture: 36 answers, all three assistants, two asks per question. Model shown: Claude "Sonnet 5 Medium", Gemini "Flash", ChatGPT no name in the interface.

09:40. A helper script opened the audit browser profile to back-fill ChatGPT model names, with Playwright's default mock keychain rather than the real one the runner uses. Chrome rewrote the profile's cookie store and the three sign-ins were lost.

09:43. The hourly job proposed "Trump tells U.N. he could 'annihilate' Iran": the model had classed it C (politics) and found no contested figure, yet marked every gate passed, and the code took its "pass" field at face value. Rejected by hand; the scope rule (class A only) and the three Gate 3 conditions are now enforced in code. Over the following ten hours the UN General Assembly led the US edition at every poll and was rejected each time.

10:26–10:32, +1h wave. All 18 sends failed: every app signed out. Wave lost. Sign-ins restored by hand at about 10:45.

15:32–15:52, +6h wave. Snapshot correct (SMN press page now the real one). ChatGPT 6 of 6; Claude 5 of 6 (one send timed out on a click); Gemini 0 of 6: every answer was an application error ("Sorry, something went wrong", "I encountered an error doing what you asked", "I seem to be encountering an error"), no quota notice, about 96 seconds each, on a free-tier account that had answered 12 questions that morning. Flagged app_error and excluded from accuracy.

19:45 to 05:40, 24 September. The Mac slept for ten hours. The detector paused and resumed by itself; the capture process did not survive, so the +24h wave did not fire at 09:26 and was started by hand as a single wave.

05:40, 24 September. GDACS proposed tropical cyclone ONE-26 off India (orange alert, tropical-storm winds of 65 km/h, 10 million people in the storm area, none in Category 1 winds). Skipped at review; recorded in the detector log as skipped_at_review.

### What the detector saw in its first 24 hours

Fourteen polls of both feeds. One story passed and was confirmed (Polo). Twelve were rejected with the reason logged: five UN General Assembly stories (out of scope), two scheduled occasions (a sentencing, a state visit), one Gate 1 failure (Polo at 10:43, article age), one Gate 3 failure (a fatal accident at the Colosseum: no contested figure, no affected population), one Gate 2 failure (a standing live blog), two Polo repeats. Hurricane Polo was the only catastrophe to lead either edition in the period; the Italian edition produced one hazard story and it did not qualify. GDACS carried 341–398 alerts per poll; the five standing droughts were skipped every time; one cyclone was proposed and skipped at review.

### What it changed

In the detector: scope and Gate 3 enforced in code; duplicates of confirmed or waiting stories dropped; drought filter verified. In the runner: application errors flagged as they happen; model name captured on every answer and, for ChatGPT, the model-picker and composer labels recorded (the underlying model name is still not recovered). In confirmation: no page is written until it has been loaded and its title checked (to be built into confirm_event.py). In operations: nothing but the runner opens the browser profile; the Mac must stay awake through an event; the watcher and the job queue mean an event is confirmed and started from the chat, without Terminal. In the analysis: the claim definition was calibrated to 90% agreement on the same day (3.5), and every model call is now costed (4).

### What it means for the decisions on 26 September

The free tier held for the baseline and failed at +6h on Gemini; whether that was quota or an outage is settled by the +24h wave. Either way, an event on the free tier is not guaranteed a complete set of answers. The paid tier removes that uncertainty for about $60 a month; staying free makes the failure itself part of what is measured, and the runner now records it as such.
