"""Ghost-Intel offline test suite — zero network, injected fakes.

Pins two things:
  1. core behavior (extract/diff/throttle/storage/crawler/leads/dashboard)
  2. the COMPLIANCE contracts that keep this tool legal and publishable:
     truthful UA, robots obedience, block=>stop (never rotate), no evasion.
"""
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ghost_intel import alerts, crawler, dashboard, extract, leads, storage, throttle, transport
from ghost_intel.llm import StubSummarizer, get_summarizer
from ghost_intel.robots import Robots


def _tmp():
    return os.path.join(tempfile.mkdtemp(), "ghost.db")


class FakeResp(object):
    def __init__(self, url, status=200, body=b"", headers=None):
        self.url = url
        self.status = status
        self.body = body
        self.headers = headers or {}


def make_fetcher(mapping, calls=None):
    """Return a fetch(url,...) -> FakeResp; record calls in `calls`."""
    def _fetch(url, method="GET", timeout=25.0, headers=None, data=None):
        if calls is not None:
            calls.append(url)
        for key, resp in mapping.items():
            if key in url:
                return resp
        return FakeResp(url, 404, b"")
    return _fetch


# ------------------------------------------------------------------ transport
class TransportTests(unittest.TestCase):
    def test_ua_is_truthful_and_identifies(self):
        self.assertIn("Ghost-Intel", transport.UA)
        self.assertIn("mailto:baranavisat@gmail.com", transport.UA)

    def test_ua_not_overridable(self):
        # REAL test: call transport.fetch with a spoofing UA and assert the
        # guard raises BEFORE any network is touched (no tautology).
        def _no_net(*a, **k):
            raise AssertionError("no network may be attempted")
        import urllib.request
        orig = urllib.request.build_opener
        urllib.request.build_opener = _no_net
        try:
            with self.assertRaises(ValueError):
                transport.fetch("https://example.com/",
                                headers={"User-Agent": "Mozilla/5.0"})
        finally:
            urllib.request.build_opener = orig

    def test_scheme_allowlist_rejects_non_http(self):
        # PUBLIC web pages only: file://, ftp:// etc must be refused
        for bad in ("file:///etc/passwd", "ftp://host/x",
                    "data:text/html,hi", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                transport.fetch(bad)
        self.assertEqual(transport.check_scheme("https://ok.example/"), "https://ok.example/")

    def test_cross_host_and_downgrade_redirects_blocked(self):
        # the redirect handler must refuse to leave the checked host or to
        # downgrade https->http (both would bypass robots/throttle of a new host)
        h = transport._SameHostRedirectHandler()

        def _blocked(newurl):
            try:
                h.redirect_request(urllib.request.Request("https://a.example/page"),
                                   None, 302, "Found", {}, newurl)
            except urllib.error.HTTPError as e:
                return "blocked" in str(e.reason)
            return False

        self.assertTrue(_blocked("https://b.example/x"))       # cross-host
        self.assertTrue(_blocked("http://a.example/x"))        # https->http
        self.assertTrue(_blocked("file:///etc/passwd"))        # non-http scheme
        # same-host https->https is allowed: no "blocked" HTTPError is raised
        # (it reaches the default handler, which only fails on the fake fp)
        try:
            h.redirect_request(urllib.request.Request("https://a.example/page"),
                               None, 302, "Found", {}, "https://a.example/other")
            allowed = True
        except urllib.error.HTTPError as e:
            allowed = "blocked" not in str(e.reason)
        except AttributeError:
            allowed = True          # reached super() -> our guard passed
        self.assertTrue(allowed)

    def test_status_class_buckets(self):
        self.assertEqual(transport.status_class(200), "ok")
        self.assertEqual(transport.status_class(429), "pressure")
        self.assertEqual(transport.status_class(503), "pressure")
        self.assertEqual(transport.status_class(403), "forbidden")
        self.assertEqual(transport.status_class(401), "forbidden")
        self.assertEqual(transport.status_class(404), "client-error")
        self.assertEqual(transport.status_class(500), "server-error")


# --------------------------------------------------------------------- robots
class RobotsTests(unittest.TestCase):
    def test_absent_robots_allows(self):
        r = Robots("https://example.com", fetcher=make_fetcher(
            {"robots.txt": FakeResp("https://example.com/robots.txt", 404, b"")}))
        ok, reason = r.allowed("https://example.com/page")
        self.assertTrue(ok)
        self.assertTrue(reason.startswith("robots-"))

    def test_disallow_is_obeyed(self):
        body = b"User-agent: *\nDisallow: /private\n"
        r = Robots("https://example.com", fetcher=make_fetcher(
            {"robots.txt": FakeResp("https://example.com/robots.txt", 200, body)}))
        ok, reason = r.allowed("https://example.com/private/x")
        self.assertFalse(ok)
        self.assertEqual(reason, "robots-disallow")
        ok2, _ = r.allowed("https://example.com/public")
        self.assertTrue(ok2)

    def test_forbidden_robots_means_not_welcoming(self):
        # a 403 on robots.txt => do not fetch anything on that host
        r = Robots("https://locked.example", fetcher=make_fetcher(
            {"robots.txt": FakeResp("https://locked.example/robots.txt", 403, b"")}))
        ok, reason = r.allowed("https://locked.example/anything")
        self.assertFalse(ok)
        self.assertEqual(reason, "host-not-welcoming")

    def test_crawl_delay_parsed(self):
        body = b"User-agent: *\nCrawl-delay: 5\nDisallow: /x\n"
        r = Robots("https://example.com", fetcher=make_fetcher(
            {"robots.txt": FakeResp("https://example.com/robots.txt", 200, body)}))
        self.assertEqual(r.crawl_delay(), 5)


# ------------------------------------------------------------------- throttle
class ThrottleTests(unittest.TestCase):
    def test_respects_min_interval(self):
        clock = {"t": 0.0}
        slept = []
        import time as _t
        orig = _t.sleep
        _t.sleep = lambda s: slept.append(s)
        try:
            th = throttle.Throttle(min_interval_s=3.0, now=lambda: clock["t"])
            th.wait_for("h")
            clock["t"] = 1.0                    # only 1s elapsed
            s = th.wait_for("h")
            self.assertAlmostEqual(s, 2.0, places=2)
            self.assertTrue(slept)
        finally:
            _t.sleep = orig

    def test_crawl_delay_never_faster_than_floor(self):
        th = throttle.Throttle(min_interval_s=3.0, now=lambda: 0.0)
        th.set_crawl_delay("h", 10)
        self.assertGreaterEqual(th._delay["h"], 10)
        th.set_crawl_delay("h", 1)              # try to speed up -> ignored
        self.assertGreaterEqual(th._delay["h"], 10)

    def test_per_host_budget_exhausted_raises(self):
        th = throttle.Throttle(min_interval_s=0.0, now=lambda: 0.0)
        for _ in range(3):
            th.wait_for("h", max_per_host=3)
        with self.assertRaises(RuntimeError):
            th.wait_for("h", max_per_host=3)

    def test_record_block_cools_down(self):
        # REAL test: after a block, the NEXT visit is SKIPPED (RuntimeError),
        # not slept out for ~1h, and no identity change happens.
        clock = {"t": 1000.0}
        slept = []
        import time as _t
        orig = _t.sleep
        _t.sleep = lambda s: slept.append(s)
        try:
            th = throttle.Throttle(min_interval_s=0.0, now=lambda: clock["t"])
            th.wait_for("h")                       # a normal request
            th.record_block("h")                   # a 403/429 arrives
            self.assertGreater(th._last["h"], clock["t"])   # clock in future
            with self.assertRaises(RuntimeError):   # SKIP, don't sleep ~1h
                th.wait_for("h")
            self.assertEqual(slept, [])             # nothing was ever slept
        finally:
            _t.sleep = orig


# -------------------------------------------------------------------- extract
class ExtractTests(unittest.TestCase):
    def test_html_to_text_strips_tags_and_scripts(self):
        html = "<html><script>var x=1;</script><body><h1>Hi</h1><p>Price $50</p></body></html>"
        t = extract.html_to_text(html)
        self.assertNotIn("var x", t)
        self.assertIn("Hi", t)
        self.assertIn("$50", t)

    def test_html_to_text_survives_unclosed_script(self):
        # ReDoS guard: many unclosed <script> tags must NOT stall the regex.
        # (pre-fix this was quadratic; now we pre-check for a closing tag)
        hostile = "<script>" * 2000 + "text"
        import time as _tm
        t0 = _tm.perf_counter()
        out = extract.html_to_text(hostile)
        self.assertLess(_tm.perf_counter() - t0, 2.0)   # fast, not minutes
        self.assertIn("text", out)

    def test_extract_prices_variants(self):
        for s, expect in [("$99", 99.0), ("€1,299.00", 1299.0),
                          ("£49", 49.0), ("100 USD", 100.0), ("₺150", 150.0)]:
            self.assertIn(expect, extract.extract_prices(s))

    def test_content_hash_whitespace_insensitive(self):
        a = extract.content_hash("Hello   World")
        b = extract.content_hash("hello world")
        self.assertEqual(a, b)

    def test_diff_summary_detects_added_removed(self):
        d = extract.diff_summary("the quick brown fox", "the quick red fox")
        self.assertTrue(d["changed"])
        self.assertIn("red", d["added"])
        self.assertIn("brown", d["removed"])

    def test_extract_keywords_case_insensitive(self):
        got = extract.extract_keywords("We need PYTHON and Go developers",
                                       ["python", "rust"])
        self.assertEqual(got, ["python"])


# -------------------------------------------------------------------- storage
class StorageTests(unittest.TestCase):
    def test_add_and_list_targets(self):
        s = storage.Storage(_tmp())
        s.add_target("Acme", "https://acme.example/pricing", "price", "price,plan")
        s.add_target("Acme", "https://acme.example/pricing", "price", "price")  # upsert
        self.assertEqual(len(s.list_targets()), 1)
        s.close()

    def test_snapshot_and_latest(self):
        s = storage.Storage(_tmp())
        tid = s.add_target("A", "https://a.example")
        s.add_snapshot(tid, 200, "ok", "hash1", [10, 20], 100, "text one")
        s.add_snapshot(tid, 200, "ok", "hash2", [15, 25], 120, "text two")
        latest = s.latest_snapshot(tid)
        self.assertEqual(latest["content_hash"], "hash2")
        self.assertEqual(latest["prices"], [15, 25])
        self.assertEqual(latest["text"], "text two")
        s.close()

    def test_price_history_is_time_ordered(self):
        s = storage.Storage(_tmp())
        tid = s.add_target("A", "https://a.example")
        s.add_snapshot(tid, 200, "ok", "h", [10], 10)
        s.add_snapshot(tid, 200, "ok", "h", [20], 10)
        hist = s.price_history(tid)
        self.assertEqual([p for _t, ps in hist for p in ps], [10, 20])
        s.close()

    def test_remote_dsn_rejected(self):
        with self.assertRaises(ValueError):
            storage.Storage("postgres://user:pw@host/db")

    def test_snapshot_text_is_capped(self):
        # unbounded text would grow ~hundreds of MB/target/year; cap enforced
        s = storage.Storage(_tmp())
        tid = s.add_target("A", "https://a.example")
        huge = "x" * (storage.Storage.MAX_STORED_TEXT + 5000)
        s.add_snapshot(tid, 200, "ok", "h", [10], len(huge), huge)
        got = s.latest_snapshot(tid)["text"]
        self.assertEqual(len(got), storage.Storage.MAX_STORED_TEXT)
        s.close()

    def test_changes_roundtrip(self):
        s = storage.Storage(_tmp())
        tid = s.add_target("A", "https://a.example")
        s.add_change(tid, "price", {"old_prices": [10], "new_prices": [8]})
        ch = s.recent_changes()
        self.assertEqual(ch[0]["kind"], "price")
        self.assertEqual(ch[0]["detail"]["new_prices"], [8])
        s.close()


# -------------------------------------------------------------------- crawler
class CrawlerTests(unittest.TestCase):
    def _store_with_target(self):
        s = storage.Storage(_tmp())
        tid = s.add_target("Acme", "https://acme.example/pricing", "price")
        return s, tid

    def test_robots_disallow_skips_fetch(self):
        s, tid = self._store_with_target()
        calls = []
        fetch = make_fetcher({
            "robots.txt": FakeResp("https://acme.example/robots.txt", 200,
                                   b"User-agent: *\nDisallow: /pricing\n"),
        }, calls)
        c = crawler.Crawler(s, fetcher=fetch, throttle=throttle.Throttle(0.0))
        res = c.run()
        self.assertEqual(res[0].skipped, "robots-disallow")
        # the protected page was never requested
        self.assertFalse(any("/pricing" in u for u in calls))
        s.close()

    def test_detects_price_change_on_second_run(self):
        s, tid = self._store_with_target()
        robots = FakeResp("https://acme.example/robots.txt", 404, b"")
        page_v1 = FakeResp("https://acme.example/pricing", 200,
                           b"<html><p>Plan A $50</p></html>")
        page_v2 = FakeResp("https://acme.example/pricing", 200,
                           b"<html><p>Plan A $40</p></html>")
        c1 = crawler.Crawler(s, summarizer=StubSummarizer(),
                             fetcher=make_fetcher({"robots.txt": robots,
                                                   "pricing": page_v1}),
                             throttle=throttle.Throttle(0.0))
        r1 = c1.run()
        self.assertFalse(r1[0].changed)         # first run: baseline
        c2 = crawler.Crawler(s, summarizer=StubSummarizer(),
                             fetcher=make_fetcher({"robots.txt": robots,
                                                   "pricing": page_v2}),
                             throttle=throttle.Throttle(0.0))
        r2 = c2.run()
        self.assertTrue(r2[0].changed)
        self.assertIn(40.0, r2[0].prices)
        self.assertIn("prices changed", r2[0].summary)
        self.assertEqual(len(s.recent_changes()), 1)
        s.close()

    def test_forbidden_page_cools_host_no_rotation(self):
        s, tid = self._store_with_target()
        clock = {"t": 5000.0}
        th = throttle.Throttle(0.0, now=lambda: clock["t"])
        robots = FakeResp("https://acme.example/robots.txt", 404, b"")
        page = FakeResp("https://acme.example/pricing", 403, b"")
        c = crawler.Crawler(s, fetcher=make_fetcher({"robots.txt": robots,
                                                     "pricing": page}),
                            throttle=th)
        r = c.run()
        self.assertEqual(r[0].status_class, "forbidden")
        # the host's next-allowed time is pushed into the FUTURE (cool-down),
        # proving record_block ran against a known clock (not a tautology)
        self.assertGreater(th._last["acme.example"], clock["t"])
        # a second target on the SAME host is now SKIPPED (block => stop),
        # not slept for ~1h and never rotated
        c2 = crawler.Crawler(s, fetcher=make_fetcher({"robots.txt": robots,
                                                      "pricing": page}),
                             throttle=th)
        r2 = c2.run()
        self.assertIn("cooling down", r2[0].skipped)
        s.close()

    def test_status_transition_recorded_as_change(self):
        # 200 -> 403 is the most operationally important event: it must be an
        # explicit change row, surfaced to the digest.
        s, tid = self._store_with_target()
        robots = FakeResp("https://acme.example/robots.txt", 404, b"")
        page_ok = FakeResp("https://acme.example/pricing", 200,
                           b"<html><p>Plan A $50</p></html>")
        page_block = FakeResp("https://acme.example/pricing", 403, b"")
        c1 = crawler.Crawler(s, summarizer=StubSummarizer(),
                             fetcher=make_fetcher({"robots.txt": robots,
                                                   "pricing": page_ok}),
                             throttle=throttle.Throttle(0.0))
        c1.run()
        clock = {"t": 100.0}
        th = throttle.Throttle(0.0, now=lambda: clock["t"])
        c2 = crawler.Crawler(s, summarizer=StubSummarizer(),
                             fetcher=make_fetcher({"robots.txt": robots,
                                                   "pricing": page_block}),
                             throttle=th)
        r2 = c2.run()
        self.assertTrue(r2[0].changed)
        self.assertIn("status changed", r2[0].summary)
        kinds = [c["kind"] for c in s.recent_changes()]
        self.assertIn("status", kinds)
        s.close()

    def test_summary_stub_deterministic(self):
        st = StubSummarizer()
        out = st.summarize("Acme", {"kind": "price", "old_prices": [10],
                                    "new_prices": [8]})
        self.assertEqual(out, "Acme: prices changed [10] -> [8]")


# ---------------------------------------------------------------------- leads
class LeadsTests(unittest.TestCase):
    FEED = (b"<?xml version='1.0'?><rss><channel>"
            b"<item><title>Need Python developer for API</title>"
            b"<description>Looking for python + fastapi</description></item>"
            b"<item><title>Graphic designer wanted</title>"
            b"<description>Design work</description></item>"
            b"</channel></rss>")

    def test_matches_keywords_only(self):
        scanner = leads.LeadsScanner(["python"],
                                     fetcher=make_fetcher({"feed": FakeResp(
                                         "https://boards.example/feed", 200, self.FEED)}),
                                     throttle=throttle.Throttle(0.0))
        found, notes = scanner.scan_all(["https://boards.example/feed"])
        self.assertEqual(len(found), 1)
        self.assertIn("python", found[0].matched)
        self.assertIn("Python", found[0].title)

    def test_robots_disallow_skips_feed(self):
        scanner = leads.LeadsScanner(["python"], fetcher=make_fetcher({
            "robots.txt": FakeResp("https://boards.example/robots.txt", 200,
                                   b"User-agent: *\nDisallow: /feed\n"),
        }), throttle=throttle.Throttle(0.0))
        found, notes = scanner.scan_all(["https://boards.example/feed"])
        self.assertEqual(found, [])
        self.assertEqual(notes[0]["note"], "robots-disallow")


# --------------------------------------------------------------------- digest
class DigestTests(unittest.TestCase):
    def test_build_digest_reports_counts(self):
        r1 = crawler.CrawlResult("https://a", "Acme", status=200,
                                 status_class="ok", changed=True,
                                 prices=[40], summary="Acme: cut price")
        r2 = crawler.CrawlResult("https://b", "Beta", skipped="robots-disallow")
        text = alerts.build_digest([r1, r2], [], when=None)
        self.assertIn("Scanned 1 target(s)", text)   # skipped excluded from scanned
        self.assertIn("1 skipped", text)             # but reported as skipped
        self.assertIn("Acme: cut price", text)
        self.assertIn("robots-disallow", text)

    def test_send_without_token_fails_closed(self):
        os.environ.pop("TELEGRAM_TOKEN", None)
        res = alerts.send("hello")
        self.assertFalse(res["ok"])

    def test_telegram_opener_refuses_ambient_proxies(self):
        # the bot token must never ride an OS-configured proxy. We set an
        # ambient proxy env var, prove a default opener WOULD honor it, then
        # prove the alerts opener does NOT (deterministic on any host).
        import urllib.request
        os.environ["HTTPS_PROXY"] = "http://proxy.invalid:8080"
        try:
            ref = urllib.request.build_opener()      # honors ambient proxy
            self.assertTrue(any(
                isinstance(h, urllib.request.ProxyHandler) and
                getattr(h, "proxies", {})
                for h in ref.handlers))
            # the Telegram opener must carry NO ambient-proxy handler at all
            ambient = [h for h in alerts._OPENER.handlers
                       if isinstance(h, urllib.request.ProxyHandler)
                       and getattr(h, "proxies", {})]
            self.assertEqual(ambient, [])
        finally:
            os.environ.pop("HTTPS_PROXY", None)


# ------------------------------------------------------------------- dashboard
class DashboardTests(unittest.TestCase):
    def test_render_produces_html(self):
        s = storage.Storage(_tmp())
        tid = s.add_target("Acme", "https://acme.example/pricing", "price")
        s.add_snapshot(tid, 200, "ok", "h", [50, 100], 100, "text")
        s.add_snapshot(tid, 200, "ok", "h", [45, 90], 100, "text2")  # 2nd point
        s.add_change(tid, "price", {"old_prices": [50], "new_prices": [40]})
        out = dashboard.render(s)
        self.assertIn("Ghost-Intel", out)
        self.assertIn("Acme", out)
        self.assertIn("<svg", out)               # price sparkline drawn
        s.close()


# ------------------------------------------------------------------- llm wiring
class LLMTests(unittest.TestCase):
    def test_get_summarizer_defaults_stub(self):
        os.environ["GHOST_AI"] = "stub"
        self.assertEqual(get_summarizer().name, "stub")

    def test_stub_summarizer_offline(self):
        s = get_summarizer("stub")
        self.assertTrue(s.summarize("X", {"kind": "content"}))


if __name__ == "__main__":
    unittest.main()
