# Security & Responsible-Use Policy — Ghost-Intel

Ghost-Intel is a **defensive, competitor-intelligence radar for public web
pages**. It is built to be lawful, polite, and auditable. This document is the
contract — every test in `tests/` pins these bright lines.

## The bright line (enforced in code, not just prose)

Ghost-Intel **does** the following, always:

- Presents a **single, truthful User-Agent** that identifies the tool, its
  repository, and a contact email (`baranavisat@gmail.com`). It is never
  overridable to impersonate a browser (see `transport.py`).
- **Obeys `robots.txt`** for every host. Absent robots = allow (standard
  convention). A `Disallow` rule is honored.
- **Rate-limits per host** (`throttle.py`): a real minimum interval between
  requests, honors `Crawl-delay` (only ever raising it, never lowering it), and
  a hard per-run request budget per host.
- **Observes public pages only.** It fetches what an anonymous visitor with a
  browser can see, without logging in.

Ghost-Intel **never** does the following — these are out of scope **by design**:

- **No TLS/JA3/JA4 fingerprint crafting.** It does not shape its TLS handshake
  to look like a specific browser.
- **No identity rotation to evade a block.** An HTTP `401`/`403`/`429`/`503` is
  a **STOP signal** for that host — it cools the host down and does *not*
  switch User-Agent, IP, or proxy to get around the block. (`throttle.record_block`)
- **No login / paywall / CAPTCHA bypass.** It does not authenticate, solve, or
  circumvent any access control. If a page is behind auth, it is not fetched.
- **No ambient system proxies.** `transport.fetch` refuses to use OS-configured
  proxies so receipts reflect the real network path.
- **No false success claims.** Reports state exactly what happened, including
  skips, blocks, and network errors. There is no "undetectable" or "100%
  success" claim anywhere in this project.

## What you may monitor

Only pages you are **entitled** to observe:

- Your own sites and products.
- **Public** competitor pages (pricing, public changelogs, public RSS).
- **Public** job/listing feeds (for the leads scanner).

Do **not** point Ghost-Intel at:

- Pages behind a login, paywall, or membership.
- Anything whose `robots.txt` disallows it (the tool enforces this).
- Personal data or anything you lack a lawful basis to process.

You are responsible for ensuring your use complies with each target's Terms of
Service and applicable law (e.g. GDPR for any personal data, and local
computer-misuse / CFAA-equivalent statutes). This tool observes **public,
non-authenticated** content politely — that is the whole scope.

## Data it stores

Locally, in SQLite (`GHOST_DB`): target config, per-fetch snapshots (status,
content hash, extracted prices, readable text), and detected changes with
timestamps. A remote data warehouse (e.g. Supabase) is **not** wired in by
design — pointing `GHOST_DB` at a URL fails loudly rather than pretending
(same honesty posture as Nexus-Zero).

Treat the store as sensitive (it reflects what you monitor). The file is
created `0600`. Delete it to erase everything.

## Reporting a vulnerability

Email **baranavisat@gmail.com** with the subject `[Ghost-Intel] security`.
Please include reproduction steps. You will get an acknowledgment within 72
hours. Please do **not** file exploitable issues in the public tracker.

## Scope of this policy

This policy is about the software's behavior. It is **not** legal advice. If
you are unsure whether a target is fair game, do not scan it.

---

MIT © 2026 Baran Avisat — Aodı Akdıro Suite · baranavisat@gmail.com ·
WhatsApp +90 531 326 37 22
