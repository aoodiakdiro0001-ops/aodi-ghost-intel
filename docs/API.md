# Ghost-Intel API reference

Concise reference for the public classes and functions of each module in
`ghost_intel/`, plus the CLI. Everything is Python-standard-library only.
Where a parameter is injectable (`fetcher`, `now`, `throttle`, `summarizer`),
it exists so the whole pipeline can be tested offline with fakes.

Environment variables referenced across modules:

| Variable              | Effect |
|-----------------------|--------|
| `GHOST_DB`            | SQLite store path (default `ghost-intel.db`); a URL/DSN is rejected loudly |
| `GHOST_AI`            | `stub` (default) \| `groq` \| `ollama` |
| `GROQ_API_KEY`        | Groq free-tier key (only when `GHOST_AI=groq`) |
| `GROQ_MODEL`          | Groq model (default `llama-3.1-8b-instant`) |
| `OLLAMA_URL`          | Ollama base URL (default `http://localhost:11434`) |
| `OLLAMA_MODEL`        | Ollama model (default `llama3.2`) |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram bot credentials |
| `GHOST_TG_TIMEOUT`    | Telegram send timeout in seconds (default 30, clamped 1–120) |

---

## `ghost_intel.transport` — honest HTTP transport

Module constants: `UA` (the truthful, non-overridable User-Agent string),
`BASE_HEADERS`, `TransportError`.

- `fetch(url, method="GET", timeout=25.0, headers=None, data=None) -> Response`
  Performs one request; always returns a `Response` (HTTP error codes are data,
  not exceptions). Raises `TransportError` on network failure. Passing a
  `User-Agent` header raises `ValueError`; ambient system proxies are refused;
  the body is capped at 2 MiB.
- `Response(url, status, headers, body, elapsed_ms)`
  Immutable-ish result container (`__slots__`): final URL, HTTP status, header
  dict, raw body bytes (capped), elapsed time in ms.
- `status_class(status) -> str`
  Buckets a status code: `ok` (2xx), `pressure` (429/503 — slow down, never
  rotate), `forbidden` (401/403 — stop, do not evade), `client-error`,
  `server-error`.
- `TransportError(Exception)`
  Network-level failure class (DNS, refused, timeout) — retryable.

## `ghost_intel.throttle` — per-host politeness throttle

Constants: `DEFAULT_MIN_INTERVAL_S = 3.0`, `DEFAULT_MAX_PER_HOST = 20`.

- `Throttle(min_interval_s=3.0, now=time.time)`
  Tracks last-request time, crawl-delay, and per-run request count per host.
- `Throttle.set_crawl_delay(host, delay_s)`
  Adopts a robots `Crawl-delay` — always the slower of (delay, existing,
  own floor); it is never lowered.
- `Throttle.remaining_for_host(host, max_per_host=20) -> int`
  Remaining request budget for the host this run.
- `Throttle.wait_for(host, max_per_host=20) -> float`
  Blocks until it is polite to request the host; returns seconds slept. Raises
  `RuntimeError` when the per-host budget is exhausted ("stop visiting today").
- `Throttle.record_block(host)`
  A 429/403 arrived: pushes the host's next allowed request an hour out. No
  rotation of any kind.

## `ghost_intel.robots` — robots.txt handling

- `Robots(base_url, timeout=20.0, fetcher=None)`
  Fetches and parses `robots.txt` once per host, using the same truthful UA.
  Exposes `host`, `agent`, `available`, `status` (`"200"`, `"403"`,
  `"unreachable"`, `"not-robots"`, …), and `welcomes` (False after a
  401/403/429/503 on robots.txt itself — the whole host is then skipped).
- `Robots.crawl_delay() -> int | None`
  The `Crawl-delay` the rules request for our agent, if any.
- `Robots.allowed(url) -> (ok: bool, reason: str)`
  Whether the URL may be fetched. `reason` is `robots-allow`,
  `robots-disallow`, `host-not-welcoming`, or `robots-<status>` when no usable
  robots.txt exists (absent = allow, per convention).

## `ghost_intel.extract` — extraction, normalization, change detection (offline)

- `html_to_text(html) -> str`
  Reduce an HTML/text body to readable, whitespace-normalized text (scripts,
  styles, tags stripped).
- `content_hash(text) -> str`
  SHA-256 over normalized text (lowercased, whitespace-insensitive) — the
  change-detection fingerprint.
- `extract_prices(text) -> list[float]`
  Sorted unique prices found via currency patterns (`$99`, `€1,299.00`,
  `£49`, `₺150`, `100 USD`, …) with localized-number parsing.
- `extract_keywords(text, kws) -> list[str]`
  Case-insensitive presence check of a configured keyword list against text.
- `diff_summary(old_text, new_text, max_items=12) -> dict`
  Word-level diff: `{added, removed, changed, added_count, removed_count}`.

## `ghost_intel.storage` — local SQLite time-series store

- `Storage(path=None)`
  Opens/creates the local store (env `GHOST_DB`), enables WAL + busy timeout,
  chmods it to `0600`, and applies the schema. A URL/DSN path raises
  `ValueError` — there is no remote warehouse by design.
- `Storage.add_target(name, url, kind="page", keywords="") -> int`
  Upsert a monitored target by URL; returns its id.
- `Storage.target_id(url) -> int | None`
  Look up a target id by exact URL.
- `Storage.list_targets() -> list[dict]`
  All targets as `{id, name, url, kind, keywords}` ordered by id.
- `Storage.add_snapshot(target_id, status, status_class, content_hash, prices, text_len, text="", note="")`
  Record one timestamped fetch observation.
- `Storage.latest_snapshot(target_id) -> dict | None`
  Most recent snapshot for a target (`ts`, `status`, `status_class`,
  `content_hash`, `prices`, `text_len`, `text`).
- `Storage.price_history(target_id, limit=100) -> list[(ts, prices)]`
  Time-series of `(ts, prices)` for 200-status snapshots, oldest first
  (limit clamped to 1–500).
- `Storage.add_change(target_id, kind, detail)`
  Record a detected change (`kind`: `content` | `price` | `status`; `detail`
  is JSON-serialized).
- `Storage.recent_changes(since_ts=0.0, limit=50) -> list[dict]`
  Recent changes, newest first, joined with target names.
- `Storage.counts() -> dict`
  Row counts: `{targets, snapshots, changes}`.
- `Storage.close()`
  Close the connection.

## `ghost_intel.llm` — optional AI summarization layer

Contract: no network unless a backend is configured; provider errors are
redacted (API keys never leak); the pipeline never depends on AI.

- `get_summarizer(kind=None) -> summarizer`
  Factory from `kind` or env `GHOST_AI`: `groq` → `GroqSummarizer`, `ollama` →
  `OllamaSummarizer`, anything else → `StubSummarizer`.
- `StubSummarizer` (`name = "stub"`)
  Deterministic, offline, zero-network fallback.
- `StubSummarizer.summarize(target_name, change) -> str`
  One-line rule-based summary of a price/status/content change dict.
- `GroqSummarizer(model=None, timeout=30.0)` (`name = "groq"`)
  Groq free-tier chat completions; requires `GROQ_API_KEY` (falls back to the
  stub without it; on API error returns `"(AI offline) …"` with the key
  redacted).
- `GroqSummarizer.summarize(target_name, change) -> str`
  One-line summary via the configured Groq model.
- `OllamaSummarizer(model=None, timeout=60.0)` (`name = "ollama"`)
  Local Ollama `/api/generate`; any failure falls back to the stub.
- `OllamaSummarizer.summarize(target_name, change) -> str`
  One-line summary via the local model.

## `ghost_intel.crawler` — polite crawl over public pages

- `CrawlResult(url, name, status=0, status_class="", changed=False, prices=None, summary="", skipped="")`
  Per-target outcome; `skipped` non-empty means the page was **not** fetched
  and says why (e.g. `robots-disallow`, `budget:…`). `as_dict()` for JSON.
- `Crawler(storage, summarizer=None, fetcher=None, throttle=None, now=time.time)`
  Pipeline object; caches one `Robots` per host per run and feeds robots
  `Crawl-delay` into the throttle.
- `Crawler.crawl_target(tgt) -> CrawlResult`
  Crawl one target dict: robots check → throttle → fetch → extract
  text/prices/hash → diff against the latest snapshot → record snapshot and
  any change. A 401/403/429/503 cools the host down (`record_block`), never
  triggers evasion.
- `Crawler.run(targets=None) -> list[CrawlResult]`
  Crawl all stored targets (or a provided list).

## `ghost_intel.leads` — public job-feed scanner

- `Lead(title, source, matched)` — a matched posting; `as_dict()` for JSON.
- `LeadsScanner(keywords, fetcher=None, throttle=None)`
  Scans public RSS/HTML listing feeds for the given keywords; same defensive
  posture as the crawler (robots per host, throttle, truthful UA, public only).
- `LeadsScanner.scan_feed(feed_url) -> (list[Lead], note: str)`
  Fetch one feed and return matching leads plus a note (`ok`,
  `robots-disallow`, `http-403`, `network-error`, `budget:…`).
- `LeadsScanner.scan_all(feed_urls) -> (list[Lead], notes: list[dict])`
  Scan several feeds; notes are `{url, note, count}` per feed.
- `_parse_feed(text) -> list[dict]` (internal) — tolerant RSS/Atom item
  extraction with an XML fallback.

## `ghost_intel.alerts` — Telegram gateway + digest builder

- `send(text, chat_id=None) -> dict`
  Send a Telegram message (clamped to 4096 chars, redirect-following blocked,
  token redacted from errors). Returns the API JSON or
  `{"ok": False, "err": …}`; fails cleanly when `TELEGRAM_TOKEN` is unset.
- `build_digest(results, changes, when=None) -> str`
  Render a readable digest from `CrawlResult`s + recent changes: counts
  (scanned/ok/changed/skipped), up to 10 change lines, up to 5 skipped lines.
  Offline and deterministic.

## `ghost_intel.dashboard` — static HTML renderer

- `render(store) -> str`
  Build a single self-contained HTML page from the store: counts cards, target
  table with inline SVG price sparklines, and the recent-changes feed. No
  external JS/CSS, no build step — hosts free on GitHub Pages.

## `ghost_intel.cli` — command-line interface

Global flags (before the subcommand): `--db PATH` (or `GHOST_DB`),
`--ai stub|groq|ollama` (or `GHOST_AI`).

Run as `python -m ghost_intel.cli <command>`:

| Command | Description |
|---------|-------------|
| `init [--sample FILE]` | Create the store; optionally load targets from a JSON file (`{"targets": [{name, url, kind, keywords}]}`). |
| `add <name> <url> [--kind page] [--keywords k1,k2]` | Register (upsert) a target to monitor. |
| `scan [--json]` | Crawl all targets politely (the cron job); prints changed/skipped, optional JSON results. |
| `digest [--hours 24] [--send]` | Crawl, build the digest, print it; with `--send`, push it to Telegram. |
| `leads <url>… [--keywords k1,k2] [--json]` | Scan public job feeds for keyword matches. |
| `dashboard [--out PATH]` | Render the static HTML dashboard (default `dashboard/index.html`; the workflow uses `docs/index.html`). |
| `doctor` | Environment self-check: store counts, active AI backend, Telegram/Groq secret status. |

Offline-safe: `dashboard` and `doctor` never touch the network; `scan`,
`digest`, and `leads` only contact the targets/feeds you configured.

## Tests

`tests/test_ghost.py` — offline suite (injected fake fetcher, zero network)
covering the behavior above **and** the compliance contracts: truthful
non-overridable UA, robots obedience, `Disallow` honored, block ⇒ stop (never
rotate), per-host budget, remote-DSN rejection, token redaction.

```bash
python -m unittest discover -s tests -v
```

---

MIT © 2026 Baran Avisat — Aodı Akdıro Suite · baranavisat@gmail.com ·
WhatsApp +90 531 326 37 22 · github.com/aoodiakdiro0001-ops
