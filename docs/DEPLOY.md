# Deploying Ghost-Intel

Ghost-Intel is a Python-stdlib-only tool with a built-in GitHub Actions workflow
(`.github/workflows/ghost-intel.yml`) that acts as its free "server": every 4
hours it crawls your targets politely, rebuilds the dashboard, sends a digest if
a token is configured, and commits the store + dashboard back to the repo.

Follow the steps below to go from clone to a running radar.

---

## 1. Create the GitHub repository

1. Create a new repository on GitHub, e.g. `aodi-ghost-intel`.
2. Push the project:

   ```bash
   git remote add origin https://github.com/<you>/aodi-ghost-intel.git
   git push -u origin main
   ```

**Recommendation: keep the repo PRIVATE** if either of these is sensitive for you:

- your target list (`examples/targets.json` reveals who you watch), or
- the stored data — the SQLite file (`ghost-intel.db`) contains extracted page
  text from every snapshot, and the workflow commits it back to the repo.

A **public** repo gets unlimited GitHub Actions minutes (the 4h cron costs
roughly 90 minutes/month). A **private** repo shares the free quota of 2,000
Actions minutes/month — at ~30 s per run every 4 hours (~180 runs) you use
about 90 minutes, so it still fits comfortably, but the margin is smaller.

Note: the generated dashboard is a static page; GitHub Pages on a private repo
requires a GitHub Pro/Team plan, so if you want the Pages dashboard for free,
use a public repo — or keep it private and read `docs/index.html` locally.

## 2. Set the optional secrets

All secrets are **optional**. The radar works with none of them (offline stub
AI, digest printed to the Actions log instead of sent to Telegram).

Go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret              | Needed for                          | How to get it |
|---------------------|-------------------------------------|---------------|
| `TELEGRAM_TOKEN`    | Sending the digest to Telegram      | Talk to [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → copy the token |
| `TELEGRAM_CHAT_ID`  | Identifying your chat               | Send a message to your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id` |
| `GHOST_AI`          | Choosing the AI backend             | `stub` (default, offline) \| `groq` \| `ollama` |
| `GROQ_API_KEY`      | Free-tier AI summaries              | Create a free key at console.groq.com — only set if `GHOST_AI=groq` |

Without a token, the workflow **skips** the digest step (`if: env.TELEGRAM_TOKEN != ''`)
and nothing fails.

## 3. Edit your target list

Replace the samples in `examples/targets.json` with **public** pages you are
entitled to observe (your own sites, public pricing/changelog/RSS pages):

```json
{
  "targets": [
    {
      "name": "Acme Pricing",
      "url": "https://acme.example/pricing",
      "kind": "price",
      "keywords": ["price", "plan", "enterprise"]
    },
    {
      "name": "Acme Changelog",
      "url": "https://acme.example/changelog",
      "kind": "page",
      "keywords": ["released", "new feature", "deprecated"]
    }
  ]
}
```

Fields: `name` (label in reports), `url` (the public page), `kind`
(`price` | `page` | `social` | `leads`), `keywords` (array; used for
keyword-presence checks and matching).

Commit and push the file. The next run of `python -m ghost_intel.cli init --sample
examples/targets.json` loads it into the store (existing URLs are upserted).

You can also add targets one by one from the CLI
(`python -m ghost_intel.cli add <name> <url>`) — the workflow commits the store
back, so they persist; but keeping the list in `targets.json` makes it versioned
and reviewable.

## 4. Enable GitHub Pages on `docs/`

The workflow renders the dashboard to **`docs/index.html`** by default
(`python -m ghost_intel.cli dashboard --out docs/index.html`) and commits it.

1. In the repo: **Settings → Pages**.
2. Under **Build and deployment**: Source = **Deploy from a branch**,
   Branch = **`main`**, folder = **`/docs`**.
3. Save. After the first successful run, your dashboard is at
   `https://<you>.github.io/aodi-ghost-intel/`.

Prefer the repo root instead? Change the workflow line to
`dashboard --out index.html` and select **`/` (root)** as the Pages folder.

## 5. How the 4-hour cron works

The schedule in `.github/workflows/ghost-intel.yml`:

```yaml
schedule:
  - cron: "23 */4 * * *"   # every 4 hours at minute 23
workflow_dispatch:
```

- **`23 */4 * * *`** — runs at 00:23, 04:23, 08:23, 12:23, 16:23, 20:23 UTC.
  The odd minute avoids the top-of-hour congestion where GitHub may delay jobs
  heavily on the free tier.
- Each run: `init --sample` → `scan` → `dashboard --out docs/index.html` →
  `doctor` → `digest --send` (only if a token is set) → commit + push the
  SQLite store and `docs/index.html` back.
- A **concurrency group** (`ghost-intel`, no cancel-in-progress) serializes
  runs so two overlapping jobs never race on the same store; the push step does
  `git pull --rebase --autostash` before pushing to survive concurrent pushes.
- **`workflow_dispatch`** lets you trigger an on-demand run from the **Actions**
  tab (Run workflow) — useful right after setup to populate the baseline.
- Heads-up on GitHub cron behavior: scheduled workflows on public repos are
  disabled automatically after **60 days of repository inactivity**; any commit
  or manual run resets the clock. GitHub also delivers no SLA on exact timing —
  runs can be late under load.

The first run only records baselines. **Changes are detected from the second
run onward**, because a diff needs a previous snapshot.

## 6. Add leads feeds

The leads scanner is CLI-driven (it is not part of the default cron):

```bash
python -m ghost_intel.cli leads \
  "https://boards.example/jobs.rss" "https://another.example/feed.xml" \
  --keywords "python,fastapi,api"
```

How to find feeds: many job boards expose a public RSS/Atom feed of listings —
look for an RSS link on their jobs page or try `/jobs.rss`, `/feed.xml`,
`/atom.xml`. Only use feeds that are **publicly accessible**; the same bright
lines apply (robots.txt is checked per feed host, a 401/403/429 skips the feed
and is never evaded).

Want it on the cron? Add a step to the workflow after `scan`, e.g.
`python -m ghost_intel.cli leads "<your-feed-url>" --keywords "python" --json`.
Results are printed to the Actions log; leads are **hints** — follow up yourself.

## 7. Troubleshooting

**A target shows `skip … (robots-disallow)`**
Your target's `robots.txt` disallows that path. This is the tool working as
designed. Options: pick a public page the site does allow, or accept that you
don't monitor it. Do **not** try to defeat the rule.

**`403` / `429` on a target (status pill "forbidden" / "pressure")**
The host said stop. Ghost-Intel cools that host down for the rest of the run
and reports it — it will **not** switch User-Agent, IP, or proxy to get around
it, and neither should you. Repeated 403s usually mean the page is protected or
the host dislikes automated access: drop the target. 429 usually means too
many requests: the per-host throttle (3 s minimum, plus robots `Crawl-delay`,
plus a 20-request-per-run budget) is already conservative; if it persists,
monitor the page less often or from your own machine on a slower cadence.

**Digest not sending**
- `doctor` shows `telegram_token: unset (digest won't send)` → add the
  `TELEGRAM_TOKEN` secret (and the workflow's `TELEGRAM_CHAT_ID`).
- Token set but no message: check `TELEGRAM_CHAT_ID` matches the chat, and that
  you have actually sent the bot at least one message (bots can't message you
  first).
- The workflow's digest step runs only `if: env.TELEGRAM_TOKEN != ''`.
- The failure object redacts the token; a redacted error still tells you the
  network/API refused.

**Dashboard not updating**
- Check the workflow run succeeded through the "Persist store + dashboard"
  step — the commit/push at the end is what publishes the new page.
- `push race` / `push failed` errors mean another run pushed first; the rebase
  step retries, but a hard conflict can fail a run — just re-run the workflow.
- Pages settings: confirm Source = deploy from branch, `main`, `/docs` (see
  step 4), and that `docs/index.html` exists on the branch.
- Remember the dashboard is rebuilt from the local store; a run that failed
  before the dashboard step won't change the page.

**"No changes detected" on every run**
That's a valid state: either nothing changed on the monitored pages, or you're
looking at the first run (baseline). Verify with a second
`workflow_dispatch` run or a local `python -m ghost_intel.cli scan`.

**Where did my data go?**
The source of truth is the local SQLite file `ghost-intel.db` (path via
`GHOST_DB`), created with mode `0600`. The workflow commits it back each run.
Delete the file to erase everything. Pointing `GHOST_DB` at a URL/remote DSN
fails loudly by design — there is no remote warehouse.

---

MIT © 2026 Baran Avisat — Aodı Akdıro Suite · baranavisat@gmail.com ·
WhatsApp +90 531 326 37 22 · github.com/aoodiakdiro0001-ops
