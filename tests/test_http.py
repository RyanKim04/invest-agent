import httpx
import pytest

from invest_agent.ingest.http import USER_AGENT, FetchError, PoliteClient


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


def client(
    routes: dict[str, httpx.Response], seen: list[httpx.Request] | None = None
) -> PoliteClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return routes.get(str(request.url), httpx.Response(404))

    clock = FakeClock()
    return PoliteClient(transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep)


HTML = {"content-type": "text/html; charset=utf-8"}


def test_disallowed_path_is_not_fetched():
    seen: list[httpx.Request] = []
    routes = {
        "https://a.test/robots.txt": httpx.Response(
            200, text="User-agent: *\nDisallow: /private/\n"
        ),
        "https://a.test/private/x": httpx.Response(200, headers=HTML, text="secret"),
    }
    with client(routes, seen) as c, pytest.raises(FetchError) as e:
        c.get("https://a.test/private/x")
    assert e.value.code == "robots_disallowed"
    assert [str(r.url) for r in seen] == ["https://a.test/robots.txt"]


def test_missing_robots_allows_and_sends_project_user_agent():
    seen: list[httpx.Request] = []
    routes = {"https://a.test/news/1": httpx.Response(200, headers=HTML, text="<p>hi</p>")}
    with client(routes, seen) as c:
        page = c.get("https://a.test/news/1")
    assert page.body == b"<p>hi</p>"
    assert all(r.headers["user-agent"] == USER_AGENT for r in seen)


def test_robots_server_error_blocks_the_host():
    routes = {"https://a.test/robots.txt": httpx.Response(503)}
    with client(routes) as c, pytest.raises(FetchError) as e:
        c.get("https://a.test/news/1")
    assert e.value.code == "robots_disallowed"


def test_redirect_hop_to_disallowed_host_stops():
    routes = {
        "https://short.test/a": httpx.Response(301, headers={"location": "https://b.test/story"}),
        "https://b.test/robots.txt": httpx.Response(
            200, text="User-agent: invest-agent\nDisallow: /\n"
        ),
    }
    with client(routes) as c, pytest.raises(FetchError) as e:
        c.get("https://short.test/a")
    assert e.value.code == "robots_disallowed"
    assert e.value.url == "https://b.test/story"


def test_same_host_waits_one_second():
    routes = {
        "https://a.test/1": httpx.Response(200, headers=HTML, text="1"),
        "https://a.test/2": httpx.Response(200, headers=HTML, text="2"),
    }
    with client(routes) as c:
        c.get("https://a.test/1")
        c.get("https://a.test/2")
        # robots.txt, /1 and /2 on one host: two 1-second waits
        assert c._sleep.__self__.slept == [1.0, 1.0]


def test_http_error_is_coded_and_429_is_transient():
    routes = {"https://a.test/1": httpx.Response(429)}
    with client(routes) as c, pytest.raises(FetchError) as e:
        c.get("https://a.test/1")
    assert e.value.code == "http_429"
    assert e.value.transient


@pytest.mark.parametrize(
    ("robots", "path", "allowed"),
    [
        ("User-agent: *\nDisallow: /*?print=\n", "/story?print=1", False),
        ("User-agent: *\nDisallow: /*?print=\n", "/story", True),
        # Longest match wins, so the Allow beats the shorter Disallow (Google News' file).
        ("User-agent: *\nDisallow: /\nAllow: /topics/\n", "/topics/x", True),
        ("User-agent: *\nDisallow: /\nAllow: /topics/\n", "/rss/search?q=x", False),
        ("User-agent: *\nDisallow: /\nAllow: /$\n", "/", True),
    ],
)
def test_robots_wildcards_and_longest_match(robots: str, path: str, allowed: bool):
    routes = {"https://a.test/robots.txt": httpx.Response(200, text=robots)}
    with client(routes) as c:
        assert c.allowed(f"https://a.test{path}") is allowed


def test_threads_keep_one_request_per_host_in_flight():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    in_flight: dict[str, int] = {}
    worst: dict[str, int] = {}
    guard = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        with guard:
            in_flight[host] = in_flight.get(host, 0) + 1
            worst[host] = max(worst.get(host, 0), in_flight[host])
        threading.Event().wait(0.01)
        with guard:
            in_flight[host] -= 1
        return httpx.Response(200, headers=HTML, text="ok")

    c = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler))
    urls = [f"https://{h}.test/{i}" for i in range(8) for h in ("a", "b", "c")]
    with c, ThreadPoolExecutor(6) as pool:
        list(pool.map(c.get, urls))
    assert worst == {"a.test": 1, "b.test": 1, "c.test": 1}
