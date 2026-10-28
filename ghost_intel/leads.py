"""Leads scanner — find public job posts matching your service keywords.

Reads a configurable list of PUBLIC listing URLs (RSS/HTML) and flags posts
whose title/description contain your keywords. This is a LEAD HINT, not a
guaranteed client — the report says so honestly.

Defensive posture (same as the crawler):
  * robots.txt respected; a 401/403/429 = skip, never evade
  * truthful User-Agent, capped reads, real rate limits
  * public pages only — no login walls, no scraping behind auth

Injectable fetcher => fully offline-testable.
"""
import re
import xml.etree.ElementTree as ET

from . import extract, transport
from .robots import Robots
from .throttle import Throttle

_ITEM_RE = re.compile(r"<item[\s>].*?</item>", re.I | re.S)
_TITLE_RE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.I | re.S)
_DESC_RE = re.compile(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", re.I | re.S)


def _parse_feed(text):
    """Best-effort RSS/Atom item extraction (tolerant of messy feeds)."""
    items = []
    for blk in _ITEM_RE.findall(text):
        t = _TITLE_RE.search(blk)
        d = _DESC_RE.search(blk)
        items.append({
            "title": extract.html_to_text(t.group(1)) if t else "",
            "description": extract.html_to_text(d.group(1)) if d else "",
        })
    if items:
        return items
    # fallback: try strict XML parse
    try:
        root = ET.fromstring(text)
        for it in root.iter():
            if it.tag.lower().endswith("item") or it.tag.lower().endswith("entry"):
                title = desc = ""
                for child in it:
                    tag = child.tag.lower()
                    if tag.endswith("title"):
                        title = (child.text or "").strip()
                    elif tag.endswith("description") or tag.endswith("summary"):
                        desc = (child.text or "").strip()
                items.append({"title": title, "description": desc})
    except Exception:
        pass
    return items


class Lead(object):
    __slots__ = ("title", "source", "matched")

    def __init__(self, title, source, matched):
        self.title = title
        self.source = source
        self.matched = matched

    def as_dict(self):
        return {"title": self.title, "source": self.source,
                "matched": self.matched}


class LeadsScanner(object):
    def __init__(self, keywords, fetcher=None, throttle=None):
        self.keywords = [k for k in (keywords or []) if k]
        self.fetch = fetcher or transport.fetch
        self.throttle = throttle or Throttle()
        self._robots = {}

    def _allowed(self, url):
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        if host not in self._robots:
            r = Robots(url, fetcher=self.fetch)
            # honor robots Crawl-delay (raise-only), same contract as crawler
            delay = r.crawl_delay()
            if delay:
                self.throttle.set_crawl_delay(host, delay)
            self._robots[host] = r
        ok, reason = self._robots[host].allowed(url)
        return ok, reason

    def scan_feed(self, feed_url):
        """Fetch one public feed and return matching leads (empty on skip)."""
        ok, reason = self._allowed(feed_url)
        if not ok:
            return [], reason
        from urllib.parse import urlparse
        host = urlparse(feed_url).netloc
        try:
            self.throttle.wait_for(host)
        except RuntimeError as e:
            return [], "budget:" + str(e)
        try:
            resp = self.fetch(feed_url)
        except transport.TransportError:
            return [], "network-error"
        if resp.status != 200:
            if transport.status_class(resp.status) in ("forbidden", "pressure"):
                # block/429 on the feed itself: cool the host down, no evasion
                self.throttle.record_block(host)
            return [], "http-%d" % resp.status
        text = resp.body.decode("utf-8", "replace")
        leads = []
        for it in _parse_feed(text):
            blob = (it["title"] + " " + it["description"]).lower()
            matched = [k for k in self.keywords if k.lower() in blob]
            if matched:
                leads.append(Lead(it["title"][:160], feed_url, matched))
        return leads, "ok"

    def scan_all(self, feed_urls):
        all_leads = []
        notes = []
        for u in feed_urls:
            leads, note = self.scan_feed(u)
            all_leads.extend(leads)
            notes.append({"url": u, "note": note, "count": len(leads)})
        return all_leads, notes
