"""The crawler — polite, defensive surveillance over PUBLIC pages.

One `run()` walks every target, and for each:
  1. fetch robots.txt (once per host) -> obey it; a 401/403/429 = skip host
  2. throttle (respect Crawl-delay, per-host budget)
  3. fetch the page with the truthful UA (no TLS tricks, no rotation)
  4. extract text/prices/hash; diff against the last snapshot
  5. record a snapshot + any detected change (with a timestamp)

It is a GUEST: it never logs in, never bypasses a paywall or CAPTCHA, never
rotates identity to slip past a block. A block or a 429 cools the host down.

`run()` is fully injectable (fetcher/summarizer/storage) so tests exercise the
whole pipeline offline with no network.
"""
import time

from . import extract, transport
from .robots import Robots
from .throttle import Throttle


class CrawlResult(object):
    __slots__ = ("url", "name", "status", "status_class", "changed",
                 "prices", "summary", "skipped")

    def __init__(self, url, name, status=0, status_class="", changed=False,
                 prices=None, summary="", skipped=""):
        self.url = url
        self.name = name
        self.status = status
        self.status_class = status_class
        self.changed = changed
        self.prices = prices or []
        self.summary = summary
        self.skipped = skipped          # non-empty => not fetched, why

    def as_dict(self):
        return {"url": self.url, "name": self.name, "status": self.status,
                "status_class": self.status_class, "changed": self.changed,
                "prices": self.prices, "summary": self.summary,
                "skipped": self.skipped}


class Crawler(object):
    def __init__(self, storage, summarizer=None, fetcher=None,
                 throttle=None, now=time.time):
        self.store = storage
        self.summarizer = summarizer
        self.fetch = fetcher or transport.fetch
        self.throttle = throttle or Throttle(now=now)
        self._robots = {}            # host -> Robots (cache per run)

    def _robots_for(self, base_url):
        from urllib.parse import urlparse
        host = urlparse(base_url).netloc
        if host not in self._robots:
            r = Robots(base_url, fetcher=self.fetch)
            delay = r.crawl_delay()
            if delay:
                self.throttle.set_crawl_delay(host, delay)
            self._robots[host] = r
        return self._robots[host]

    def crawl_target(self, tgt):
        url, name = tgt["url"], tgt["name"]
        from urllib.parse import urlparse
        host = urlparse(url).netloc

        robots = self._robots_for(url)
        ok, reason = robots.allowed(url)
        if not ok:
            return CrawlResult(url, name, skipped=reason)

        try:
            self.throttle.wait_for(host)
        except RuntimeError as e:
            return CrawlResult(url, name, skipped="budget:" + str(e))

        prev = self.store.latest_snapshot(tgt["id"]) if tgt.get("id") else None

        try:
            resp = self.fetch(url)
        except transport.TransportError as e:
            # network-level failure (DNS/timeout/refused): record, don't crash.
            # No rotation, no retry-with-different-identity — ever.
            if prev and prev.get("status") == 200:
                # a previously-live target just went unreachable: that IS a change
                self.store.add_change(tgt["id"], "status",
                                      {"old_status": prev.get("status"),
                                       "new_status": None,
                                       "new_status_class": "network-error"})
            return CrawlResult(url, name, status_class="network-error",
                               summary="network error: %s" % e)

        sclass = transport.status_class(resp.status)
        if sclass in ("forbidden", "pressure"):
            # blocked or rate-limited: cool the host down, do NOT evade
            self.throttle.record_block(host)

        text = extract.html_to_text(resp.body)
        prices = extract.extract_prices(text)
        chash = extract.content_hash(text) if resp.status == 200 else ""

        changed = False
        summary = ""
        detail = {}

        if resp.status != 200 and prev and prev.get("status") == 200:
            # status transition (e.g. 200 -> 403/500): the most operationally
            # important event — record it as an explicit change.
            changed = True
            detail = {"kind": "status", "old_status": prev.get("status"),
                      "new_status": resp.status, "new_status_class": sclass}
            self.store.add_change(tgt["id"], "status", detail)
            if self.summarizer:
                summary = self.summarizer.summarize(name, detail)

        if resp.status == 200 and prev and prev.get("content_hash"):
            diff = extract.diff_summary(prev.get("text", ""), text)
            price_changed = bool(prev["prices"]) and \
                sorted(prev["prices"]) != sorted(prices)
            hash_changed = prev["content_hash"] != chash
            if price_changed or hash_changed or diff["changed"]:
                changed = True
                kind = "price" if price_changed else "content"
                detail = {"kind": kind, "old_prices": prev["prices"],
                          "new_prices": prices, "hash_changed": hash_changed,
                          "price_changed": price_changed, "diff": diff}
                self.store.add_change(tgt["id"], kind, detail)
                if self.summarizer:
                    summary = self.summarizer.summarize(name, detail)

        self.store.add_snapshot(tgt["id"], resp.status, sclass, chash,
                                prices, len(text), text)
        return CrawlResult(url, name, status=resp.status, status_class=sclass,
                           changed=changed, prices=prices, summary=summary)

    def run(self, targets=None):
        """Crawl all (or provided) targets; returns list of CrawlResult."""
        targets = targets if targets is not None else self.store.list_targets()
        results = []
        for tgt in targets:
            results.append(self.crawl_target(tgt))
        return results
