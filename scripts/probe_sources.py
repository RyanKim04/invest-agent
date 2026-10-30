"""F1: measure each source for the universe tickers.

Raw responses and one summary per source go to data/probe/ (gitignored). Successful responses
are cached, so a rerun doesn't spend API quota; pass --refresh to call again. Keys are read
from the environment and never written out.

    uv run --env-file .env --group probe python scripts/probe_sources.py all
"""

import base64
import json
import os
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
import lxml.html
import typer

from invest_agent.config import Company, load_universe
from invest_agent.ingest.http import USER_AGENT, FetchError, PoliteClient
from invest_agent.ingest.news_body import fetch_body

OUT = Path("data/probe")
DAYS = 7
TODAY = datetime.now(UTC).date()
SINCE = TODAY - timedelta(days=DAYS)

app = typer.Typer(no_args_is_help=True, add_completion=False)


class Recorder:
    """GETs with a minimum interval, caches 2xx bodies under data/probe/raw/<source>/, and
    keeps status, timing and rate-limit headers per call. URLs aren't stored: some carry keys."""

    def __init__(self, source: str, http: httpx.Client, interval: float, refresh: bool):
        self.dir = OUT / "raw" / source
        self.dir.mkdir(parents=True, exist_ok=True)
        self.http = http
        self.interval = interval
        self.refresh = refresh
        self.calls: list[dict] = []
        self._last = float("-inf")

    def get(
        self,
        label: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        ext: str = "json",
        cache_if: Callable[[bytes], bool] = lambda body: True,
    ) -> tuple[int, bytes]:
        path = self.dir / f"{_safe(label)}.{ext}"
        meta = self.dir / f"{_safe(label)}.meta.json"
        if path.exists() and meta.exists() and not self.refresh:
            record = json.loads(meta.read_text()) | {"cached": True}
            self.calls.append(record)
            return record["status"], path.read_bytes()

        wait = self._last + self.interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        start = time.monotonic()
        try:
            response = self.http.get(url, params=params, headers=headers)
        except httpx.HTTPError as e:
            self.calls.append({"label": label, "status": 0, "error": type(e).__name__})
            return 0, b""
        finally:
            self._last = time.monotonic()
        record = {
            "label": label,
            "status": response.status_code,
            "ms": int((time.monotonic() - start) * 1000),
            "bytes": len(response.content),
            "rate": {
                k: v
                for k, v in response.headers.items()
                if "ratelimit" in k.lower() or k.lower() == "retry-after"
            },
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.calls.append(record)
        if response.is_success and cache_if(response.content):
            path.write_bytes(response.content)
            meta.write_text(json.dumps(record))
        return response.status_code, response.content


# --- SEC EDGAR -------------------------------------------------------------------------------

SEC_INTERVAL = 0.15  # SEC allows 10 requests/s


@app.command()
def sec(refresh: bool = False) -> None:
    universe = load_universe()
    headers = {"User-Agent": _env("SEC_USER_AGENT"), "Accept-Encoding": "gzip, deflate"}
    with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as http:
        rec = Recorder("sec", http, SEC_INTERVAL, refresh)
        _, body = rec.get("company_tickers", "https://www.sec.gov/files/company_tickers.json")
        sec_ciks = {r["ticker"]: f"{r['cik_str']:010d}" for r in json.loads(body).values()}
        ciks = {
            t: {"config": c.cik if c else None, "sec": sec_ciks.get(t)}
            for t, c in [(c.ticker, c) for c in universe] + [("GOOGL", None)]
        }

        since = (TODAY - timedelta(days=90)).isoformat()
        companies = {}
        for c in universe:
            url = f"https://data.sec.gov/submissions/CIK{c.cik}.json"
            _, body = rec.get(f"submissions_{c.ticker}", url)
            recent = json.loads(body)["filings"]["recent"]
            rows = [
                dict(zip(recent, values, strict=True))
                for values in zip(*recent.values(), strict=True)
            ]
            window = [r for r in rows if r["filingDate"] >= since]
            companies[c.ticker] = {
                "submission_fields": sorted(recent),
                "acceptance_example": rows[0]["acceptanceDateTime"],
                "forms_90d": Counter(r["form"] for r in window),
                "10q_90d": [r["filingDate"] for r in window if r["form"] == "10-Q"],
                "8k_90d": [_sec_8k(rec, c, r) for r in window if r["form"] in ("8-K", "8-K/A")],
            }
        facts = _sec_companyfacts(rec, universe[0])
    _save("sec", {"ciks": ciks, "companies": companies, "companyfacts": facts, "calls": rec.calls})


def _sec_8k(rec: Recorder, company: Company, row: dict) -> dict:
    acc = row["accessionNumber"]
    base = f"https://www.sec.gov/Archives/edgar/data/{int(company.cik)}/{acc.replace('-', '')}"
    status, body = rec.get(f"index_{acc}", f"{base}/{acc}-index.htm", ext="html")
    docs, index_items = _sec_index(body) if status == 200 else ([], [])
    out = {
        "accession": acc,
        "filed": row["filingDate"],
        "accepted": row["acceptanceDateTime"],
        "items_api": row["items"],
        "items_index": index_items,
        "index_status": status,
        "documents": [],
    }
    for doc in docs:
        if doc["type"] not in ("8-K", "8-K/A") and not doc["type"].startswith("EX-99"):
            continue
        url = urljoin("https://www.sec.gov", doc["href"].replace("/ix?doc=", ""))
        name = url.rsplit("/", 1)[-1]
        status, body = rec.get(f"doc_{acc}_{name}", url, ext="html")
        out["documents"].append(
            {"type": doc["type"], "name": name, "status": status, "words": _html_words(body)}
        )
    return out


def _sec_index(html: bytes) -> tuple[list[dict], list[str]]:
    tree = lxml.html.fromstring(html)
    docs = []
    for row in tree.xpath('//table[@class="tableFile"]//tr[td]'):
        cells = row.xpath("./td")
        if len(cells) < 4:
            continue
        href = cells[2].xpath(".//a/@href")
        docs.append({"href": href[0] if href else "", "type": cells[3].text_content().strip()})
    items: list[str] = []
    for group in tree.xpath('//div[@class="formGrouping"]'):
        if [h.strip() for h in group.xpath('./div[@class="infoHead"]/text()')] == ["Items"]:
            items = [t.strip() for t in group.xpath('./div[@class="info"]//text()') if t.strip()]
    return docs, items


def _sec_companyfacts(rec: Recorder, company: Company) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company.cik}.json"
    status, body = rec.get(f"companyfacts_{company.ticker}", url)
    if status != 200:
        return {"ticker": company.ticker, "status": status}
    data = json.loads(body)
    entry_keys: Counter[str] = Counter()
    repeats = 0
    concepts = 0
    for taxonomy in data["facts"].values():
        for concept in taxonomy.values():
            concepts += 1
            for entries in concept["units"].values():
                entry_keys.update(k for e in entries for k in e)
                periods = Counter((e.get("start"), e["end"], e["accn"]) for e in entries)
                repeats += sum(1 for n in periods.values() if n > 1)
    return {
        "ticker": company.ticker,
        "taxonomies": sorted(data["facts"]),
        "concepts": concepts,
        "entry_keys": dict(entry_keys),
        # More than one value for the same concept, period and filing would mean dimensions.
        "same_period_same_filing_repeats": repeats,
    }


# --- Finnhub ---------------------------------------------------------------------------------


@app.command()
def finnhub(refresh: bool = False, days: int = DAYS) -> None:
    key = _env("FINNHUB_API_KEY")
    base = "https://finnhub.io/api/v1"
    window = {"from": SINCE.isoformat(), "to": TODAY.isoformat()}
    dates = [TODAY - timedelta(days=n) for n in range(days, -1, -1)]
    with httpx.Client(timeout=30) as http:
        rec = Recorder("finnhub", http, 1.1, refresh)
        # One call over the whole window is cut off at about 250 items, so ask one day at a time.
        status, body = rec.get(
            "company_news_GOOG_7d",
            f"{base}/company-news",
            params={"symbol": "GOOG", **window, "token": key},
        )
        whole = json.loads(body) if status == 200 else []
        news: dict = {"GOOG_one_call_7d": {"count": len(whole), **_time_range(whole)}}
        ids = {}
        for symbol in ["GOOG", "GOOGL", "META", "RDDT"]:
            items, per_day = {}, {}
            for day in dates:
                params = {
                    "symbol": symbol,
                    "from": day.isoformat(),
                    "to": day.isoformat(),
                    "token": key,
                }
                status, body = rec.get(
                    f"company_news_{symbol}_{day}", f"{base}/company-news", params=params
                )
                batch = json.loads(body) if status == 200 else []
                per_day[day.isoformat()] = len(batch) if status == 200 else f"http_{status}"
                items.update((i["id"], i) for i in batch)
            items = list(items.values())
            (OUT / "raw" / "finnhub" / f"company_news_{symbol}.json").write_text(json.dumps(items))
            ids[symbol] = {i["id"] for i in items}
            news[symbol] = {"per_day": per_day, **_time_range(items)}
            news[symbol] |= _news_stats(items, "datetime", "url", "source")
            news[symbol]["summary_words"] = _word_stats(i.get("summary") for i in items)
            news[symbol]["categories"] = Counter(i.get("category") for i in items)
            news[symbol]["related"] = Counter(i.get("related") for i in items).most_common(5)
        news["GOOG_GOOGL_shared_ids"] = len(ids["GOOG"] & ids["GOOGL"])

        later = (TODAY + timedelta(days=30)).isoformat()
        endpoints = {
            "press_releases": ("/press-releases", {"symbol": "GOOG"}),
            "transcripts_list": ("/stock/transcripts/list", {"symbol": "GOOG"}),
            "earnings_calendar": ("/calendar/earnings", {"from": SINCE.isoformat(), "to": later}),
            "recommendation": ("/stock/recommendation", {"symbol": "GOOG"}),
            "price_target": ("/stock/price-target", {"symbol": "GOOG"}),
            "upgrade_downgrade": ("/stock/upgrade-downgrade", {"symbol": "GOOG"}),
            "news_sentiment": ("/news-sentiment", {"symbol": "GOOG"}),
            "basic_financials": ("/stock/metric", {"symbol": "GOOG", "metric": "all"}),
            "sec_filings": ("/stock/filings", {"symbol": "GOOG"}),
        }
        access = {}
        for name, (path, params) in endpoints.items():
            status, body = rec.get(name, f"{base}{path}", params={**params, "token": key})
            access[name] = {"status": status, "shape": _shape(body) if status == 200 else None}
            if status != 200:
                access[name]["error"] = body[:150].decode("utf-8", errors="replace")
    _save(
        "finnhub", {"window": window, "company_news": news, "endpoints": access, "calls": rec.calls}
    )


# --- Google News RSS -------------------------------------------------------------------------

GNEWS_QUERY = {
    "GOOG": '"Alphabet" OR GOOGL OR GOOG',
    "META": '"Meta Platforms" OR "META stock"',
    "RDDT": '"Reddit Inc" OR RDDT',
}


@app.command()
def gnews(refresh: bool = False, resolve: int = 30) -> None:
    feeds, links = {}, []
    with (
        PoliteClient() as polite,
        httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30) as http,
    ):
        rec = Recorder("gnews", http, 1.0, refresh)
        for c in load_universe():
            params = {
                "q": f"{GNEWS_QUERY[c.ticker]} when:{DAYS}d",
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            }
            url = "https://news.google.com/rss/search"
            allowed = polite.allowed(str(httpx.URL(url, params=params)))
            status, body = (
                rec.get(f"rss_{c.ticker}", url, params=params, ext="xml") if allowed else (0, b"")
            )
            entries = feedparser.parse(body).entries
            feeds[c.ticker] = {
                "robots_allowed": allowed,
                "status": status,
                "count": len(entries),
                "fields": sorted(entries[0]) if entries else [],
                "published_example": entries[0].get("published") if entries else None,
                "outlets": Counter(e.get("source", {}).get("title") for e in entries).most_common(
                    15
                ),
                "description_words": _word_stats(_text(e.get("summary", "")) for e in entries),
            }
            links += [(c.ticker, e.link) for e in entries]

        step = max(1, len(links) // resolve)
        results = []
        for ticker, link in links[::step][:resolve]:
            result = {
                "ticker": ticker,
                "robots_allowed": polite.allowed(link),
                "local_decode": _gnews_decode(link) is not None,
            }
            try:
                page = polite.get(link)
                result["final_host"] = urlsplit(page.final_url).netloc
            except FetchError as e:
                result["fetch_error"] = e.code
            results.append(result)
    resolution = {
        "tried": len(results),
        "robots_allowed": sum(r["robots_allowed"] for r in results),
        "redirected_to_publisher": sum(
            1 for r in results if r.get("final_host") and "google." not in r["final_host"]
        ),
        "decoded_without_request": sum(r["local_decode"] for r in results),
        "errors": Counter(r.get("fetch_error") for r in results if r.get("fetch_error")),
        "final_hosts": Counter(r.get("final_host") for r in results if r.get("final_host")),
    }
    _save("gnews", {"feeds": feeds, "resolution": resolution, "calls": rec.calls})


def _gnews_decode(link: str) -> str | None:
    """Old-style Google News article ids carry the publisher URL in base64. Newer ones don't."""
    match = re.search(r"/articles/([A-Za-z0-9_-]+)", link)
    if not match:
        return None
    token = match.group(1)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except ValueError:
        return None
    found = re.search(rb"https?://[\x21-\x7e]+", raw)
    return found.group(0).decode() if found else None


# --- GDELT DOC 2.0 ---------------------------------------------------------------------------

GDELT_QUERY = {
    "GOOG": "(Alphabet OR GOOGL)",
    "META": '("Meta Platforms" OR Zuckerberg)',
    "RDDT": '("Reddit Inc" OR RDDT)',
}


@app.command()
def gdelt(refresh: bool = False) -> None:
    out = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as http:
        rec = Recorder("gdelt", http, 6.0, refresh)  # GDELT asks for 1 request per 5 s
        for ticker, query in GDELT_QUERY.items():
            params = {
                "query": f"{query} sourcelang:english",
                "mode": "ArtList",
                "format": "json",
                "maxrecords": "250",
                "timespan": f"{DAYS}d",
                "sort": "DateDesc",
            }
            status, body = rec.get(
                f"artlist_{ticker}",
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params=params,
                cache_if=lambda b: b.lstrip().startswith(b"{"),
            )
            try:
                items = json.loads(body).get("articles", [])
            except ValueError:
                out[ticker] = {"status": status, "error": body[:200].decode(errors="replace")}
                continue
            out[ticker] = {
                "status": status,
                "query": params["query"],
                "hit_cap": len(items) == 250,
                **_news_stats(items, "seendate", "url", "domain"),
                "languages": Counter(i.get("language") for i in items),
                "countries": Counter(i.get("sourcecountry") for i in items).most_common(5),
            }
    _save("gdelt", {"artlist": out, "calls": rec.calls})


# --- Company IR RSS --------------------------------------------------------------------------

IR_HOME = {
    "GOOG": "https://abc.xyz/investor/",
    "META": "https://investor.atmeta.com/",
    "RDDT": "https://investor.redditinc.com/",
}
FEED_PATHS = ["/rss/news-releases.xml", "/news-releases/rss", "/rss/pressrelease.aspx", "/rss"]


@app.command()
def ir() -> None:
    out = {}
    raw = OUT / "raw" / "ir"
    raw.mkdir(parents=True, exist_ok=True)
    with PoliteClient() as polite:
        for ticker, home in IR_HOME.items():
            result: dict = {"home": home, "tried": []}
            candidates = []
            try:
                page = polite.get(home)
                (raw / f"home_{ticker}.html").write_bytes(page.body)
                tree = lxml.html.fromstring(page.body)
                found = tree.xpath(
                    '//link[contains(@type,"rss") or contains(@type,"atom")]/@href'
                    ' | //a[contains(translate(@href,"RS","rs"),"rss")]/@href'
                )
                result["feed_links_on_page"] = found[:10]
                candidates = [urljoin(page.final_url, h) for h in found]
            except FetchError as e:
                result["home_error"] = e.code
            candidates += [urljoin(home, p) for p in FEED_PATHS]
            for url in list(dict.fromkeys(candidates))[:10]:
                try:
                    page = polite.get(url)
                except FetchError as e:
                    result["tried"].append([url, e.code])
                    continue
                entries = feedparser.parse(page.body).entries
                if not entries:
                    result["tried"].append([url, "no_entries"])
                    continue
                (raw / f"feed_{ticker}.xml").write_bytes(page.body)
                result["feed"] = url
                result["count"] = len(entries)
                result["fields"] = sorted(entries[0])
                result["published_example"] = entries[0].get("published")
                result["releases"] = [_release(polite, e) for e in entries[:2]]
                break
            out[ticker] = result
    _save("ir", out)


def _release(polite: PoliteClient, entry: dict) -> dict:
    body, page, _ = fetch_body(polite, entry["link"])
    return {
        "title": entry.get("title"),
        "link": entry["link"],
        "body_status": body.status,
        "fetch_error": body.fetch_error,
        "words": body.words,
        "content_type": page.content_type if page else None,
    }


# --- yfinance --------------------------------------------------------------------------------


@app.command("yfinance")
def yfinance_probe() -> None:
    import yfinance as yf

    attributes = [
        "news",
        "upgrades_downgrades",
        "analyst_price_targets",
        "earnings_estimate",
        "calendar",
        "earnings_dates",
    ]
    out = {}
    for c in load_universe():
        ticker = yf.Ticker(c.ticker)
        result = {}
        for name in ["history", *attributes]:
            try:
                value = (
                    ticker.history(period="10d", interval="1d")
                    if name == "history"
                    else getattr(ticker, name)
                )
                result[name] = _describe(value)
            except Exception as e:  # yfinance raises many types; record them all
                result[name] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            time.sleep(1)
        out[c.ticker] = result
    _save("yfinance", {"version": yf.__version__, "tickers": out})


def _describe(value: object) -> dict:
    if hasattr(value, "columns"):  # DataFrame
        index = getattr(value, "index", None)
        return {
            "rows": len(value),
            "columns": [str(c) for c in value.columns],
            "index_example": str(index[-1]) if index is not None and len(index) else None,
            "index_tz": str(getattr(index, "tz", None)),
        }
    if isinstance(value, list):
        content = [v.get("content", v) for v in value if isinstance(v, dict)]
        return {
            "count": len(value),
            "keys": sorted({k for v in value if isinstance(v, dict) for k in v}),
            "content_keys": sorted({k for v in content for k in v}),
            "pub_date_example": next((v.get("pubDate") for v in content if "pubDate" in v), None),
            "summary_words": _word_stats(v.get("summary") for v in content),
        }
    if isinstance(value, dict):
        return {
            "keys": sorted(map(str, value)),
            "example": {k: str(v)[:60] for k, v in value.items()},
        }
    return {"type": type(value).__name__, "value": str(value)[:200]}


# --- Tiingo ----------------------------------------------------------------------------------


@app.command()
def tiingo(refresh: bool = False) -> None:
    headers = {"Authorization": f"Token {_env('TIINGO_API_KEY')}"}
    base = "https://api.tiingo.com"
    with httpx.Client(headers=headers, timeout=30) as http:
        rec = Recorder("tiingo", http, 1.0, refresh)
        prices = {}
        for c in load_universe():
            status, body = rec.get(
                f"eod_{c.ticker}",
                f"{base}/tiingo/daily/{c.ticker}/prices",
                params={"startDate": SINCE.isoformat()},
            )
            rows = json.loads(body) if status == 200 else []
            prices[c.ticker] = {
                "status": status,
                "rows": len(rows),
                "fields": sorted(rows[0]) if rows else [],
                "date_example": rows[-1]["date"] if rows else None,
                "error": None if status == 200 else body[:150].decode(errors="replace"),
            }
        status, body = rec.get(
            "news",
            f"{base}/tiingo/news",
            params={"tickers": "goog,meta,rddt", "startDate": SINCE.isoformat(), "limit": "100"},
        )
        items = json.loads(body) if status == 200 else []
        news = {"status": status, **_news_stats(items, "publishedDate", "url", "source")}
        news["description_words"] = _word_stats(i.get("description") for i in items)
        news["tickers"] = Counter(t for i in items for t in i.get("tickers", [])).most_common(10)
        if status != 200:
            news["error"] = body[:150].decode(errors="replace")
        status, body = rec.get(
            "fundamentals",
            f"{base}/tiingo/fundamentals/goog/statements",
            params={"startDate": (TODAY - timedelta(days=200)).isoformat()},
        )
        fundamentals = {"status": status, "shape": _shape(body) if status == 200 else None}
        if status != 200:
            fundamentals["error"] = body[:150].decode(errors="replace")
    _save(
        "tiingo",
        {"prices": prices, "news": news, "fundamentals": fundamentals, "calls": rec.calls},
    )


# --- Alpha Vantage (free tier: 25 requests a day) --------------------------------------------


def _av_ok(body: bytes) -> bool:
    head = body[:400]
    return not any(k in head for k in (b'"Information"', b'"Note"', b'"Error Message"'))


@app.command()
def alphavantage(refresh: bool = False) -> None:
    key = _env("ALPHAVANTAGE_API_KEY")
    since = SINCE.strftime("%Y%m%dT0000")
    calls = {
        **{
            f"news_{t}": {
                "function": "NEWS_SENTIMENT",
                "tickers": t,
                "time_from": since,
                "limit": "1000",
                "sort": "LATEST",
            }
            for t in ("GOOG", "META", "RDDT")
        },
        "daily_GOOG": {"function": "TIME_SERIES_DAILY", "symbol": "GOOG"},
        "transcript_GOOG_2026Q2": {
            "function": "EARNINGS_CALL_TRANSCRIPT",
            "symbol": "GOOG",
            "quarter": "2026Q2",
        },
        "transcript_GOOG_2026Q3": {
            "function": "EARNINGS_CALL_TRANSCRIPT",
            "symbol": "GOOG",
            "quarter": "2026Q3",
        },
    }
    out = {}
    with httpx.Client(timeout=60) as http:
        rec = Recorder("alphavantage", http, 13.0, refresh)
        for label, params in calls.items():
            status, body = rec.get(
                label,
                "https://www.alphavantage.co/query",
                params={**params, "apikey": key},
                cache_if=_av_ok,
            )
            if not _av_ok(body):
                out[label] = {"status": status, "message": body[:300].decode(errors="replace")}
                continue
            data = json.loads(body)
            if label.startswith("news_"):
                items = data.get("feed", [])
                out[label] = {
                    "status": status,
                    "items_field": data.get("items"),
                    **_news_stats(items, "time_published", "url", "source"),
                    "summary_words": _word_stats(i.get("summary") for i in items),
                    "ticker_sentiment_fields": sorted(
                        {k for i in items for s in i.get("ticker_sentiment", []) for k in s}
                    ),
                }
            elif label.startswith("transcript_"):
                parts = data.get("transcript", [])
                out[label] = {
                    "status": status,
                    "keys": sorted(data),
                    "parts": len(parts),
                    "part_fields": sorted({k for p in parts for k in p}),
                    "words": sum(len(str(p.get("content", "")).split()) for p in parts),
                }
            else:
                series = data.get("Time Series (Daily)", {})
                out[label] = {
                    "status": status,
                    "keys": sorted(data),
                    "rows": len(series),
                    "latest": max(series) if series else None,
                }
    _save("alphavantage", {"results": out, "calls": rec.calls})


# --- FRED ------------------------------------------------------------------------------------

FRED_SERIES = ["DGS10", "DGS2", "T10Y2Y", "DFF", "CPIAUCSL", "UNRATE", "VIXCLS", "DTWEXBGS"]


@app.command()
def fred(refresh: bool = False) -> None:
    key = _env("FRED_API_KEY")
    base = "https://api.stlouisfed.org/fred"
    out = {}
    with httpx.Client(timeout=30) as http:
        rec = Recorder("fred", http, 0.6, refresh)
        for series_id in FRED_SERIES:
            params = {"series_id": series_id, "api_key": key, "file_type": "json"}
            status, body = rec.get(f"series_{series_id}", f"{base}/series", params=params)
            meta = (json.loads(body).get("seriess") or [{}])[0] if status == 200 else {}
            status2, body2 = rec.get(
                f"obs_{series_id}",
                f"{base}/series/observations",
                params={**params, "observation_start": (TODAY - timedelta(days=90)).isoformat()},
            )
            obs = json.loads(body2).get("observations", []) if status2 == 200 else []
            out[series_id] = {
                "status": [status, status2],
                "title": meta.get("title"),
                "frequency": meta.get("frequency_short"),
                "units": meta.get("units_short"),
                "last_updated": meta.get("last_updated"),
                "observation_end": meta.get("observation_end"),
                "copyright_note": "copyright" in (meta.get("notes") or "").lower(),
                "obs_fields": sorted(obs[0]) if obs else [],
                "obs_90d": len(obs),
                "missing": sum(1 for o in obs if o.get("value") == "."),
                "latest": obs[-1] if obs else None,
            }
    _save("fred", {"series": out, "calls": rec.calls})


# --- Alpaca News (Benzinga feed) ------------------------------------------------------------

RATING_HEADLINE = re.compile(
    r"\b(maintains|reiterates|upgrades?|downgrades?|initiates|raises|lowers|cuts|boosts)\b.*"
    r"\b(price target|rating|overweight|underweight|outperform|buy|sell|neutral|hold)\b",
    re.I,
)


@app.command()
def alpaca(refresh: bool = False, days: int = 30, max_pages: int = 60) -> None:
    headers = {
        "APCA-API-KEY-ID": _env("APCA_API_KEY_ID"),
        "APCA-API-SECRET-KEY": _env("APCA_API_SECRET_KEY"),
    }
    start = (TODAY - timedelta(days=days)).isoformat()
    out = {}
    with httpx.Client(headers=headers, timeout=30) as http:
        rec = Recorder("alpaca", http, 0.4, refresh)  # Basic plan: 200 calls a minute
        for c in load_universe():
            items, token = [], None
            for page in range(max_pages):
                params = {
                    "symbols": c.ticker,
                    "start": start,
                    "limit": "50",
                    "sort": "desc",
                    "include_content": "true",
                    "exclude_contentless": "false",
                }
                if token:
                    params["page_token"] = token
                status, body = rec.get(
                    f"news_{c.ticker}_{days}d_p{page}",
                    "https://data.alpaca.markets/v1beta1/news",
                    params=params,
                )
                if status != 200:
                    out[c.ticker] = {"status": status, "error": body[:200].decode(errors="replace")}
                    break
                data = json.loads(body)
                items += data.get("news", [])
                token = data.get("next_page_token")
                if not token:
                    break
            (OUT / "raw" / "alpaca" / f"news_{c.ticker}.json").write_text(json.dumps(items))
            if c.ticker in out:
                continue
            words = [len(_text(i.get("content") or "").split()) for i in items]
            out[c.ticker] = {
                "count": len(items),
                "fields": _fields(items),
                "time_example": items[0].get("created_at") if items else None,
                "first": min((i["created_at"] for i in items), default=None),
                "sources": Counter(i.get("source") for i in items).most_common(5),
                "with_content": sum(1 for w in words if w),
                "content_150_words_plus": sum(1 for w in words if w >= 150),
                "content_words": _word_stats(_text(i.get("content") or "") for i in items),
                "with_url": sum(1 for i in items if i.get("url")),
                "rating_headlines": sum(1 for i in items if RATING_HEADLINE.search(i["headline"])),
                "rating_headlines_with_150_words": sum(
                    1
                    for i, w in zip(items, words, strict=True)
                    if RATING_HEADLINE.search(i["headline"]) and w >= 150
                ),
                "authors": Counter(i.get("author") for i in items).most_common(8),
            }
    # Benzinga articles that returned 403 through Finnhub links: does Alpaca have them?
    blocked = set()
    results = OUT / "bodies30" / "results.jsonl"
    if results.exists():
        for line in results.open():
            r = json.loads(line)
            if r["outlet"] == "Benzinga" and r["status"] == "headline_only":
                blocked.add(_norm_headline(r["headline"]))
    alpaca_heads = {
        _norm_headline(i["headline"]): bool(_text(i.get("content") or "").strip())
        for t in out
        for i in json.loads((OUT / "raw" / "alpaca" / f"news_{t}.json").read_text())
    }
    found = [h for h in blocked if h in alpaca_heads]
    out["blocked_benzinga_on_finnhub"] = {
        "blocked": len(blocked),
        "found_in_alpaca": len(found),
        "found_with_content": sum(alpaca_heads[h] for h in found),
    }
    _save("alpaca", {"days": days, "news": out, "calls": rec.calls})


def _norm_headline(headline: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()


# --- EODHD news (free: 20 calls a day + 500 welcome calls) ----------------------------------


@app.command()
def eodhd(refresh: bool = False, days: int = 30) -> None:
    """One request per ticker for the window (10 calls each), then one 3-ticker request
    (20 calls) to see whether a single daily request fits the free plan."""
    key = _env("EODHD_API_KEY")
    start = (TODAY - timedelta(days=days)).isoformat()
    requests = {
        **{t: f"{t}.US" for t in ("GOOG", "GOOGL", "META", "RDDT")},
        "combined": "GOOG.US,META.US,RDDT.US",
    }
    out, usage = {}, []
    with httpx.Client(timeout=60) as http:
        rec = Recorder("eodhd", http, 3.5, refresh)  # free: 20 requests a minute
        usage.append(_eodhd_usage(http, key))
        for label, symbols in requests.items():
            params = {
                "s": symbols,
                "from": start,
                "to": TODAY.isoformat(),
                "limit": "1000",
                "offset": "0",
                "fmt": "json",
                "api_token": key,
            }
            status, body = rec.get(
                f"news_{label}_{days}d", "https://eodhd.com/api/news", params=params
            )
            usage.append(_eodhd_usage(http, key) | {"after": label})
            try:
                items = json.loads(body)
            except ValueError:
                items = None
            if status != 200 or not isinstance(items, list):
                out[label] = {"status": status, "error": body[:200].decode(errors="replace")}
                continue
            (OUT / "raw" / "eodhd" / f"articles_{label}.json").write_text(json.dumps(items))
            words = [len((i.get("content") or "").split()) for i in items]
            out[label] = {
                "status": status,
                "count": len(items),
                "capped": len(items) == 1000,
                "fields": _fields(items),
                "first": min((i["date"] for i in items), default=None),
                "last": max((i["date"] for i in items), default=None),
                "content_words": _word_stats(i.get("content") for i in items),
                "content_150_words_plus": sum(1 for w in words if w >= 150),
                "hosts": Counter(urlsplit(i.get("link") or "").netloc for i in items).most_common(
                    15
                ),
            }
    out["vs_finnhub_bodies"] = _eodhd_vs_ours()
    _save("eodhd", {"days": days, "news": out, "usage": usage, "calls": rec.calls})


def _eodhd_usage(http: httpx.Client, key: str) -> dict:
    """Calls used today, from the account endpoint (the response also has the account email,
    which isn't kept)."""
    try:
        data = http.get(
            "https://eodhd.com/api/user", params={"api_token": key, "fmt": "json"}
        ).json()
    except (httpx.HTTPError, ValueError):
        return {"error": "user endpoint failed"}
    return {
        k: data.get(k) for k in ("apiRequests", "dailyRateLimit", "extraLimit", "apiRequestsDate")
    }


def _eodhd_vs_ours() -> dict:
    """Same article in EODHD and in our 30-day fetch: compare body lengths. Also count EODHD
    articles from hosts that block us."""
    ours = {}
    results = OUT / "bodies30" / "results.jsonl"
    if results.exists():
        for line in results.open():
            r = json.loads(line)
            if r.get("final_url"):
                ours[_bare_url(r["final_url"])] = r
    items = {}
    for path in (OUT / "raw" / "eodhd").glob("articles_*.json"):
        for i in json.loads(path.read_text()):
            items[_bare_url(i.get("link") or "")] = i
    pairs = [(i, ours[u]) for u, i in items.items() if u in ours]
    ratios = [
        len((i.get("content") or "").split()) / r["words"]
        for i, r in pairs
        if r["status"] == "full" and r["words"]
    ]
    blocked = {"www.benzinga.com", "seekingalpha.com", "www.thestreet.com"}
    return {
        "eodhd_unique_links": len(items),
        "also_in_our_fetch": len(pairs),
        "where_ours_was_not_full": sum(1 for _, r in pairs if r["status"] != "full"),
        "pairs_ours_full": len(ratios),
        "words_eodhd_over_ours_median": round(statistics.median(ratios), 2) if ratios else None,
        "eodhd_under_70pct_of_ours": sum(1 for x in ratios if x < 0.7),
        "from_hosts_that_block_us": sum(1 for u in items if urlsplit(u).netloc in blocked),
    }


def _bare_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path}".rstrip("/").lower()


# --- FMP grades and price targets ------------------------------------------------------------


@app.command()
def fmp(refresh: bool = False) -> None:
    key = _env("FMP_API_KEY")
    endpoints = [
        "grades",
        "grades-historical",
        "grades-consensus",
        "price-target-news",
        "price-target-consensus",
    ]
    out = {}
    with httpx.Client(timeout=30) as http:
        rec = Recorder("fmp", http, 1.0, refresh)
        for c in load_universe():
            for name in endpoints:
                status, body = rec.get(
                    f"{name}_{c.ticker}",
                    f"https://financialmodelingprep.com/stable/{name}",
                    params={"symbol": c.ticker, "apikey": key},
                )
                try:
                    data = json.loads(body)
                except ValueError:
                    data = None
                entry = {"status": status, "shape": _shape(body)}
                if status != 200 or not isinstance(data, list):
                    entry["error"] = body[:200].decode(errors="replace")
                elif data:
                    entry["fields"] = sorted(data[0])
                    dates = [str(d.get("date") or d.get("publishedDate") or "") for d in data]
                    cutoff = (TODAY - timedelta(days=30)).isoformat()
                    entry["last_30d"] = sum(d >= cutoff for d in dates)
                    entry["newest"] = max(dates)
                out[f"{c.ticker} {name}"] = entry
    _save("fmp", {"results": out, "calls": rec.calls})


# --- TipRanks MCP (free key: 50 requests a month) ------------------------------------------
# Terms §3 forbid keeping the data beyond transient operational caching. The probe keeps only
# field names and counts; raw responses aren't written.


@app.command()
def tipranks() -> None:
    url = "https://mcp.tipranks.com/mcp/"
    params = {"apikey": _env("TIPRANKS_API_KEY")}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    out: dict = {}
    with httpx.Client(timeout=60, headers=headers) as http:

        def rpc(method: str, body_params: dict, rpc_id: int | None) -> tuple[int, dict | None]:
            message = {"jsonrpc": "2.0", "method": method, "params": body_params}
            if rpc_id is not None:
                message["id"] = rpc_id
            r = http.post(url, params=params, json=message)
            if "mcp-session-id" in r.headers:
                http.headers["Mcp-Session-Id"] = r.headers["mcp-session-id"]
            text = r.text
            if "text/event-stream" in r.headers.get("content-type", ""):
                lines = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
                text = lines[-1] if lines else ""
            try:
                return r.status_code, json.loads(text) if text.strip() else None
            except ValueError:
                return r.status_code, {"raw": text[:200]}

        status, init = rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "invest-agent-probe", "version": "0.1"},
            },
            1,
        )
        out["initialize"] = {
            "status": status,
            "server": (init or {}).get("result", {}).get("serverInfo"),
        }
        rpc("notifications/initialized", {}, None)
        status, tools = rpc("tools/list", {}, 2)
        listed = (tools or {}).get("result", {}).get("tools", [])
        out["tools"] = {
            "status": status,
            "count": len(listed),
            "analyst_or_news": sorted(
                t["name"] for t in listed if re.search(r"analyst|news|article|rating", t["name"])
            ),
        }
        for n, (tool, args) in enumerate(
            [
                ("get_recent_analyst_ratings", {"ticker": "GOOGL"}),
                ("get_latest_news", {"ticker": "GOOGL"}),
            ],
            start=3,
        ):
            status, result = rpc("tools/call", {"name": tool, "arguments": args}, n)
            content = (result or {}).get("result", {}).get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            parsed = None
            for t in texts:
                try:
                    parsed = json.loads(t)
                    break
                except ValueError:
                    continue
            out[tool] = {
                "status": status,
                "error": (result or {}).get("error"),
                "text_chars": sum(map(len, texts)),
                "shape": _shape(json.dumps(parsed).encode()) if parsed is not None else None,
                "item_fields": _item_fields(parsed),
            }
    _save("tipranks", out)


def _item_fields(data: object) -> list[str] | None:
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return sorted(value[0])
        return sorted(data)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return sorted(data[0])
    return None


# --- all -------------------------------------------------------------------------------------


@app.command("all")
def run_all(refresh: bool = False) -> None:
    for name, fn in [
        ("sec", lambda: sec(refresh)),
        ("finnhub", lambda: finnhub(refresh)),
        ("gnews", lambda: gnews(refresh)),
        ("gdelt", lambda: gdelt(refresh)),
        ("ir", ir),
        ("yfinance", yfinance_probe),
        ("tiingo", lambda: tiingo(refresh)),
        ("alphavantage", lambda: alphavantage(refresh)),
        ("fred", lambda: fred(refresh)),
        ("alpaca", lambda: alpaca(refresh)),
        ("eodhd", lambda: eodhd(refresh)),
        ("fmp", lambda: fmp(refresh)),
    ]:
        start = time.monotonic()
        try:
            fn()
            typer.echo(f"{name}: done in {time.monotonic() - start:.0f}s")
        except Exception as e:  # one broken source must not stop the others
            typer.echo(f"{name}: FAILED {type(e).__name__}: {str(e)[:200]}")


# --- helpers ---------------------------------------------------------------------------------


def _time_range(items: list[dict]) -> dict:
    stamps = [i["datetime"] for i in items]
    if not stamps:
        return {}
    return {
        "first": datetime.fromtimestamp(min(stamps), UTC).isoformat(),
        "last": datetime.fromtimestamp(max(stamps), UTC).isoformat(),
    }


def _news_stats(items: list[dict], time_key: str, url_key: str, outlet_key: str) -> dict:
    return {
        "count": len(items),
        "fields": _fields(items),
        "time_example": items[0].get(time_key) if items else None,
        "outlets": Counter(i.get(outlet_key) for i in items).most_common(15),
        "url_hosts": Counter(urlsplit(i.get(url_key) or "").netloc for i in items).most_common(10),
    }


def _fields(items: list[dict]) -> dict[str, str]:
    types: dict[str, set[str]] = {}
    for item in items:
        for k, v in item.items():
            types.setdefault(k, set()).add(type(v).__name__)
    return {k: "|".join(sorted(v)) for k, v in sorted(types.items())}


def _word_stats(texts) -> dict:
    counts = [len(t.split()) for t in texts if isinstance(t, str) and t]
    if not counts:
        return {"n": 0}
    return {"n": len(counts), "median": statistics.median(counts), "max": max(counts)}


def _shape(body: bytes) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return f"non-json, {len(body)} bytes"
    if isinstance(data, list):
        return f"list of {len(data)}"
    if isinstance(data, dict):
        return "keys: " + ", ".join(sorted(data)[:12])
    return type(data).__name__


def _text(html: str) -> str:
    if not html.strip():
        return ""
    try:
        return lxml.html.fromstring(html).text_content()
    except (lxml.etree.LxmlError, ValueError):
        return html


def _html_words(body: bytes) -> int:
    try:
        return len(lxml.html.fromstring(body).text_content().split())
    except (lxml.etree.LxmlError, ValueError):
        return 0


def _safe(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", label)


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise typer.BadParameter(f"{name} is not set")
    return value


def _save(source: str, summary: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"probed_at": datetime.now(UTC).isoformat(timespec="seconds"), **summary}
    (OUT / f"{source}.json").write_text(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    app()
