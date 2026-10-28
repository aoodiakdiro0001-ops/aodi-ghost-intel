"""Optional AI summarization layer (Groq free tier / Ollama / stub).

Ghost-Intel's findings are HARD FACTS produced offline by extract.py (hashes,
prices, word diffs). This module is an OPTIONAL natural-language layer that
turns those facts into a one-line human summary. The pipeline NEVER depends on
it: if the LLM is unreachable, the raw facts are still delivered.

Contracts (pinned by tests):
  * no phone-home unless a backend is explicitly configured
  * provider error text is redacted before it can reach a log or a reply
  * a rule-based stub is the deterministic fallback (offline + tests)
"""
import json
import os
import urllib.request


def _redact(msg):
    for k in ("GROQ_API_KEY", "HF_TOKEN"):
        v = os.environ.get(k)
        if v:
            msg = msg.replace(v, "***")
    return msg[:200]


class StubSummarizer(object):
    """Deterministic, offline, zero-network. Used in tests and as fallback."""
    name = "stub"

    def summarize(self, target_name, change):
        kind = change.get("kind", "content")
        if kind == "price":
            old = change.get("old_prices", [])
            new = change.get("new_prices", [])
            return "%s: prices changed %s -> %s" % (target_name, old, new)
        if kind == "status":
            return "%s: HTTP status changed to %s" % (
                target_name, change.get("new_status"))
        added = change.get("diff", {}).get("added", [])[:6]
        removed = change.get("diff", {}).get("removed", [])[:6]
        bits = []
        if added:
            bits.append("added: " + ", ".join(added))
        if removed:
            bits.append("removed: " + ", ".join(removed))
        return "%s: content changed (%s)" % (
            target_name, "; ".join(bits) if bits else "details in dashboard")


class GroqSummarizer(object):
    """Groq free-tier chat model. Requires GROQ_API_KEY."""
    name = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model=None, timeout=30.0):
        self.model = model or os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
        self.timeout = timeout
        self.key = os.environ.get("GROQ_API_KEY", "")

    def summarize(self, target_name, change):
        if not self.key:
            return StubSummarizer().summarize(target_name, change)
        prompt = ("Summarize this competitor-intel change in ONE concise line "
                  "for a busy founder. Target: %s. Change JSON: %s"
                  % (target_name, json.dumps(change, ensure_ascii=False)[:800]))
        try:
            body = json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 120, "temperature": 0.2,
            }).encode("utf-8")
            req = urllib.request.Request(
                self.URL, data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + self.key})
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=self.timeout) as r:
                data = json.loads(r.read(1 << 20).decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            # fail open to the stub, but never leak the key
            return "(AI offline) " + _redact(str(e))


class OllamaSummarizer(object):
    """Local Ollama model. Requires OLLAMA_URL (default localhost:11434)."""
    name = "ollama"

    def __init__(self, model=None, timeout=60.0):
        base = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        self.url = base.rstrip("/") + "/api/generate"
        self.model = model or os.environ.get("OLLAMA_MODEL", "llama3.2")
        self.timeout = timeout

    def summarize(self, target_name, change):
        prompt = ("One-line competitor-intel summary. Target: %s. Change: %s"
                  % (target_name, json.dumps(change, ensure_ascii=False)[:800]))
        try:
            body = json.dumps({"model": self.model, "prompt": prompt,
                               "stream": False}).encode("utf-8")
            req = urllib.request.Request(
                self.url, data=body, method="POST",
                headers={"Content-Type": "application/json"})
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=self.timeout) as r:
                data = json.loads(r.read(1 << 20).decode("utf-8"))
            return (data.get("response") or "").strip() or \
                StubSummarizer().summarize(target_name, change)
        except Exception:
            return StubSummarizer().summarize(target_name, change)


def get_summarizer(kind=None):
    kind = (kind or os.environ.get("GHOST_AI", "stub")).lower()
    if kind == "groq":
        return GroqSummarizer()
    if kind == "ollama":
        return OllamaSummarizer()
    return StubSummarizer()
