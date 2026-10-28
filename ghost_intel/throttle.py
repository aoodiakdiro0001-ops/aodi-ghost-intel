"""Per-host politeness throttle.

Ghost-Intel is a GUEST on every host it visits. This module enforces:
  * a minimum interval between requests to the SAME host (default 3s)
  * honoring robots.txt ``Crawl-delay`` when present (raised, never lowered)
  * a per-run cap on requests per host (no crawl storms)

Bright line: an HTTP 429/403 is a STOP signal for that host, not a cue to
rotate identity or change fingerprints. We back off, we do not evade.
"""
import time

DEFAULT_MIN_INTERVAL_S = 3.0
DEFAULT_MAX_PER_HOST = 20          # per run; generous but bounded


class Throttle(object):
    """Tracks last-request time + crawl-delay per host."""

    def __init__(self, min_interval_s=DEFAULT_MIN_INTERVAL_S, now=time.time):
        self.min_interval = float(min_interval_s)
        self._last = {}             # host -> monotonic ts
        self._delay = {}            # host -> crawl-delay seconds (from robots)
        self._count = {}            # host -> requests this run
        self._now = now             # injectable clock (tests)

    def set_crawl_delay(self, host, delay_s):
        """robots.txt asked for a slower cadence; adopt the SLOWER value."""
        try:
            d = float(delay_s)
        except (TypeError, ValueError):
            return
        if d > 0:
            # never go faster than our own floor
            self._delay[host] = max(self._delay.get(host, 0.0), d,
                                    self.min_interval)

    def remaining_for_host(self, host, max_per_host=DEFAULT_MAX_PER_HOST):
        return max_per_host - self._count.get(host, 0)

    def wait_for(self, host, max_per_host=DEFAULT_MAX_PER_HOST):
        """Block until it's polite to hit `host`. Returns seconds slept.

        Raises RuntimeError when the host must NOT be visited right now:
          * the per-run per-host request budget is exhausted, or
          * the host is in an active cool-down after a block (401/403/429/503).

        The cool-down check is deliberate: a blocked host is a STOP signal, so
        we SKIP it (fast, no evasion) rather than sleeping out a ~1h penalty.
        The caller turns the RuntimeError into ``skipped=...``.
        """
        if self._count.get(host, 0) >= max_per_host:
            raise RuntimeError("per-host budget exhausted for %s" % host)
        interval = max(self.min_interval, self._delay.get(host, 0.0))
        now = self._now()
        last = self._last.get(host)
        if last is not None and last > now:
            # cool-down armed by record_block(): the clock is in the future.
            # SKIP immediately — never sleep, never rotate, never evade.
            raise RuntimeError("host cooling down after block: %s" % host)
        sleep_s = 0.0
        if last is not None and now - last < interval:
            sleep_s = interval - (now - last)
            time.sleep(sleep_s)
        self._last[host] = self._now()
        self._count[host] = self._count.get(host, 0) + 1
        return sleep_s

    def record_block(self, host):
        """A 429/403 arrived: mark the host as cooled-down (no rotation)."""
        # push the next allowed request far out into the FUTURE. The next
        # wait_for() will see last > now and SKIP this host (no long sleep, no
        # identity change). We do NOT change UA, IP, or route.
        self._last[host] = self._now() + 3600.0
