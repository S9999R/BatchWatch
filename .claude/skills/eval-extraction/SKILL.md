---
name: eval-extraction
description: Run the BatchWatch extraction evaluation on the 90 labelled alerts and report regressions against run U3. Costs Nebius credit unless "reuse" is passed.
disable-model-invocation: true
argument-hint: "[reuse] [model=nano|super|ultra] [fallback=super|none]"
---

Evaluate extraction against `eval/gold_batches.json`. Arguments: $ARGUMENTS

1. Build the command from the arguments. Defaults: `--model nano --fallback super`.
   - `reuse` in the arguments: add `--reuse --recheck` (saved replies through today's checks, no API call, free).
   - Without `reuse` the run calls Nemotron on all 90 alerts (about $0.08 for nano+super, $0.60 for ultra).
     Say the expected cost and ask the user to confirm before running.
2. Run `python eval/evaluate_extraction.py <flags>` from the project root, in the background if it calls
   the model. If it exits with "Token Factory balance exhausted", stop and tell the user; nothing was written.
3. Compare the new `eval/results_<model>+<fallback>.json` (or `results_<model>.json` with fallback none) with
   the baseline `eval/results_nano+super_run_U3.json` (90/90 alerts, 340/340 batches, 0 extra, 91/91 product
   attachment, $0.0764). Show only:
   - summary rows that changed (alerts_fully_correct, batches_correct, batches_extra, batches_missed,
     product_attachment_correct, escalated_to_fallback, cost_usd, median_latency_s)
   - alerts that went from correct to wrong, and the reverse, from `per_alert[*].issues`
4. If any alert regressed, delegate to the `eval-miss-analyst` subagent with the run name for the cause.
5. Never edit `eval/gold_batches.json` or `eval/alerts_snapshot.jsonl`. Never update the README numbers
   unless the user asks.
