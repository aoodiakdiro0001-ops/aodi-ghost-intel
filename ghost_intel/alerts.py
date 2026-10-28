"""Telegram alert gateway + report builder (stdlib-only, hardened).

Sends a daily digest and instant change alerts. Same hardening as Nexus-Zero:
  * constant-time webhook-style secret compare is NOT needed here (we only
    SEND), but the token is redacted from any error string
  * outbound reads are capped; no redirect following that could leak the token
  * messages are size-clamped to Telegram's 4096-char limit

The report builder turns crawl results + recent changes into a readable digest
— offline and deterministic (no AI required).
"""
import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
API = "https://api.telegram.org/bot%s"
MAX_READ = 1 << 20


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, "redirect blocked", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _redact(msg):
    v = os.environ.get("TELEGRAM_TOKEN")
    if v:
        msg = msg.replace(v, "***")
    return msg[:160]


def _int_env(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


TIMEOUT = max(1, min(_int_env("GHOST_TG_TIMEOUT", 30), 120))


def send(text, chat_id=None):
    """Send a Telegram message; returns {ok:bool} (+err redacted on failure)."""
    if not TOKEN:
        return {"ok": False, "err": "TELEGRAM_TOKEN not set"}
    url = (API % TOKEN) + "/sendMessage"
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id or CHAT_ID, "text": str(text)[:4096]}).encode("utf-8")
    req = urllib.request.Request(url, data=payload)
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as r:
            return json.loads(r.read(MAX_READ).decode("utf-8"))
    except Exception as e:
        return {"ok": False, "err": _redact(str(e))}


def build_digest(results, changes, when=None):
    """Render a readable morning digest from crawl results + recent changes."""
    when = when or datetime.datetime.utcnow()
    lines = ["Ghost-Intel digest — %s UTC" % when.strftime("%Y-%m-%d %H:%M")]
    scanned = [r for r in results if not r.skipped]
    skipped = [r for r in results if r.skipped]
    changed = [r for r in scanned if r.changed]
    ok = [r for r in scanned if r.status_class == "ok"]
    lines.append("Scanned %d target(s): %d ok, %d changed, %d skipped"
                 % (len(scanned), len(ok), len(changed), len(skipped)))
    if changed:
        lines.append("")
        lines.append("CHANGES:")
        for r in changed[:10]:
            label = r.summary or ("%s prices now %s" % (r.name, r.prices)
                                  if r.prices else r.name + ": content changed")
            lines.append("  • " + label)
    else:
        lines.append("No monitored changes since the last run.")
    if skipped:
        lines.append("")
        lines.append("Skipped (politeness/robots): " +
                     ", ".join("%s=%s" % (r.name, r.skipped) for r in skipped[:5]))
    return "\n".join(lines)
