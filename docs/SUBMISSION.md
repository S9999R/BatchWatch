# BatchWatch: Devpost submission draft

Everything below is a draft to paste into the Devpost form. Numbers come from `eval/results_*.json`, `registry/traces/` and the watcher's run log; re-check them on the day of submission.

## Name and tagline

**BatchWatch: is this medicine recalled?**
Reads drug-recall and counterfeit alerts from 10 regulators with NVIDIA Nemotron, checks its own reading, and tells a pharmacist in seconds whether a pack or a stock list is affected.

## Track

Best Apps and Agents. Bonus: Best Use of Tavily (runtime calls to Tavily search and extract).

## What it does

A batch recalled in one country is often still on shelves in another. Regulators publish recalls as web pages, PDFs and tables, each in its own format and language, and pharmacists check them by hand. In August 2026 Nigeria's NAFDAC relayed a recall that Pakistan's DRAP had issued four months earlier.

BatchWatch is a workflow that runs itself:

1. **Watch**: every 30 minutes it asks 10 regulators (Saudi SFDA, US FDA, UK MHRA, Health Canada, NAFDAC, DRAP, SAHPRA, Rwanda FDA, FDA Ghana, WHO) what is new, using light listings and HTTP validators, and downloads only alerts it has not seen.
2. **Read**: NVIDIA Nemotron 3 Nano on Nebius Token Factory turns each notice into products, strengths and batch numbers (constrained JSON).
3. **Check**: six deterministic checks compare the reading with the regulator's own text (grounding, a rule-based reader, all-batches wording, misattachment, name spelling, truncation).
4. **Re-read**: if a check fails, Nemotron 3 Super reads the alert again.
5. **Match**: a pharmacist scans a pack's GS1 DataMatrix (decoded on the phone) or uploads a stock export (CSV or Excel, Arabic or English columns). Matching is rule-based, batch by batch, with look-alike characters (O/0, I/1), GTIN, strength and manufacturer evidence. The answer is plain: Stop dispensing, Confirm product, Follow the notice, Review, or No alert found, with what to do, the regulator's own words and a quarantine note.
6. **Trace**: an agent on Nemotron 3 Super chooses its own Tavily searches and reads regulator pages to find where else a batch was flagged; a finding counts only if its quote and batch numbers are on the page.

New alerts go out on an Atom feed and to Telegram, email or a webhook. A stock list kept in the browser is re-checked automatically when new alerts arrive. The interface is in English and Arabic (right to left, SFDA wording).

## Measured

- 90 real alerts from 10 regulators, 340 batch numbers labelled by hand: **90/90 alerts and 340/340 batch numbers correct, 0 invented, $0.076 for all 90**.
- Same accuracy as Nemotron 3 Ultra ($0.59) at about an eighth of the cost. Without the Super re-read, Nano gets 88/90 and misses 13 batch numbers; the checks sent 9 alerts to Super.
- The agent traced all 90 alerts ($1.29): for 28 a regulator in another country reported the same product, verified on the page. Same batches at two regulators (25 cases): 13 within a day, 7 a month or more apart, longest 126 days (Pakistan → Nigeria). An SFDA recall appeared in Botswana 36 days later.
- First live smart-update run: 53 new alerts read, 0 failures, $0.05. A quiet run: 10 regulators in 57 s, no model call.

## How we built it

Python, FastAPI, a no-build HTML/CSS/JS front end, html5-qrcode for on-device GS1 DataMatrix decoding. Nebius Token Factory (OpenAI-compatible API) for every model call: Nemotron 3 Nano (extraction, stock-column identification), Nemotron 3 Super (re-reading, tool-calling agent), Nemotron 3 Ultra (measured for comparison). Tavily search and extract as the agent's tools. GitHub Actions or a Nebius Serverless Job runs the smart update; the same Docker image serves the web app on Nebius Serverless Endpoints or a Hugging Face Space. (State on the day of submission which of these the demo actually runs on.)

## Built with

nvidia-nemotron, nebius-token-factory, nebius-serverless, tavily, python, fastapi, javascript, html5-qrcode, gs1-datamatrix, github-actions, docker

## Testing instructions

Open the demo URL. No login.
- **One pack**: press a demo chip (104173 = a Saudi SFDA recall; AVT50 = falsified Jakavi reported by WHO and DRAP; 25JO1305 = look-alike of a recalled batch), or "Check the demo pack", or scan the demo DataMatrix shown on the page with a phone.
- **Stock list**: choose "A Riyadh pharmacy" (Arabic columns, real SFDA recalls) or upload your own CSV/Excel.
- **Alerts**: search a batch or product; open an alert to see the six checks, the regulator's text with the batches highlighted, and the cross-border trace.
- **How it works**: the measured results, the model comparison and the live watcher table.
- The "ع" button switches to Arabic.

## Feedback on Nebius Token Factory, AI Cloud and NVIDIA models (required)

**What we used them for.** Token Factory served every model call: Nemotron 3 Nano for extracting batch-level records from 140+ regulator notices and for identifying stock-file columns, Nemotron 3 Super as a fallback reader and as a tool-calling agent (Tavily search and extract), and Nemotron 3 Ultra as a measured comparison. We planned Nebius Serverless Jobs for the scheduler and Serverless Endpoints for the web app.

**What worked well.**
- The OpenAI-compatible API meant one client for every step; switching a step between Nano, Super and Ultra was a model-id change, so we could measure three set-ups on the same 90 alerts in a day.
- `response_format: json_object` removed almost all malformed replies; it was the single biggest reliability gain over our first free-text runs (43/45 → 90/90 alerts after the checks).
- Tool calling on Super was dependable across 90 multi-step agent runs (0 failures); it chose sensible searches, including batch-number queries.
- Prices are low enough to evaluate repeatedly: a full 90-alert run cost $0.06–0.08 on Nano.

**What needs work.**
- **Reasoning tokens and max_tokens**: Nano's reasoning plus a long multi-product reply was cut off at 8k tokens on a 26-batch alert; we had to raise max_tokens to 12k (24k on the fallback) and detect truncation ourselves. A documented way to cap or disable reasoning per request, or reasoning usage reported separately in `usage`, would help.
- **Latency surprise**: in our runs Nano's median time per alert (15.3 s) was higher than Ultra's (5.7 s). If that is reasoning length, guidance on prompting Nano for short reasoning would help.
- **Model ids are inconsistent** (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, `nvidia/nemotron-3-super-120b-a12b`, `nvidia/Nemotron-3_5-Lightning`): case and separators differ, so ids are easy to mistype.
- **Long requests**: we saw `APIConnectionError` on long calls until we set a 300 s timeout and retries; documenting expected durations and recommended client timeouts would save time.
- **No NVIDIA vision model after Nemotron Nano Omni was removed (31 Aug 2026)**: we had planned to read pack photos with it; we moved barcode decoding to the phone instead.
- **Arabic** is not an officially supported Nemotron language; Saudi SFDA notices mix Arabic labels with English values, so we mapped the Arabic field labels to English before extraction. Official Arabic support would matter for the Gulf market.
- **Credits and billing**: the balance ran out in the middle of an evaluation run, and every remaining call failed with HTTP 402; a low-balance email or a usage alert threshold would have prevented a wasted run. The $1 trial balance also ran out quickly during evaluation; the promo-code confirmation did not arrive by email for several days; and it was not clear whether hackathon credits cover Serverless Jobs and Endpoints, which made us keep a GitHub Actions fallback.

**Onboarding (zero to hello world).** About 15 minutes: create a key in the Token Factory console, point the OpenAI client at `https://api.tokenfactory.nebius.com/v1/`, call Nano. The model catalogue page with prices made choosing models easy.

**Would we build with it again?** Yes. The combination of a cheap, fast reader (Nano) checked by rules, a stronger model only when needed (Super), and one API for all of them is exactly the cost structure a public-health tool needs; we would like the reasoning-control and Arabic points above addressed.

## What was built during the submission period

All of it: the project started on 26 August 2026 or later (submission period) and every file in the repository was written during the period.
