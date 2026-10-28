"""Ghost-Intel CLI — the single entry GitHub Actions calls.

Commands:
  init                create the store + optional sample targets
  add <name> <url>    register a target to monitor
  scan                crawl all targets politely (the cron job)
  digest              build + (optionally) send the Telegram digest
  leads <url>...      scan public job feeds for matching leads
  dashboard           render a static HTML dashboard from the store
  doctor              environment self-check

Offline-safe: `scan`/`digest`/`leads` only touch the network for the targets
you configured. `dashboard` and `doctor` are pure-local.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ghost_intel import alerts, crawler, dashboard, leads, storage  # noqa: E402
from ghost_intel.llm import get_summarizer  # noqa: E402


def cmd_init(args):
    store = storage.Storage(args.db)
    n = 0
    if args.sample and os.path.exists(args.sample):
        data = json.load(open(args.sample))
        for t in data.get("targets", []):
            store.add_target(t["name"], t["url"], t.get("kind", "page"),
                             ",".join(t.get("keywords", [])))
            n += 1
    counts = store.counts()
    store.close()
    print("initialized %s (%d targets, %d snapshots)"
          % (os.path.basename(args.db or storage.DEFAULT_DB),
             counts["targets"], counts["snapshots"]))
    if n:
        print("loaded %d sample target(s)" % n)
    return 0


def cmd_add(args):
    store = storage.Storage(args.db)
    store.add_target(args.name, args.url, args.kind, args.keywords)
    store.close()
    print("monitoring: %s -> %s" % (args.name, args.url))
    return 0


def cmd_scan(args):
    store = storage.Storage(args.db)
    summ = get_summarizer(args.ai)
    c = crawler.Crawler(store, summarizer=summ)
    results = c.run()
    store.close()
    changed = [r for r in results if r.changed]
    skipped = [r for r in results if r.skipped]
    print("scanned %d target(s): %d changed, %d skipped"
          % (len(results), len(changed), len(skipped)))
    for r in changed:
        print("  CHANGE %s: %s" % (r.name, r.summary or r.prices))
    for r in skipped:
        print("  skip %s (%s)" % (r.name, r.skipped))
    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2))
    return 0


def cmd_digest(args):
    store = storage.Storage(args.db)
    summ = get_summarizer(args.ai)
    c = crawler.Crawler(store, summarizer=summ)
    results = c.run()
    since = time.time() - (args.hours * 3600)
    changes = store.recent_changes(since_ts=since)
    store.close()
    text = alerts.build_digest(results, changes)
    print(text)
    if args.send:
        res = alerts.send(text)
        print("\n[tg] ok=%s %s" % (res.get("ok"), res.get("err", "")))
    return 0


def cmd_leads(args):
    kws = [k.strip() for k in (args.keywords or "").split(",") if k.strip()]
    scanner = leads.LeadsScanner(kws)
    all_leads, notes = scanner.scan_all(args.urls)
    print("found %d matching lead(s)" % len(all_leads))
    for l in all_leads[:20]:
        print("  • %s  [%s]  (%s)" % (l.title, ",".join(l.matched), l.source))
    for n in notes:
        print("  feed %s -> %s (%d)" % (n["url"], n["note"], n["count"]))
    if args.json:
        print(json.dumps([l.as_dict() for l in all_leads], indent=2))
    return 0


def cmd_dashboard(args):
    store = storage.Storage(args.db)
    out = dashboard.render(store)
    store.close()
    path = args.out or "dashboard/index.html"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)
    print("dashboard written to %s (%d bytes)" % (path, len(out)))
    return 0


def cmd_doctor(args):
    store = storage.Storage(args.db)
    counts = store.counts()
    store.close()
    checks = [
        ("db", os.path.basename(args.db or storage.DEFAULT_DB)),
        ("targets", counts["targets"]),
        ("snapshots", counts["snapshots"]),
        ("changes", counts["changes"]),
        ("ai", get_summarizer(args.ai).name),
        ("telegram_token", "set" if os.environ.get("TELEGRAM_TOKEN")
         else "unset (digest won't send)"),
        ("telegram_chat", os.environ.get("TELEGRAM_CHAT_ID") or "unset"),
        ("groq_key", "set" if os.environ.get("GROQ_API_KEY") else "unset"),
    ]
    out = ["Ghost-Intel doctor", "=" * 44]
    for k, v in checks:
        out.append("  %-16s %s" % (k, v))
    out.append("=" * 44)
    out.append("public-only, robots-respecting crawler. No evasion by design.")
    print("\n".join(out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ghost-intel", description=__doc__)
    ap.add_argument("--db", default=None, help="store path (or GHOST_DB)")
    ap.add_argument("--ai", default=None, help="stub | groq | ollama")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create the store (+ sample targets)")
    p.add_argument("--sample", default=None, help="path to a targets JSON")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("add", help="register a target")
    p.add_argument("name")
    p.add_argument("url")
    p.add_argument("--kind", default="page")
    p.add_argument("--keywords", default="")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("scan", help="crawl all targets (the cron job)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("digest", help="build + optionally send the digest")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--send", action="store_true")
    p.set_defaults(fn=cmd_digest)

    p = sub.add_parser("leads", help="scan public job feeds")
    p.add_argument("urls", nargs="+")
    p.add_argument("--keywords", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_leads)

    p = sub.add_parser("dashboard", help="render static HTML dashboard")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_dashboard)

    p = sub.add_parser("doctor", help="environment self-check")
    p.set_defaults(fn=cmd_doctor)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
