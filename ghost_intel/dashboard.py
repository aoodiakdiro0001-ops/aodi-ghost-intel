"""Static HTML dashboard renderer (stdlib-only, offline).

Renders a single self-contained HTML file from the store: target table, recent
changes feed, and an inline price time-series (no external JS dependencies, so
it hosts for FREE on GitHub Pages). Charts are drawn with a tiny inline SVG
sparkline — no CDN, no build step.
"""
import datetime
import html as _html
import json


def _esc(s):
    return _html.escape(str(s if s is not None else ""))


def _sparkline(points, width=220, height=44):
    """Inline SVG polyline for a series of numbers (prices over time)."""
    if len(points) < 2:
        return "<span class='muted'>—</span>"
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1.0
    step = width / float(len(points) - 1)
    coords = []
    for i, v in enumerate(points):
        x = i * step
        y = height - ((v - lo) / span) * (height - 6) - 3
        coords.append("%.1f,%.1f" % (x, y))
    pts = " ".join(coords)
    return ("<svg width='%d' height='%d' viewBox='0 0 %d %d'>"
            "<polyline points='%s' fill='none' stroke='#4ade80' "
            "stroke-width='2'/></svg>"
            % (width, height, width, height, pts))


def _latest_price(history):
    """Most recent non-empty price list from a (ts, prices) history."""
    for _ts, prices in reversed(history):
        if prices:
            return prices
    return []


def render(store):
    targets = store.list_targets()
    changes = store.recent_changes(limit=25)
    counts = store.counts()

    rows = []
    for t in targets:
        prev = store.latest_snapshot(t["id"])
        hist = store.price_history(t["id"], limit=40)
        # one plotted value per snapshot: its max price (0 if none)
        series = [max(ps) if ps else 0 for _ts, ps in hist]
        spark = _sparkline(series) if series else "<span class='muted'>—</span>"
        status = prev["status"] if prev else "—"
        cls = prev["status_class"] if prev else ""
        last_ts = datetime.datetime.utcfromtimestamp(prev["ts"]).strftime(
            "%Y-%m-%d %H:%M") if prev else "—"
        rows.append(
            "<tr><td>%s</td><td class='url'>%s</td>"
            "<td><span class='pill %s'>%s</span></td>"
            "<td>%s</td><td>%s</td><td>%s</td></tr>"
            % (_esc(t["name"]), _esc(t["url"]), _esc(cls), _esc(status),
               spark, _esc(", ".join(str(p) for p in _latest_price(hist)) or "—"),
               last_ts))

    change_rows = []
    for c in changes:
        when = datetime.datetime.utcfromtimestamp(c["ts"]).strftime("%Y-%m-%d %H:%M")
        detail = c["detail"]
        if c["kind"] == "price":
            txt = "prices %s → %s" % (detail.get("old_prices"), detail.get("new_prices"))
        else:
            d = detail.get("diff", {})
            txt = "content changed (+%s / -%s words)" % (
                d.get("added_count", "?"), d.get("removed_count", "?"))
        change_rows.append("<li><span class='time'>%s</span> "
                           "<b>%s</b> — %s</li>"
                           % (when, _esc(c["name"] or c["target_id"]), _esc(txt)))

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return _TEMPLATE.format(
        now=_esc(now), targets=counts["targets"], snapshots=counts["snapshots"],
        changes=counts["changes"], rows="\n".join(rows) or
        "<tr><td colspan='6' class='muted'>No targets yet — "
        "<code>ghost-intel add</code></td></tr>",
        changes_list="\n".join(change_rows) or
        "<li class='muted'>No changes detected yet.</li>")


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ghost-Intel — Competitor Radar</title>
<style>
:root{{--bg:#0b1020;--card:#141b2e;--fg:#e6ebf5;--mut:#8b96ad;--acc:#4ade80;--warn:#f59e0b}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:960px;margin:0 auto;padding:32px 20px}}
h1{{font-size:22px;margin:0}} .sub{{color:var(--mut);margin:4px 0 24px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}}
.card{{background:var(--card);border-radius:12px;padding:16px 20px;min-width:140px}}
.card .n{{font-size:28px;font-weight:700}} .card .l{{color:var(--mut);font-size:12px}}
h2{{font-size:16px;margin:28px 0 12px;color:var(--fg)}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid #1f2942;font-size:14px}}
th{{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}}
.url{{color:var(--mut);font-size:12px;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.pill{{padding:2px 8px;border-radius:20px;font-size:12px}}
.pill.ok{{background:#12351f;color:var(--acc)}}
.pill.forbidden,.pill.pressure{{background:#3a2712;color:var(--warn)}}
ul{{list-style:none;padding:0;margin:0}}
li{{padding:10px 12px;border-bottom:1px solid #1f2942;background:var(--card);border-radius:8px;margin-bottom:6px;font-size:14px}}
.time{{color:var(--mut);font-size:12px;margin-right:8px}}
.muted{{color:var(--mut)}} code{{background:#1f2942;padding:1px 5px;border-radius:4px}}
footer{{color:var(--mut);font-size:12px;margin-top:32px}}
</style></head><body><div class="wrap">
<h1>Ghost-Intel — Competitor Radar</h1>
<div class="sub">Public-page surveillance · robots-respecting · free-tier runtime · rendered {now}</div>
<div class="cards">
  <div class="card"><div class="n">{targets}</div><div class="l">Targets</div></div>
  <div class="card"><div class="n">{snapshots}</div><div class="l">Snapshots</div></div>
  <div class="card"><div class="n">{changes}</div><div class="l">Changes</div></div>
</div>
<h2>Monitored targets</h2>
<table><thead><tr><th>Name</th><th>URL</th><th>Status</th><th>Price trend</th><th>Latest price</th><th>Last seen</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>Recent changes</h2>
<ul>{changes_list}</ul>
<footer>Ghost-Intel · MIT © 2026 Baran Avisat — Aodı Akdıro Suite ·
baranavisat@gmail.com · WhatsApp +90 531 326 37 22 ·
Public pages only, robots respected, no evasion by design.</footer>
</div></body></html>
"""
