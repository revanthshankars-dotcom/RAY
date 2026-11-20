"""Real web access.

* Search uses DuckDuckGo's HTML endpoint (no API key, no account).
* Pages are fetched and reduced to readable text locally.
* Research answers come from the AI providers but are always returned with the
  URLs they were given; if fetching fails, RAY says so instead of inventing
  sources.

Web content is untrusted input.  It is never executed, never treated as
instructions, and is truncated before it reaches a model.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

from ..core.logging_setup import get_logger

log = get_logger("browser.search")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 RAY/0.1"
)
SEARCH_ENDPOINTS = [
    "https://html.duckduckgo.com/html/",
    "https://lite.duckduckgo.com/lite/",
]

# --------------------------------------------------------------- fetch policy
# RAY fetches on behalf of a model, so every URL is hostile input: it may point
# at loopback, the LAN, a router admin page, a cloud metadata service or a
# ``file://`` path.  Fetching is therefore restricted to public http(s) targets
# and every redirect hop is re-checked (a redirect is just a second URL).
MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 3 * 1024 * 1024
BLOCKED_HOST_SUFFIXES = (
    ".localhost", ".local", ".localdomain", ".internal", ".intranet",
    ".home.arpa", ".lan", ".corp", ".private",
)


class UnsafeTarget(ValueError):
    """Raised when a URL would make RAY fetch something it should not."""


def _is_private_address(raw: str) -> bool:
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def check_fetch_target(url: str, allow_private: bool = False) -> str:
    """Validate one URL before RAY opens a socket to it.

    Returns the URL to fetch (``https://`` is assumed when no scheme is given).
    Raises :class:`UnsafeTarget` for anything that is not a public http(s) URL.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise UnsafeTarget("no URL given")
    if "://" not in candidate:
        candidate = "https://" + candidate.lstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeTarget(f"{parsed.scheme or 'that'} URLs are not fetched (http and https only)")
    if parsed.username or parsed.password:
        raise UnsafeTarget("URLs with embedded credentials are refused")
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise UnsafeTarget("URL has no host")
    if allow_private:
        return candidate
    if host in ("localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"):
        raise UnsafeTarget("RAY does not fetch loopback addresses")
    if host.endswith(BLOCKED_HOST_SUFFIXES):
        raise UnsafeTarget(f"RAY does not fetch private network names ({host})")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if _is_private_address(str(literal)):
            raise UnsafeTarget(f"RAY does not fetch private or local addresses ({host})")
        return candidate
    if "." not in host:
        # Single-label names resolve through local search domains (intranets,
        # routers, printer consoles) - never a public web page.
        raise UnsafeTarget(f"RAY does not fetch intranet hostnames ({host})")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return candidate  # offline or bad DNS: let httpx report the failure
    for info in infos:
        address = info[4][0]
        if _is_private_address(address):
            raise UnsafeTarget(f"{host} resolves to a private address ({address})")
    return candidate


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = "web"

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet, "source": self.source}


@dataclass
class Page:
    url: str
    title: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    status: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "ok": self.ok,
            "error": self.error,
            "status": self.status,
            "chars": len(self.text),
        }


class _DuckDuckGoParser(HTMLParser):
    """Parses both the html.duckduckgo.com and lite.duckduckgo.com layouts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._in_link = False
        self._in_snippet = False
        self._current: dict[str, str] = {}
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        classes = attributes.get("class", "")
        if tag == "a" and ("result__a" in classes or "result-link" in classes):
            self._in_link = True
            self._buffer = []
            self._current = {"url": _clean_ddg_url(attributes.get("href", ""))}
        elif ("result__snippet" in classes or "result-snippet" in classes) and tag in ("a", "td", "div", "span"):
            self._in_snippet = True
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if self._in_link and tag == "a":
            self._in_link = False
            title = " ".join("".join(self._buffer).split())
            url = self._current.get("url", "")
            if title and url:
                self.results.append(SearchResult(title=title, url=url))
        elif self._in_snippet and tag in ("a", "td", "div", "span"):
            self._in_snippet = False
            snippet = " ".join("".join(self._buffer).split())
            if snippet and self.results and not self.results[-1].snippet:
                self.results[-1].snippet = snippet[:400]

    def handle_data(self, data: str) -> None:
        if self._in_link or self._in_snippet:
            self._buffer.append(data)


def _clean_ddg_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        query = parse_qs(urlparse(href).query)
        if "uddg" in query:
            return unquote(query["uddg"][0])
    return href


class _ReadableTextParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "form", "head"}
    BLOCK = {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.title = ""
        self.links: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.chunks.append("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith("http") and href not in self.links and len(self.links) < 60:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        text = data.strip()
        if text:
            self.chunks.append(text + " ")


def html_to_text(markup: str) -> tuple[str, str, list[str]]:
    parser = _ReadableTextParser()
    try:
        parser.feed(markup)
    except Exception:  # malformed HTML should not crash the request
        pass
    text = "".join(parser.chunks)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return html.unescape(text), " ".join(parser.title.split()), parser.links


def parse_search_html(markup: str) -> list[SearchResult]:
    parser = _DuckDuckGoParser()
    try:
        parser.feed(markup)
    except Exception:
        pass
    # de-duplicate by URL, keep first
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in parser.results:
        if result.url.startswith("http") and result.url not in seen:
            seen.add(result.url)
            unique.append(result)
    return unique


class WebBrowser:
    def __init__(self, db=None, timeout: float = 20.0, allow_private: bool = False) -> None:
        self.db = db
        self.timeout = timeout
        # Only ever true when the user explicitly allows LAN/loopback access.
        self.allow_private = allow_private

    # ------------------------------------------------------------------ search
    def search(self, query: str, limit: int = 8) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise ValueError("nothing to search for")
        errors: list[str] = []
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
        for endpoint in SEARCH_ENDPOINTS:
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
                    response = client.post(endpoint, data={"q": query, "kl": "wt-wt"})
                if response.status_code >= 400:
                    errors.append(f"{endpoint} -> HTTP {response.status_code}")
                    continue
                results = parse_search_html(response.text)
                if results:
                    trimmed = [result.as_dict() for result in results[:limit]]
                    self._record_history(query, endpoint, "search")
                    return {"query": query, "results": trimmed, "count": len(trimmed), "provider": "DuckDuckGo"}
                errors.append(f"{endpoint} -> no parseable results")
            except httpx.HTTPError as exc:
                errors.append(f"{endpoint} -> {exc.__class__.__name__}")
                continue
        raise RuntimeError(
            "Web search failed (offline, blocked, or the search provider changed its markup). "
            + " | ".join(errors[:3])
        )

    # -------------------------------------------------------------------- pages
    def fetch(self, url: str, max_chars: int = 12000) -> Page:
        url = (url or "").strip()
        if not url:
            raise ValueError("no URL given")
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
        current = url
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False, headers=headers) as client:
                for _hop in range(MAX_REDIRECTS + 1):
                    current = check_fetch_target(current, allow_private=self.allow_private)
                    with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location", "")
                            if not location:
                                return Page(url=current, ok=False, status=response.status_code,
                                            error="redirect without a location")
                            current = urljoin(str(response.url), location)
                            continue
                        status = response.status_code
                        content_type = response.headers.get("content-type", "")
                        if status >= 400:
                            return Page(url=current, ok=False, status=status,
                                        error=f"HTTP {status}")
                        if ("html" not in content_type and "text" not in content_type
                                and "xml" not in content_type):
                            return Page(
                                url=current, ok=False, status=status,
                                error=f"unsupported content type: {content_type or 'unknown'} "
                                      "(RAY reads text pages only)",
                            )
                        chunks: list[bytes] = []
                        received = 0
                        for chunk in response.iter_bytes():
                            chunks.append(chunk)
                            received += len(chunk)
                            if received >= MAX_PAGE_BYTES:
                                break
                        body = b"".join(chunks)[:MAX_PAGE_BYTES]
                        final_url = str(response.url)
                        break
                else:
                    return Page(url=current, ok=False, error="too many redirects")
        except UnsafeTarget as exc:
            log.warning("blocked fetch: %s", exc)
            return Page(url=url, ok=False, error=f"blocked: {exc}")
        except httpx.HTTPError as exc:
            return Page(url=current, ok=False, error=f"{exc.__class__.__name__}: {exc}")
        charset = "utf-8"
        match = re.search(r"charset=([\w.-]+)", content_type, re.IGNORECASE)
        if match:
            charset = match.group(1)
        try:
            markup = body.decode(charset, "replace")
        except LookupError:
            markup = body.decode("utf-8", "replace")
        text, title, links = html_to_text(markup)
        self._record_history(title or final_url, final_url, "page")
        return Page(
            url=final_url,
            title=title or final_url,
            text=text[:max_chars],
            links=links,
            ok=True,
            status=status,
        )

    # ---------------------------------------------------------------- research
    def research(self, question: str, router=None, pages: int = 4) -> dict[str, Any]:
        """Search, read the top pages, then ask a model to synthesise *with sources*."""
        search_payload = self.search(question, limit=max(pages + 2, 6))
        results = search_payload["results"][:pages]
        sources: list[dict[str, Any]] = []
        corpus_parts: list[str] = []
        for result in results:
            page = self.fetch(result["url"], max_chars=6000)
            entry = {
                "title": result["title"],
                "url": result["url"],
                "ok": page.ok,
                "error": page.error,
                "chars": len(page.text),
            }
            sources.append(entry)
            if page.ok and page.text:
                corpus_parts.append(f"SOURCE: {result['url']}\nTITLE: {result['title']}\n{page.text}")

        if not corpus_parts:
            return {
                "question": question,
                "report": "",
                "sources": sources,
                "error": "None of the search results could be read, so RAY will not guess an answer.",
            }

        corpus = "\n\n".join(corpus_parts)[:20000]
        report = ""
        error = ""
        if router is not None:
            from ..ai.base import ChatMessage, ProviderError

            system = (
                "You are a research assistant. Using ONLY the numbered sources provided, answer the "
                "question. Cite sources as [n] matching their order. If the sources do not contain the "
                "answer, say so explicitly. Never add facts that are not in the sources."
            )
            numbered = "\n\n".join(
                f"[{index + 1}] {source['url']} - {source['title']}" for index, source in enumerate(sources)
            )
            try:
                result = router.chat(
                    "research",
                    [ChatMessage("user", f"Question: {question}\n\nSources:\n{numbered}\n\n{corpus}")],
                    system=system,
                    temperature=0.2,
                    max_tokens=1200,
                )
                report = result.text
            except ProviderError as exc:
                error = exc.message
            except Exception as exc:
                error = f"{exc.__class__.__name__}: {exc}"
        else:
            from ..knowledge.extract import summarise_locally

            report = summarise_locally("\n\n".join(corpus_parts), max_sentences=8)
            error = "no AI provider available - this is a local extractive summary of the sources"

        if self.db:
            import json

            from ..core.database import utcnow

            self.db.insert(
                "research_reports",
                {
                    "question": question[:500],
                    "report": report,
                    "sources": json.dumps(sources),
                    "created_at": utcnow(),
                },
            )
        return {"question": question, "report": report, "sources": sources, "error": error}

    # -------------------------------------------------------------- bookmarks
    def _record_history(self, title: str, url: str, kind: str) -> None:
        if not self.db:
            return
        from ..core.database import utcnow

        self.db.insert(
            "browser_history",
            {"title": (title or "")[:300], "url": url[:2000], "kind": kind, "ts": utcnow()},
        )

    def history(self, limit: int = 60) -> list[dict[str, Any]]:
        if not self.db:
            return []
        return self.db.query("SELECT * FROM browser_history ORDER BY id DESC LIMIT ?", (limit,))

    def clear_history(self) -> int:
        if not self.db:
            return 0
        return self.db.execute("DELETE FROM browser_history")

    def add_bookmark(self, title: str, url: str, tags: str = "") -> dict[str, Any]:
        if not self.db:
            raise RuntimeError("bookmarks need a database")
        from ..core.database import utcnow

        self.db.execute(
            "INSERT OR REPLACE INTO bookmarks (title, url, tags, created_at) VALUES (?,?,?,?)",
            ((title or url)[:300], url.strip(), tags, utcnow()),
        )
        return self.db.query_one("SELECT * FROM bookmarks WHERE url = ?", (url.strip(),)) or {}

    def bookmarks(self) -> list[dict[str, Any]]:
        if not self.db:
            return []
        return self.db.query("SELECT * FROM bookmarks ORDER BY created_at DESC")

    def delete_bookmark(self, bookmark_id: int) -> bool:
        if not self.db:
            return False
        return bool(self.db.delete("bookmarks", bookmark_id))

    def open_in_browser(self, url: str) -> dict[str, Any]:
        from ..computer.apps import AppManager

        if not re.match(r"^https?://", url.strip()):
            url = f"https://{url.strip()}"
        result = AppManager().open_path(url)
        self._record_history(url, url, "open")
        return result
