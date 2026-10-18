# BatchWatch

**Is this medicine recalled?** BatchWatch watches the recall and falsified-medicine alerts of 10 drug regulators, reads every new alert into batch-level records with **NVIDIA Nemotron on Nebius Token Factory**, checks its own reading, and tells a pharmacist in seconds whether a pack, or any line of a stock list, is named in an alert, with what to do and the regulator's own words.

A batch recalled in one country is often still on shelves in another. In October 2025 a contaminated cough syrup batch (Coldrif, batch SR-13) killed children in India, and pharmacies were asked to check their shelves by hand. In August 2026 Nigeria's NAFDAC relayed a recall that Pakistan's DRAP had issued four months earlier and asked its staff to search every state for four batches. BatchWatch answers that question for one pack or a whole stock list in seconds, and its agent shows where else a batch has been flagged.

> Prototype for the Nebius × NVIDIA Global AI Hackathon, track **Best Apps and Agents**, with runtime use of **Tavily**. Built during the submission period.

## What it does

| | |
|---|---|
| **One pack** | Scan the GS1 DataMatrix on the box (camera or photo, decoded on the device), or type the batch. The answer is a plain verdict: *Stop dispensing*, *Confirm product*, *Follow the notice*, *Review* or *No alert found*, with what to do, the regulator's instruction, a link to the notice, a copyable quarantine note, and a Yes/No question when the pharmacist must confirm the product. |
| **Stock list** | Upload a CSV or Excel export as the pharmacy system produces it: English or Arabic column names, UTF-8 or Arabic Windows encoding. Unfamiliar columns are identified by Nemotron 3 Nano. The report lists every affected line with the evidence and downloads as CSV with audit columns. A list can be kept on the device and is re-checked automatically when new alerts arrive. |
| **Alerts** | Every alert with its products and batch numbers, the model that read it, the six checks it passed, the regulator's text with each extracted batch number highlighted, and where else it was flagged. |
| **Smart update** | Every 30 minutes each regulator is asked what is newest. Only alerts BatchWatch has not seen are downloaded and read. New alerts go out on an Atom feed and, when configured, to Telegram, email and a webhook. |
| **Cross-border trace** | An agent on Nemotron 3 Super chooses its own Tavily searches, reads regulator pages and reports where else a product or batch was flagged. A finding is marked verified only if its quote and batch numbers are on the page it read. The steps stream to the page as the agent works. |
| **English and Arabic** | Full right-to-left Arabic interface using the SFDA's own terms. |

## Measured results

**Extraction** (evaluation run U3, 9 Oct 2026, re-scored on 11 Oct with the current cleanup and checks): 90 real alerts from 10 regulators (15 each from FDA, MHRA, NAFDAC and SFDA; 5 each from Health Canada, DRAP, SAHPRA, Rwanda FDA, FDA Ghana and WHO), frozen in `eval/alerts_snapshot.jsonl`, with all 340 batch numbers labelled by hand in `eval/gold_batches.json`.

| Setup | Alerts fully correct | Batch numbers found | Invented | Batch on the right product | Cost for 90 alerts | Median time per alert |
|---|---|---|---|---|---|---|
| **BatchWatch: Nemotron 3 Nano + six checks + Nemotron 3 Super re-read** | **90 / 90** | **340 / 340** | **0** | **91 / 91** | **$0.076** | not recorded |
| Nemotron 3 Ultra, no re-read | 90 / 90 | 340 / 340 | 0 | 91 / 91 | $0.590 | 5.7 s |
| Nemotron 3 Nano, no re-read | 88 / 90 | 327 / 340 | 0 | 78 / 78 | $0.058 | 15.3 s |
| First unguarded run (first 45 alerts, free-text JSON) | 43 / 45 | 142 / 181 | | | | |

The checked Nano pipeline matches Nemotron 3 Ultra's accuracy at about an eighth of Ultra's cost; the checks sent 9 of the 90 alerts to Super for a re-read. Without the re-read, Nano gets 88/90 and misses 13 batch numbers (all in one 13-batch defect notice). All rows apply the same deterministic cleanup and checks; only the re-read differs. Nano's one invented "batch" in the original U3 scoring was an Arabic phrase from an SFDA notice (مرفق بالتعميم, "attached to the circular"); a rule added since rejects Arabic text in a batch field, so it now scores 0 invented. Every check was added because an evaluation run caught a real failure. Results per run: `eval/results_*.json`.

Hard cases in the set: an alert withdrawn as issued in error; a safety communication and an import rule that must not flag any stock; "all lots up to 05/2029"; a lot number that looks like a date; Saudi registration numbers printed next to batch numbers in a flattened Arabic/English table; batch lists that live in attachments the model must not invent; batch numbers as short as `008`; a counterfeit named to look like a brand ("Ulmicort"); an alert of four strengths with 26 batches.

**Matching**: `examples/inventory_sample.csv` (a hospital store, 25 lines) and `examples/riyadh_pharmacy.csv` (a Riyadh pharmacy, 16 lines, Arabic column names, real SFDA recalls) have known expected outcomes; all come out as expected (`tests/test_match.py`). A 10,000-line stock file is matched in about 6 seconds.

**Cross-border** (all 90 evaluation alerts traced, $1.29 of model cost and 495 Tavily calls in total): for 28 of the 90 a regulator in another country reported the same product, verified on the page (news sites and same-country bodies are not counted). In 25 cases two regulators named the same batches: 13 within a day of each other, mostly Hong Kong relaying UK recalls, and 7 a month or more apart. The longest gap was 126 days: four batches recalled by Pakistan's DRAP on 14 April 2026 reached Nigeria's alert list in August. Batches of a Saudi SFDA recall (Credanil, Remedica) appear in a notice from Botswana's regulator 36 days later; a WHO falsified-Jakavi alert was relayed by DRAP 30 days later. 

**Smart update**: the first live run found 53 alerts that were not in the registry and read all of them (0 failures) for $0.05 of model cost. The next run, nine hours later, found nothing new: 10 regulators asked in 57 seconds, no model call, and Health Canada's 15 MB index answered "not modified" so it was not downloaded.

## How it works

```
 watch ──► read ──► check ──► re-read ──► match ──► trace
 10 regulators   Nemotron 3 Nano   six rule-based   Nemotron 3 Super   rules, per batch   Nemotron 3 Super
 every 30 min    on Token Factory  checks           when a check fails  (no AI, no cost)   + Tavily agent
```

1. **Watch** (`batchwatch/watch.py`). No regulator pushes its alerts, so each run asks every source one light question first: an API listing or a list page, sent with ETag / Last-Modified when the server provides them (Health Canada's 15 MB index is skipped entirely when unchanged). Only alerts not yet in the registry are downloaded in full. New alerts beyond `WATCH_MAX_NEW_PER_RUN` or the daily `WATCH_DAILY_USD` limit wait for the next run; a source that is down never stops the others. Every run is logged in `runs.jsonl` and each source's state in `watch_state.json`.
2. **Read** (`batchwatch/extract.py`). Nemotron 3 Nano extracts alert type and status, products, strengths, manufacturers, codes, batch numbers with expiry conditions, the reason and the required action, as constrained JSON.
3. **Check**. The answer is accepted only if it passes six deterministic checks against the regulator's own text:

| Check | Catches |
|---|---|
| Constrained JSON output and truncation check | malformed or cut-off replies |
| Every batch number must occur in the alert as a whole identifier | invented numbers ("24" does not count inside "2024") |
| A rule-based reader lists the batch numbers it can see on its own (303 of the 340, none wrong); the model must include them | missed batches |
| "All batches/lots" in a recall sentence or table cell needs an ALL entry, and an ALL entry needs that text | missed product-wide recalls, and import rules mistaken for recalls |
| In multi-product alerts each batch must sit under its product in the text | batches filed under the wrong strength |
| Product-name words must occur in the alert | misspelled names |
| Shape gate: a "product" that is a fragment of a broken reply (`"reason"`, `alert_type": ...`) is dropped, and Arabic text is never a batch number | a model writing its own repair attempts into a name field; phrases mistaken for batch numbers |

4. **Re-read**. If a check fails, Nemotron 3 Super reads the alert again and the reading that passes more checks is kept.
5. **Match** (`batchwatch/match.py`), deterministic and conservative. A pharmacist decides; BatchWatch never declares a medicine safe and never quarantines on its own.

| Status | Rule |
|---|---|
| Stop dispensing | exact batch and the same product (GTIN, or name and strength, with manufacturer agreement for short numbers) in a recall, falsified, substandard or watch-list alert |
| Follow the notice | the same, in a defect or safety notice (the regulator's instruction may be "continue use") |
| Confirm product | look-alike batch characters (O/0, I/1), an all-batches alert, a strength difference, a different listed manufacturer, a batch match on the brand name only, or a scanned batch without product evidence; the page asks the pharmacist |
| Review | product named in an alert without batch numbers |
| No action | the regulator withdrew the alert |

A different GTIN always blocks a match.

6. **Trace** (`batchwatch/trace.py`): Nemotron 3 Super gets the alert and two tools, `search_web` (Tavily search) and `read_pages` (Tavily extract), and decides every query and page itself. Deterministic checks mark a finding verified only if the agent read the page, its quote occurs in the page text, and every claimed batch number occurs there as a whole identifier. Dates are compared with the original alert. Saved traces are in `registry/traces/`.

### NVIDIA Nemotron and Nebius

| Model on Nebius Token Factory | Used for |
|---|---|
| `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | every alert extraction; identifying the columns of an unfamiliar stock export |
| `nvidia/nemotron-3-super-120b-a12b` | re-reading alerts that fail a check; the cross-border agent (tool calling) |
| `nvidia/Nemotron-3-Ultra-550b-a55b` | measured for comparison (table above) |

Token Factory's OpenAI-compatible API, constrained JSON output and tool calling let the same client run every step; switching a step between Nano, Super and Ultra is one model id, which is how the model comparison above was measured in a day. The Docker image is ready to run the web app on **Nebius Serverless Endpoints** and the smart update as a **Nebius Serverless Job** (see Deploy); a GitHub Actions workflow is the free fallback scheduler.

## Sources

| Source | Country | Access |
|---|---|---|
| SFDA drug circulars and withdrawals | Saudi Arabia | list pages, then each notice (fields labelled in Arabic, mapped to English) |
| WHO Medical Product Alerts | Global | alert list and pages |
| FDA drug enforcement reports | United States | openFDA API |
| MHRA medicines recalls and defect notices | United Kingdom | GOV.UK search and content APIs |
| Health Canada recalls and safety alerts | Canada | open-data JSON (conditional request), then each notice page |
| NAFDAC public alerts | Nigeria | WordPress REST API |
| DRAP recall alerts | Pakistan | WordPress REST API |
| SAHPRA recall press releases | South Africa | WordPress REST API |
| Rwanda FDA recall register | Rwanda | register table, one row per recall |
| FDA Ghana alerts | Ghana | WordPress REST API |

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env          # then add NEBIUS_API_KEY and TAVILY_API_KEY to .env
```

## Run

```bash
uvicorn app.main:app --port 8000                       # the web app: http://localhost:8000
python -m batchwatch.watch --dry-run                   # ask every regulator what is new, without reading anything
python -m batchwatch.watch                             # one smart-update pass (reads new alerts with Nemotron)
python -m batchwatch.trace <alert_id>                  # trace one alert across borders
python -m batchwatch.trace --all --max-usd 2           # trace every alert without a saved trace, within a budget
python eval/evaluate_extraction.py --model nano --fallback super   # re-score extraction on the 90 labelled alerts
python -m batchwatch.recheck --seed                    # re-run today's checks on every stored reading, no model call
python -m pytest                                       # 88 offline tests, no API key needed
```

Without a live registry the app serves `registry/seed_alerts.jsonl`, the 90 evaluated alerts as read in run U3, including each notice's text.

## Deploy

The smart update writes to `BATCHWATCH_DATA_DIR` (default `registry/`): `alerts.jsonl`, `runs.jsonl`, `watch_state.json`. In production it publishes them to a `data` branch of this repository, so the main branch stays code-only, and the web app reads them from there with `REGISTRY_BASE_URL=https://raw.githubusercontent.com/<owner>/<repo>/data`, refreshed every 5 minutes.

- **Scheduler, GitHub Actions** (`.github/workflows/watch.yml`, every 30 minutes): add the repository secret `NEBIUS_API_KEY`, and optionally `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `RESEND_API_KEY`, `NOTIFY_EMAIL_TO`, `NOTIFY_EMAIL_FROM`, `NOTIFY_WEBHOOK_URL`; the variable `SITE_URL` sets links in messages.
- **Scheduler, Nebius Serverless Job**: build the `Dockerfile` and run it with the argument `watch` and the environment `NEBIUS_API_KEY`, `GITHUB_REPO=<owner>/<repo>` and `GITHUB_TOKEN` (a token that may push to the repository).
- **Web app, Nebius Serverless Endpoint or a Hugging Face Docker Space**: the same image with the default argument `web` (port 7860), with `REGISTRY_BASE_URL`, `TAVILY_API_KEY` and `NEBIUS_API_KEY` for the trace agent and column identification, and optionally `SITE_URL`.

**Spending guard**: only a new cross-border trace costs money at request time, so only that is capped: `TRACE_DAILY_LIMIT` (default 30 a day for the site) and `TRACE_PER_VISITOR_LIMIT` (default 5). Saved traces, checks, search and scans are free and unlimited; `/api/limits` shows today's use. The smart update has its own daily limit, `WATCH_DAILY_USD` (default $0.50).

**Model credit**: when the Token Factory balance runs out (HTTP 402), the smart update stops reading, logs the run as waiting for credit and leaves the new alerts for a later run; the evaluation refuses to overwrite earlier results; the trace endpoint says so and does not count the attempt.

**Privacy**: barcodes are decoded on the device; stock files are parsed in memory and never written to disk; a list "kept on this device" stays in the browser. For an unfamiliar export, only the header row and three sample rows are sent to Nemotron to identify the columns.

## API

`/api/docs` lists every endpoint. The main ones: `GET /api/lookup` (one pack), `POST /api/check` (stock file), `GET /api/registry`, `GET /api/alert/{id}` (alert with notice text), `GET /api/search`, `GET /api/status` (smart update), `GET /api/insights` (cross-border findings), `POST /api/trace/{id}` (agent, streamed as server-sent events), `GET /feed.xml` (Atom; `?source=SFDA` for one regulator).

## License

MIT, see [LICENSE](LICENSE).
