"""Honest HTTP transport for Ghost-Intel.

Non-negotiable design contracts (every test pins them):
  * a single, truthful User-Agent that identifies the tool + a contact + a repo
  * NO TLS/JA3/JA4 fingerprint crafting, NO header stripping, NO browser-mimic
  * NO identity rotation to slip past a block — a block is a STOP signal
  * ambient system proxies are refused (receipts must reflect the real path)
  * non-2xx responses are DATA, not exceptions — the pipeline wants status codes
  * reads are capped; a hostile page can't exhaust memory

The bright line: Ghost-Intel observes PUBLIC pages politely. It never logs in,
never solves/bypasses a CAPTCHA, never touches a paywall/auth wall, and never
rotates identity to defeat a block. Those are out of scope BY DESIGN, not by
accident — see SECURITY.md.
"""
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

# Truthful, self-identifying UA. Not a browser, never claimed to be one.
UA = ("Ghost-Intel/1.0 "
      "(+https://github.com/aoodiakdiro0001-ops/aodi-ghost-intel; "
      "mailto:baranavisat@gmail.com)")

BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.7",
    "Accept-Language": "en",
}

_MAX_BODY = 2 * 1024 * 1024   # 2 MiB hard cap per response


class TransportError(Exception):
    """Network-level failure (DNS, refused, timeout) — retryable class."""


class Response(object):
    __slots__ = ("url", "status", "headers", "body", "elapsed_ms")

    def __init__(self, url, status, headers, body, elapsed_ms):
        self.url = url
        self.status = status
        self.headers = headers          # dict, original case preserved
        self.body = body                # bytes (capped)
        self.elapsed_ms = elapsed_ms


ALLOWED_SCHEMES = ("http", "https")   # PUBLIC web pages only — no file://, no ftp://


def check_scheme(url):
    """Reject anything that isn't a PUBLIC web page (finding: scheme allowlist).

    Enforces the 'public pages only' bright line at the lowest layer, so a
    `file://`, `ftp://`, or other scheme can never reach the network stack
    (which would otherwise allow reading local files into the store).
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError("only public http/https URLs are allowed, got %r" % (url,))
    return url


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse cross-host and https->http redirects (defense in depth).

    A robots-checked, throttled target must not be able to bounce the truthful
    UA fetch to a DIFFERENT host whose robots.txt was never consulted and whose
    throttle bucket is never used. Same-host, http/https-preserving redirects
    are followed; anything else is stopped.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = urllib.parse.urlparse(newurl)
        old = urllib.parse.urlparse(req.full_url)
        if new.scheme.lower() not in ALLOWED_SCHEMES:
            raise urllib.error.HTTPError(newurl, code, "redirect to non-http scheme blocked", headers, fp)
        if new.netloc.lower() != old.netloc.lower():
            raise urllib.error.HTTPError(newurl, code, "cross-host redirect blocked", headers, fp)
        if old.scheme.lower() == "https" and new.scheme.lower() == "http":
            raise urllib.error.HTTPError(newurl, code, "https->http downgrade blocked", headers, fp)
        return super(_SameHostRedirectHandler, self).redirect_request(
            req, fp, code, msg, headers, newurl)


def fetch(url, method="GET", timeout=25.0, headers=None, data=None):
    """Always returns a Response; raises TransportError on network failure.

    HTTP error codes (403/404/429/500...) are returned as Response objects so
    the crawler can record them as observations instead of crashing.

    Enforces the bright lines: truthful non-overridable UA, scheme allowlist
    (http/https only), no ambient proxies, and same-host-only redirects.
    """
    check_scheme(url)                                   # PUBLIC web pages only
    h = dict(BASE_HEADERS)
    if headers:
        for k, v in headers.items():
            if k.lower() == "user-agent":
                # refuse impersonation: the tool must stay identifiable
                raise ValueError("User-Agent is not overridable in this build")
            h[k] = v
    # refuse ambient system proxies so the receipt reflects the real network
    # path, and constrain redirects so they can't hop to an unchecked host.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _SameHostRedirectHandler())
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    t0 = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(_MAX_BODY)
            return Response(r.geturl(), r.status, dict(r.headers.items()), body,
                            (time.perf_counter() - t0) * 1000.0)
    except urllib.error.HTTPError as e:
        body = e.read(_MAX_BODY)
        return Response(e.url, e.code, dict(e.headers.items()), body,
                        (time.perf_counter() - t0) * 1000.0)
    except urllib.error.URLError as e:
        raise TransportError(str(getattr(e, "reason", e)))
    except (socket.timeout, OSError) as e:
        raise TransportError(str(e))


def status_class(status):
    """Bucket an HTTP status for reporting + backoff decisions."""
    if 200 <= status < 300:
        return "ok"
    if status == 429 or status == 503:
        return "pressure"        # slow down; NEVER rotate identity here
    if status in (401, 403):
        return "forbidden"       # auth/paywall/blocked -> do NOT evade, just stop
    if 400 <= status < 500:
        return "client-error"
    return "server-error"
