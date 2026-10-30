"""F1 news body test: run the F2 fetch path on 100 real article candidates.

Reads the Finnhub probe output, so run `probe_sources.py finnhub` first. Everything goes to
data/probe/bodies/ (gitignored): article text stays local, only counts go in the docs.

    uv run python scripts/probe_news_bodies.py sample
    uv run python scripts/probe_news_bodies.py fetch     # resumable
    uv run python scripts/probe_news_bodies.py review    # 30-row sheet to check by hand
    uv run python scripts/probe_news_bodies.py report    # markdown tables
"""

import csv
import gzip
import hashlib
import json
import random
import re
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import trafilatura
import typer

from invest_agent.ingest.http import PoliteClient
from invest_agent.ingest.news_body import EXTRACTOR, fetch_body

PROBE = Path("data/probe")
DIR = PROBE / "bodies"
CANDIDATES = DIR / "candidates.json"
RESULTS = DIR / "results.jsonl"
REVIEW = DIR / "review.csv"
DIR30 = PROBE / "bodies30"
QUOTA = {"GOOG": 40, "META": 40, "RDDT": 20}

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def sample(n: int = 100, seed: int = 7) -> None:
    """Pick candidates spread over tickers and outlets, round-robin, with a fixed seed."""
    rng = random.Random(seed)
    picked, seen = [], set()
    for ticker, quota in QUOTA.items():
        items = json.loads((PROBE / "raw" / "finnhub" / f"company_news_{ticker}.json").read_text())
        by_outlet = defaultdict(list)
        for item in items:
            if item["url"] not in seen:
                by_outlet[item.get("source") or "?"].append(item)
        for group in by_outlet.values():
            rng.shuffle(group)
        chosen = []
        while len(chosen) < quota and any(by_outlet.values()):
            for outlet in sorted(by_outlet):
                if by_outlet[outlet] and len(chosen) < quota:
                    chosen.append(by_outlet[outlet].pop())
        for item in chosen:
            seen.add(item["url"])
            picked.append(
                {
                    "id": hashlib.sha1(item["url"].encode()).hexdigest()[:12],
                    "source": "finnhub",
                    "ticker": ticker,
                    "outlet": item.get("source"),
                    "headline": item.get("headline"),
                    "published": datetime.fromtimestamp(item["datetime"], UTC).isoformat(),
                    "url": item["url"],
                    "feed_snippet_words": len((item.get("summary") or "").split()),
                }
            )
    DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text(json.dumps(picked[:n], indent=1))
    typer.echo(f"{len(picked[:n])} candidates: {Counter(c['outlet'] for c in picked[:n])}")


@app.command()
def fetch() -> None:
    _fetch_all(json.loads(CANDIDATES.read_text()), DIR)


@app.command()
def backfill(days: int = 30, block_after: int = 20) -> None:
    """Every Finnhub item in the window, one per headline, through the fetch path. Resumable.

    After `block_after` 403s in a row from one host, that host isn't asked again; the rest of
    its outlet's items are recorded as `host_blocked`."""
    items, tickers = {}, defaultdict(set)
    for symbol in ("GOOG", "GOOGL", "META", "RDDT"):
        for item in json.loads(
            (PROBE / "raw" / "finnhub" / f"company_news_{symbol}.json").read_text()
        ):
            items[item["id"]] = item
            tickers[item["id"]].add("GOOG" if symbol == "GOOGL" else symbol)
    by_headline = {}
    for key, item in sorted(items.items()):
        by_headline.setdefault(_norm(item["headline"]), (key, item))
    candidates = [
        {
            "id": hashlib.sha1(item["url"].encode()).hexdigest()[:12],
            "finnhub_id": key,
            "tickers": sorted(tickers[key]),
            "outlet": item.get("source"),
            "headline": item.get("headline"),
            "published": datetime.fromtimestamp(item["datetime"], UTC).isoformat(),
            "url": item["url"],
        }
        for key, item in by_headline.values()
    ]
    candidates.sort(key=lambda c: c["published"])
    DIR30.mkdir(parents=True, exist_ok=True)
    (DIR30 / "counts.json").write_text(
        json.dumps({"discovered_ids": len(items), "unique_headlines": len(candidates)})
    )
    _fetch_all(candidates, DIR30, block_after)


def _fetch_all(
    candidates: list[dict], directory: Path, block_after: int | None = None, workers: int = 6
) -> None:
    """Hosts run in parallel; PoliteClient keeps each host at one request at a time."""
    results = directory / "results.jsonl"
    done = {json.loads(line)["id"] for line in results.open()} if results.exists() else set()
    (directory / "html").mkdir(parents=True, exist_ok=True)
    (directory / "text").mkdir(exist_ok=True)
    streak: Counter[str] = Counter()
    blocked: set[str] = set()
    outlet_host: dict[str, Counter[str]] = defaultdict(Counter)
    written = 0

    def record(row: dict) -> None:
        nonlocal written
        host = row["final_host"]
        if row["fetch_error"] != "host_blocked":
            outlet_host[row["outlet"]][host] += 1
            streak[host] = streak[host] + 1 if row["fetch_error"] == "http_403" else 0
            if block_after and streak[host] >= block_after:
                blocked.add(host)
        out.write(json.dumps(row) + "\n")
        out.flush()
        written += 1
        if written % 50 == 0:
            typer.echo(f"{written + len(done)} / {len(candidates)}")

    with PoliteClient() as client, results.open("a") as out, ThreadPoolExecutor(workers) as pool:
        pending: set = set()
        for c in candidates:
            if c["id"] in done:
                continue
            usual = outlet_host[c["outlet"]].most_common(1)
            if usual and usual[0][0] in blocked:
                record(
                    {
                        **c,
                        "final_url": None,
                        "final_host": usual[0][0],
                        "sitename": None,
                        "status": "headline_only",
                        "fetch_error": "host_blocked",
                        "words": 0,
                        "reasons": [],
                        "jsonld_free": None,
                        "jsonld_word_count": None,
                        "fetch_ms": 0,
                        "extract_ms": 0,
                        "extractor": EXTRACTOR,
                    }
                )
                continue
            pending.add(pool.submit(_fetch_one, client, c, directory))
            if len(pending) >= workers * 2:
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    record(future.result())
        for future in as_completed(pending):
            record(future.result())


def _fetch_one(client: PoliteClient, c: dict, directory: Path) -> dict:
    start = time.monotonic()
    body, page, last_url = fetch_body(client, c["url"])
    total_ms = int((time.monotonic() - start) * 1000)
    site = None
    if page is not None:
        (directory / "html" / f"{c['id']}.html.gz").write_bytes(gzip.compress(page.body))
        meta = trafilatura.extract_metadata(page.body)
        site = meta.sitename if meta else None
    if body.text:
        (directory / "text" / f"{c['id']}.txt").write_text(body.text)
    return {
        **c,
        "final_url": last_url,
        "final_host": urlsplit(last_url).netloc,
        "sitename": site,
        "status": body.status,
        "fetch_error": body.fetch_error,
        "words": body.words,
        "reasons": list(body.reasons),
        "jsonld_free": body.jsonld.accessible_for_free,
        "jsonld_word_count": body.jsonld.word_count,
        "fetch_ms": page.elapsed_ms if page else total_ms,
        "extract_ms": total_ms - page.elapsed_ms if page else 0,
        "extractor": EXTRACTOR,
    }


def _norm(headline: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()


# Events in the 7-day window, found by reading the headlines. Filing and IR coverage comes from
# the SEC and IR probes; this checks whether news alone gives a full-text article.
EVENTS = {
    "GOOG Q3 results (Oct 28)": (
        "GOOG",
        r"alphabet.{0,40}(earnings|results|quarter)|google.{0,20}earnings",
    ),
    "META $95B stock and preferred offering (Oct 26-27)": (
        "META",
        r"offering|equity raise|\$95 billion",
    ),
    "META Muse AI agent": ("META", r"\bmuse\b"),
    "Anthropic IPO filing (Google is an investor)": ("GOOG", r"anthropic.{0,40}(ipo|files)"),
    "Google pays SpaceX for compute": (
        "GOOG",
        r"spacex.{0,40}(compute|google)|google.{0,40}spacex",
    ),
    "RDDT Q3 results (Oct 29)": ("RDDT", r"earnings|results|q3"),
    "RDDT US user growth slows": ("RDDT", r"user growth|slower growth"),
}


@app.command()
def events(per_event: int = 6) -> None:
    done = {r["id"]: r for r in _results()}
    path = DIR / "events.jsonl"
    if path.exists():
        done |= {r["id"]: r for r in map(json.loads, path.open())}
    lines = []
    with PoliteClient() as client, path.open("a") as out:
        for event, (ticker, pattern) in EVENTS.items():
            items = json.loads(
                (PROBE / "raw" / "finnhub" / f"company_news_{ticker}.json").read_text()
            )
            matches = [i for i in items if re.search(pattern, i["headline"], re.I)]
            by_outlet = defaultdict(list)
            for i in matches:
                by_outlet[i.get("source")].append(i)
            picked = []
            while len(picked) < per_event and any(by_outlet.values()):
                for group in by_outlet.values():
                    if group and len(picked) < per_event:
                        picked.append(group.pop(0))
            rows = []
            for item in picked:
                key = hashlib.sha1(item["url"].encode()).hexdigest()[:12]
                if key not in done:
                    body, _, last_url = fetch_body(client, item["url"])
                    done[key] = {
                        "id": key,
                        "event": event,
                        "outlet": item.get("source"),
                        "final_host": urlsplit(last_url).netloc,
                        "status": body.status,
                        "fetch_error": body.fetch_error,
                        "words": body.words,
                    }
                    out.write(json.dumps(done[key]) + "\n")
                rows.append(done[key])
            full = [r for r in rows if r["status"] == "full"]
            lines.append(
                [
                    event,
                    len(matches),
                    len(rows),
                    len(full),
                    sum(r["status"] == "partial" for r in rows),
                    ", ".join(sorted({r["final_host"] for r in full})) or "none",
                ]
            )
    typer.echo(_table(["event", "headlines", "fetched", "full", "partial", "full from"], lines))


@app.command()
def review(size: int = 30, seed: int = 7) -> None:
    """Sheet for the hand check: about two thirds `full`, the rest `partial`, many sites."""
    rows = _results()
    rng = random.Random(seed)
    picks = []
    for status, want in (("full", size * 2 // 3), ("partial", size - size * 2 // 3)):
        pool = [r for r in rows if r["status"] == status]
        rng.shuffle(pool)
        pool.sort(key=lambda r: sum(p["final_host"] == r["final_host"] for p in picks))
        picks += pool[:want]
    if len(picks) < size:
        rest = [r for r in rows if r["status"] in ("full", "partial") and r not in picks]
        picks += rest[: size - len(picks)]
    with REVIEW.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "n",
                "id",
                "status",
                "words",
                "site",
                "url",
                "text_file",
                "complete",
                "main_text_only",
                "notes",
            ]
        )
        for n, r in enumerate(picks, 1):
            writer.writerow(
                [
                    n,
                    r["id"],
                    r["status"],
                    r["words"],
                    r["sitename"] or r["final_host"],
                    r["final_url"],
                    f"text/{r['id']}.txt",
                    "",
                    "",
                    "",
                ]
            )
    typer.echo(f"wrote {REVIEW} with {len(picks)} rows")


@app.command()
def report() -> None:
    rows = _results()
    typer.echo(f"## {len(rows)} articles\n")
    typer.echo(_table(["body_status", "n"], Counter(r["status"] for r in rows).most_common()))

    by_site = defaultdict(list)
    for r in rows:
        by_site[_site(r)].append(r)
    lines = []
    for site, group in sorted(by_site.items(), key=lambda kv: -len(kv[1])):
        s = Counter(r["status"] for r in group)
        why = Counter(
            r["fetch_error"] or ",".join(r["reasons"]) for r in group if r["status"] != "full"
        )
        lines.append(
            [
                site,
                len(group),
                s["full"],
                s["partial"],
                s["headline_only"],
                "; ".join(f"{k} {v}" for k, v in why.most_common(3)),
            ]
        )
    typer.echo(
        "\n" + _table(["site", "n", "full", "partial", "headline_only", "why not full"], lines)
    )

    typer.echo(
        "\n"
        + _table(
            ["finnhub outlet", "n", "full"],
            [
                [o, len(g), sum(r["status"] == "full" for r in g)]
                for o, g in _group(rows, "outlet").items()
            ],
        )
    )
    errors = Counter(r["fetch_error"] for r in rows if r["fetch_error"])
    reasons = Counter(x for r in rows for x in r["reasons"])
    typer.echo(
        "\n"
        + _table(
            ["fetch_error / partial reason", "n"], [*errors.most_common(), *reasons.most_common()]
        )
    )
    fetched = [r for r in rows if r["final_url"]]
    typer.echo(
        "\n"
        + _table(
            ["timing", "p50 ms", "p95 ms"],
            [
                ["fetch", *_pcts([r["fetch_ms"] for r in fetched])],
                ["extract", *_pcts([r["extract_ms"] for r in fetched])],
            ],
        )
    )
    words = [r["words"] for r in rows if r["status"] == "full"]
    if words:
        typer.echo(f"\nfull articles: median {statistics.median(words)} words, min {min(words)}")
    if REVIEW.exists():
        checked = [r for r in csv.DictReader(REVIEW.open()) if r["complete"]]
        if checked:
            yes = lambda k: sum(r[k].strip().lower().startswith("y") for r in checked)  # noqa: E731
            typer.echo(
                f"\nhand check ({len(checked)}): complete {yes('complete')}, "
                f"main text only {yes('main_text_only')}"
            )


def _results() -> list[dict]:
    return [json.loads(line) for line in RESULTS.open()]


def _site(r: dict) -> str:
    host = r["final_host"] or "(no page)"
    return f"{host} / {r['sitename']}" if r["sitename"] and host.endswith("yahoo.com") else host


def _group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    out = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return dict(sorted(out.items(), key=lambda kv: -len(kv[1])))


def _pcts(values: list[int]) -> list[int]:
    if len(values) < 2:
        return values * 2 or [0, 0]
    q = statistics.quantiles(values, n=20)
    return [int(statistics.median(values)), int(q[18])]


def _table(header: list[str], rows) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


if __name__ == "__main__":
    app()
