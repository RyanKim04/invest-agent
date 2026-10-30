"""HTTP for publisher pages: robots.txt, at most 1 request per second per host, and a
User-Agent that names the project. Redirects are followed by hand so every hop is checked.

Safe to share between threads: each host has one request in flight at a time, and different
hosts proceed in parallel."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from protego import Protego

USER_AGENT = "invest-agent/0.1 (+https://github.com/RyanKim04/invest-agent)"
ROBOTS_TOKEN = "invest-agent"
MAX_BYTES = 5_000_000
MAX_REDIRECTS = 5
_ALLOW_ALL = Protego.parse("")
_DISALLOW_ALL = Protego.parse("User-agent: *\nDisallow: /")
_TRANSIENT = {
    "timeout",
    "connect_error",
    "http_429",
    "http_500",
    "http_502",
    "http_503",
    "http_504",
}


class FetchError(Exception):
    """`code` is the value stored in `fetch_error`, e.g. `robots_disallowed` or `http_403`."""

    def __init__(self, code: str, url: str):
        super().__init__(code)
        self.code = code
        self.url = url

    @property
    def transient(self) -> bool:
        return self.code in _TRANSIENT


@dataclass(frozen=True)
class Page:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    elapsed_ms: int


@dataclass(frozen=True)
class _Response:
    status: int
    location: str | None
    content_type: str
    body: bytes


class PoliteClient:
    def __init__(
        self,
        min_interval: float = 1.0,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._http = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._robots: dict[str, Protego] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, url: str) -> Page:
        start = self._clock()
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not self.allowed(current):
                raise FetchError("robots_disallowed", current)
            response = self._request(current)
            if response.location:
                current = urljoin(current, response.location)
                continue
            if response.status >= 400:
                raise FetchError(f"http_{response.status}", current)
            return Page(
                url=url,
                final_url=current,
                status=response.status,
                content_type=response.content_type,
                body=response.body,
                elapsed_ms=int((self._clock() - start) * 1000),
            )
        raise FetchError("too_many_redirects", current)

    def allowed(self, url: str) -> bool:
        origin = _origin(url)
        with self._lock(f"robots {origin}"):
            if origin not in self._robots:
                self._robots[origin] = self._load_robots(origin)
        return self._robots[origin].can_fetch(url, ROBOTS_TOKEN)

    def _load_robots(self, origin: str) -> Protego:
        # RFC 9309: a 4xx means no rules; a 5xx or no answer means fetch nothing.
        # Protego handles * and $ and lets the longest matching rule win, as the RFC says.
        url = f"{origin}/robots.txt"
        try:
            for _ in range(MAX_REDIRECTS + 1):
                response = self._request(url)
                if not response.location:
                    break
                url = urljoin(url, response.location)
        except FetchError:
            return _DISALLOW_ALL
        if response.status >= 500 or response.location:
            return _DISALLOW_ALL
        if response.status >= 400:
            return _ALLOW_ALL
        return Protego.parse(response.body.decode("utf-8", errors="replace"))

    def _lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _request(self, url: str) -> _Response:
        host = urlsplit(url).netloc
        with self._lock(f"host {host}"):
            return self._request_locked(url, host)

    def _request_locked(self, url: str, host: str) -> _Response:
        delay = self._min_interval
        robots = self._robots.get(_origin(url))
        if robots is not None:
            delay = max(delay, float(robots.crawl_delay(ROBOTS_TOKEN) or 0))
        wait = self._last.get(host, float("-inf")) + delay - self._clock()
        if wait > 0:
            self._sleep(wait)
        try:
            with self._http.stream("GET", url) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FetchError("too_large", url)
                    chunks.append(chunk)
                return _Response(
                    status=response.status_code,
                    location=response.headers.get("location") if response.is_redirect else None,
                    content_type=response.headers.get("content-type", ""),
                    body=b"".join(chunks),
                )
        except httpx.TimeoutException as e:
            raise FetchError("timeout", url) from e
        except httpx.TransportError as e:
            raise FetchError("connect_error", url) from e
        finally:
            self._last[host] = self._clock()


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
