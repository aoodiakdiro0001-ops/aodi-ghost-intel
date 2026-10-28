"""robots.txt handling — a guest obeys the house rules.

Absent robots.txt = allow (standard robots convention). But an HTTP
401/403/429 on robots.txt itself is a STOP signal: we treat it as 'this host
does not welcome automated access' and do NOT fetch anything else there.

Reuses the transport's truthful User-Agent so can_fetch() is evaluated against
the SAME identity we present.
"""
import urllib.parse
import urllib.robotparser

from . import transport


class Robots(object):
    """Fetch + comprehend robots.txt once per host."""

    def __init__(self, base_url, timeout=20.0, fetcher=None):
        fetcher = fetcher or transport.fetch
        parsed = urllib.parse.urlparse(base_url)
        self.host = parsed.netloc
        self.agent = transport.UA.split("(")[0].strip()   # "Ghost-Intel/1.0"
        self.available = False
        self.status = ""          # "200" | "403" | "unreachable" | "not-robots"
        self.welcomes = True      # False only on explicit auth/pressure signal
        self._rp = None
        robots_url = parsed._replace(path="/robots.txt", query="",
                                     fragment="").geturl()
        try:
            resp = fetcher(robots_url, timeout=timeout)
        except transport.TransportError:
            self.status = "unreachable"
            return
        self.status = str(resp.status)
        if resp.status in (401, 403, 429, 503):
            # explicit "not welcome" — stop, don't probe further
            self.welcomes = False
            return
        if resp.status != 200:
            return                      # 404 etc: treat as no robots -> allow
        try:
            text = resp.body.decode("utf-8", "replace")
        except Exception:
            return
        if "user-agent" not in text.lower():
            self.status = "not-robots"  # e.g. an SPA error page
            return
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(text.splitlines())
        self._rp = rp
        self.available = True

    def crawl_delay(self):
        if self._rp is None:
            return None
        try:
            return self._rp.crawl_delay(self.agent)
        except Exception:
            return None

    def allowed(self, url):
        """Returns (ok, reason). ok=False means DO NOT fetch this URL."""
        if not self.welcomes:
            return False, "host-not-welcoming"
        if self._rp is None:
            return True, "robots-" + self.status
        try:
            ok = self._rp.can_fetch(self.agent, url)
        except Exception:
            ok = True
        return ok, ("robots-allow" if ok else "robots-disallow")
