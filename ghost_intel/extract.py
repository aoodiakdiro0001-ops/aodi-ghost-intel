"""Content extraction + normalization + change detection (stdlib-only).

Given an HTML/text body, Ghost-Intel:
  * strips scripts/styles/tags -> readable text
  * detects prices ($/€/£ + digits, and common EUR/USD patterns)
  * extracts a stable content HASH for change detection
  * computes a word-level diff summary between two versions

Everything is deterministic and offline — no network, no AI required. The AI
layer (llm.py) is an OPTIONAL summarizer on top of these hard facts.
"""
import hashlib
import re

# price patterns: $99, €1,299.00, £49, 1999₺, USD 100, 100 USD
_PRICE_RE = re.compile(
    r"(?P<pre>[$€£₹₺])\s?(?P<num>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"|(?P<num2>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s?(?P<post>[$€£₹₺]|USD|EUR|GBP|TRY)"
)
_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.I | re.S)
# ReDoS guard: an unclosed <script>/<style> tag makes the backreference regex
# scan to EOF for every start position (quadratic blowup on a 2 MiB page). We
# only apply the regex when a matching close tag is actually present.
_SCRIPT_TAGS = ("script", "style", "noscript")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[a-zA-Z0-9\u0600-\u06FF]{2,}")


def html_to_text(html):
    """Reduce an HTML document to readable, whitespace-normalized text."""
    if html is None:
        return ""
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    low = html.lower()
    for tag in _SCRIPT_TAGS:
        # only strip when BOTH an open and a close tag exist (avoids ReDoS)
        if "<" + tag in low and "</" + tag in low:
            html = re.sub(r"<%s[^>]*>.*?</%s>" % (tag, tag), " ", html,
                          flags=re.I | re.S)
    t = _TAG_RE.sub(" ", html)
    t = _WS_RE.sub(" ", t).strip()
    return t


def content_hash(text):
    """Stable SHA-256 over normalized text (whitespace-insensitive)."""
    norm = _WS_RE.sub(" ", (text or "").strip().lower())
    return hashlib.sha256(norm.encode("utf-8", "replace")).hexdigest()


def _parse_num(s):
    """Normalize a localized number to float.

    Rules (tested): a comma with exactly 3 digits after it and no dot present
    is a thousands separator ('1,299' -> 1299). A comma followed by 1-2 digits
    is a decimal separator ('12,5' -> 12.5). A dot is always decimal.
    """
    s = s.strip()
    if "," in s and "." not in s:
        head, _, tail = s.rpartition(",")
        if len(tail) == 3 and head:
            s = s.replace(",", "")           # thousands: 1,299 -> 1299
        else:
            s = s.replace(",", ".")          # decimal: 12,5 -> 12.5
    else:
        s = s.replace(",", "")               # drop stray commas otherwise
    try:
        return float(s)
    except ValueError:
        return None


def extract_prices(text):
    """Return a sorted list of unique numeric prices found in text."""
    out = set()
    for m in _PRICE_RE.finditer(text or ""):
        raw = m.group("num") or m.group("num2")
        if not raw:
            continue
        val = _parse_num(raw)
        if val is not None and val > 0:
            out.add(round(val, 2))
    return sorted(out)


def extract_keywords(text, kws):
    """Case-insensitive presence check for a configured keyword list."""
    low = (text or "").lower()
    return [k for k in (kws or []) if k.lower() in low]


def _words(text):
    return _WORD_RE.findall((text or "").lower())


def diff_summary(old_text, new_text, max_items=12):
    """Word-level diff: returns {added:[...], removed:[...], changed:bool}."""
    old_w = set(_words(old_text))
    new_w = set(_words(new_text))
    added = sorted(new_w - old_w)[:max_items]
    removed = sorted(old_w - new_w)[:max_items]
    return {
        "added": added,
        "removed": removed,
        "changed": content_hash(old_text) != content_hash(new_text),
        "added_count": len(new_w - old_w),
        "removed_count": len(old_w - new_w),
    }
