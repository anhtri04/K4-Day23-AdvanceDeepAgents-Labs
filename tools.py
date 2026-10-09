"""tools.py - STUDENT IMPLEMENTS.  Source tools for the research agents.   Guide: GUIDE.md, part 1.

Rules for every tool:
  * runs on the HOST (not in the sandbox): API keys must never enter the sandbox;
  * returns a STRING (JSON text of compact records) and NEVER raises:
        "NO RESULTS"  when the source answers with nothing,
        "ERROR: ..."  when the source keeps failing after the retries (the agent then tries another source);
  * the docstring is the tool description the LLM reads: keep it precise (what it does, what it returns, when to use it).
Try your tools without any agent:   python tools.py
"""
import json
import os
import random
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import httpx
from langchain_core.tools import tool

# ---- constants (given) ----
ARXIV_URL = "https://export.arxiv.org/api/query"  # https only: http answers 301
HF_DAILY_URL = "https://huggingface.co/api/daily_papers"
HF_SEARCH_URL = "https://huggingface.co/api/papers/search"
LINKUP_SEARCH_URL = "https://api.linkup.so/v1/search"  # https://docs.linkup.so
LINKUP_FETCH_URL = "https://api.linkup.so/v1/fetch"

ATOM = "{http://www.w3.org/2005/Atom}"
RETRY_STATUS = {429, 500, 502, 503, 504}
TIMEOUT = 30.0
_last_arxiv_call = 0.0


class RetryableError(Exception):
    """Given. Raise it inside a call to ask with_retry to wait and try again (retry_after in seconds, optional)."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


# ---- TODO 1: retry helper ----
def with_retry(fn, *, attempts=5, base=1.0, cap=30.0):
    """Call fn(); when it raises RetryableError, wait and call it again.

    Exponential backoff (base * 2**attempt) with random jitter, capped at `cap`; a server-provided
    Retry-After is honoured (also capped). The last failure is re-raised without sleeping; any
    exception that is not a RetryableError propagates untouched.
    """
    for attempt in range(attempts):
        try:
            return fn()
        except RetryableError as exc:
            if attempt >= attempts - 1:
                raise
            if exc.retry_after is not None:
                delay = min(float(exc.retry_after), cap)
            else:
                backoff = base * (2 ** attempt)
                delay = min(cap, backoff + random.uniform(0, backoff * 0.1))
            time.sleep(delay)


def _retry_after(response):
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _redact(message, key):
    """Never let an API key leak into a string handed back to the agent."""
    text = str(message)
    if key:
        text = text.replace(key, "***").replace(urllib.parse.quote(key), "***")
    return text


def _linkup_key():
    return (os.getenv("LINKUP_API_KEY") or "").strip()


def _source_error(exc):
    return f"ERROR: {type(exc).__name__}: {_redact(exc, _linkup_key())}"


def _request(method, url, *, params=None, json_body=None, headers=None, retry=None):
    """One HTTP call wrapped in with_retry; Retry-After/transport errors become RetryableError."""
    retry = retry or {}

    def call():
        try:
            response = httpx.request(method, url, params=params, json=json_body, headers=headers,
                                     timeout=TIMEOUT, follow_redirects=True)
        except httpx.TransportError as exc:
            raise RetryableError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code in RETRY_STATUS:
            raise RetryableError(f"HTTP {response.status_code}", retry_after=_retry_after(response))
        response.raise_for_status()
        return response

    return with_retry(call, **retry)


# ---- TODO 2: arXiv ----
@tool
def arxiv_search(query: str, max_results: int = 10) -> str:
    """Search arXiv papers by keywords, newest first. Returns a JSON list of {id, url, published, title, summary}."""
    try:
        terms = re.findall(r"[A-Za-z0-9-]+", query or "")
        if not terms:
            return "NO RESULTS"
        search_query = " AND ".join(f"all:{term}" for term in terms[:12])
        max_results = max(1, min(int(max_results), 30))

        def call():
            # arXiv etiquette: keep >= 3s between two calls to the same endpoint.
            global _last_arxiv_call
            gap = 3.0 - (time.monotonic() - _last_arxiv_call)
            if gap > 0:
                time.sleep(gap)
            _last_arxiv_call = time.monotonic()
            resp = httpx.get(ARXIV_URL, params={
                "search_query": search_query,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": max_results,
                "start": 0,
            }, timeout=TIMEOUT, follow_redirects=True)
            if resp.status_code in RETRY_STATUS:
                raise RetryableError(f"HTTP {resp.status_code}", retry_after=_retry_after(resp))
            resp.raise_for_status()
            return resp

        # arXiv rate-limits by IP (a whole class shares one): retry longer and more often.
        resp = with_retry(call, attempts=6, cap=60.0)
        root = ET.fromstring(resp.text)
        records = []
        for entry in root.findall(f"{ATOM}entry"):
            raw_id = entry.findtext(f"{ATOM}id", default="") or ""
            if "/abs/" not in raw_id:
                continue
            arxiv_id = re.sub(r"v\d+$", "", raw_id.split("/abs/")[-1].strip())
            title = " ".join((entry.findtext(f"{ATOM}title", default="") or "").split())
            summary = " ".join((entry.findtext(f"{ATOM}summary", default="") or "").split())[:600]
            records.append({
                "id": arxiv_id,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "published": (entry.findtext(f"{ATOM}published", default="") or "")[:10],
                "title": title,
                "summary": summary,
            })
        return json.dumps(records, ensure_ascii=False) if records else "NO RESULTS"
    except Exception as exc:  # noqa: BLE001 - a tool never raises; the agent must get a string back
        return _source_error(exc)


# ---- TODO 3: Hugging Face ----
def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _hf_record(item):
    """Map one API item ({"paper": {...}, ...}) to the compact record; None when it has no paper id."""
    paper = item.get("paper") if isinstance(item, dict) else None
    if not isinstance(paper, dict):
        return None
    paper_id = paper.get("id")
    if not paper_id:
        return None
    summary = paper.get("ai_summary") or paper.get("summary") or item.get("summary") or ""
    return {
        "id": paper_id,
        "url": f"https://huggingface.co/papers/{paper_id}",
        "published": str(paper.get("publishedAt") or item.get("publishedAt") or "")[:10],
        "title": paper.get("title") or item.get("title") or "",
        "summary": " ".join(str(summary).split())[:600],
        "upvotes": _as_int(paper.get("upvotes")),
        "github": paper.get("githubRepo") or "",
        "stars": _as_int(paper.get("githubStars")),
    }


@tool
def hf_daily_papers(limit: int = 30, date: str = "", keyword: str = "") -> str:
    """Hugging Face Daily Papers = what is trending in AI research. Returns a JSON list of
    {id, url, published, title, summary, upvotes, github, stars} sorted by upvotes. `date` is YYYY-MM-DD (empty = latest).
    `keyword` filters title/summary; there is no topic search on this endpoint (use hf_search_papers for a topic)."""
    try:
        limit = max(1, min(int(limit), 100))
        params = {"limit": limit}
        if date:
            params["date"] = date
        resp = _request("GET", HF_DAILY_URL, params=params)
        data = resp.json()
        if not isinstance(data, list):
            return "NO RESULTS"
        records = [record for item in data if (record := _hf_record(item)) is not None]
        if keyword:
            needle = keyword.lower()
            records = [r for r in records if needle in f"{r['title']} {r['summary']}".lower()]
        records.sort(key=lambda r: r["upvotes"], reverse=True)
        return json.dumps(records, ensure_ascii=False) if records else "NO RESULTS"
    except Exception as exc:  # noqa: BLE001
        return _source_error(exc)


@tool
def hf_search_papers(query: str, limit: int = 10) -> str:
    """Search Hugging Face papers by topic. Returns a JSON list of
    {id, url, published, title, summary, upvotes, github, stars}."""
    try:
        limit = max(1, min(int(limit), 50))
        resp = _request("GET", HF_SEARCH_URL, params={"q": query, "limit": limit})
        data = resp.json()
        if not isinstance(data, list):
            return "NO RESULTS"
        records = [record for item in data if (record := _hf_record(item)) is not None]
        return json.dumps(records, ensure_ascii=False) if records else "NO RESULTS"
    except Exception as exc:  # noqa: BLE001
        return _source_error(exc)


# ---- TODO 4: web search / fetch through the Linkup API (plain HTTP, docs.linkup.so) ----
def _linkup_call(url, body, retry):
    """POST a JSON body to Linkup with bearer auth and return the parsed JSON object.

    Auth and rate limits are Linkup's own: 429/5xx/TransportError are retried by `_request`,
    while 400/401/402 raise immediately (fix the key/credits, retrying will not help).
    """
    key = _linkup_key()
    if not key:
        raise RuntimeError("LINKUP_API_KEY is not set (web search/fetch unavailable)")
    response = _request("POST", url, json_body=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }, retry=retry)
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(_redact(f"linkup returned non-JSON: {exc}", key)) from exc
    if not isinstance(data, dict):
        raise RuntimeError(_redact("linkup returned an unexpected payload", key))
    return data


@tool
def web_search(query: str, objective: str = "", num_results: int = 5) -> str:
    """Search the web (Linkup). Give a natural-language query describing what to find; returns the top
    results as text with their titles and URLs."""
    try:
        query = (query or "").strip()
        if not query:
            return "NO RESULTS"
        objective = (objective or "").strip()
        full_query = query if not objective else f"{query}. {objective}"
        num_results = max(1, min(int(num_results), 10))
        data = _linkup_call(LINKUP_SEARCH_URL, {
            "q": full_query,
            "depth": "standard",
            "outputType": "searchResults",
            "maxResults": num_results,
        }, retry={"attempts": 5, "cap": 60.0})
        blocks = []
        for item in data.get("results") or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            content = " ".join(str(item.get("content") or "").split())
            blocks.append(f"## {item.get('name') or 'Untitled'}\n{item['url']}\n{content[:1200]}")
        return "\n\n".join(blocks) if blocks else "NO RESULTS"
    except Exception as exc:  # noqa: BLE001
        return _source_error(exc)


@tool
def web_fetch(url: str) -> str:
    """Read the full content of one web page (e.g. an arXiv abstract page) as markdown. Long pages are truncated."""
    try:
        data = _linkup_call(LINKUP_FETCH_URL, {
            "url": url,
            "mode": "standard",
            "renderJs": True,
        }, retry={"attempts": 5, "cap": 60.0})
        text = str(data.get("markdown") or "").strip()
        return text[:12000] if text else "NO RESULTS"
    except Exception as exc:  # noqa: BLE001
        return _source_error(exc)


# ---- TODO 5: registry (the researcher subagent gets exactly these) ----
SOURCE_TOOLS = [arxiv_search, hf_daily_papers, hf_search_papers, web_search, web_fetch]


if __name__ == "__main__":
    for name, fn, args in [
        ("arxiv_search", arxiv_search, {"query": "world model", "max_results": 3}),
        ("hf_daily_papers", hf_daily_papers, {"limit": 20}),
        ("hf_search_papers", hf_search_papers, {"query": "world model", "limit": 3}),
        ("web_search", web_search, {"query": "survey paper on world models", "num_results": 2}),
        ("web_fetch", web_fetch, {"url": "https://arxiv.org/abs/1803.10122"}),
    ]:
        try:
            print(f"== {name}\n{fn.invoke(args)[:400]}\n")
        except NotImplementedError as exc:
            print(f"== {name}: not implemented yet ({exc})\n")
