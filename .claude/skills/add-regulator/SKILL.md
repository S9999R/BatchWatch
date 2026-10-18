---
name: add-regulator
description: Add a new drug regulator as a BatchWatch alert source (fetcher, smart-update listing, tests, README). Use when the user wants to watch another country's recall or falsified-medicine alerts.
argument-hint: "<regulator name> <alert list URL>"
---

Add the regulator: $ARGUMENTS

## 1. Look at the site first
Fetch the alert list page. Pick the lightest way to ask "what is newest", in this order:
a JSON API (openFDA style), a WordPress REST API (`/wp-json/wp/v2/posts`), a paginated HTML list.
Open two or three real alerts and note where the batch numbers live (body text, table, attachment, link).

## 2. Fetcher in `batchwatch/sources.py`
- WordPress site: one line with the existing factory, like
  `fetch_x = _wordpress("https://...", "Name (Country)", "PREFIX", category=..., search=...)`.
- Otherwise follow the list/detail pattern of `list_sfda` / `detail_sfda` or `who_items` / `detail_who`:
  `list_x(c, limit)` returns identity only (no notice text); `detail_x(c, item)` adds the text.
- Every alert dict has `source`, `alert_id` (unique, with a short prefix), `url`, `date` (YYYY-MM-DD),
  `title`, `text`. Use `html_to_text` so table rows stay readable: batch tables live there.
- Add it to `FETCHERS`.

## 3. Smart update in `batchwatch/watch.py`
Add it to `SOURCES`. Use `_whole(S.fetch_x)` for a single cheap call, or a list function plus detail
function so only new alerts are downloaded. Use `_conditional_get` when the list endpoint returns
ETag or Last-Modified.

## 4. Tests
Add an offline test with a saved HTML/JSON sample (no network in tests), in the style of
`tests/test_watch.py`: the list parser returns the newest items, the detail parser yields text that
contains the batch numbers. Run `python -m pytest -q`.

## 5. Check the extraction on real alerts
Run the extractor on two or three live alerts of the new source (this calls the model; tell the user).
Watch for registration numbers, dates and phone numbers next to batch numbers, and batch lists in
attachments. Do not add them to the frozen evaluation set; that set stays at 90 alerts.

## 6. Docs
Update the regulator count and list in `README.md` and the Arabic name, if needed, in `web/i18n.js`.
