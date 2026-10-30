"""F1 content measures over 30 days, on the two kept news sources: our fetch of every Finnhub
link (data/probe/bodies30/, from `probe_news_bodies.py backfill`) and Alpaca News content
(from `probe_sources.py alpaca`):

- how many readable bodies remain after dedupe, and the full / partial / headline_only split
- what each source adds that the others don't
- independent reporting vs analysis vs press releases, by the page's provider
- yfinance rating changes linked to a sentence in an article that names the firm

Quotes are copied from article text, never written. A rating change with no such sentence
stays unlinked.

    uv run python scripts/probe_content.py
"""

import csv
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import lxml.html
import typer

from invest_agent.config import load_universe

PROBE = Path("data/probe")
DIR = PROBE / "bodies30"
SHINGLE = 8
COPY_CONTAINMENT = 0.8
COPY_WINDOW = timedelta(hours=72)

# Provider (JSON-LD provider/publisher, else host) → article type. Unlisted → unclassified.
TYPES = {
    "press_release": [
        "pr newswire",
        "prnewswire",
        "business wire",
        "businesswire",
        "globenewswire",
        "globe newswire",
        "accesswire",
        "access newswire",
        "newsfile",
        "cnw",
    ],
    "reporting": [
        "reuters",
        "afp",
        "associated press",
        "bloomberg",
        "yahoo finance",
        "cnbc",
        "axios",
        "telegraph",
        "fortune",
        "business insider",
        "barron",
        "marketwatch",
        "wall street journal",
        "wsj",
        "investor's business daily",
        "fox business",
        "techcrunch",
        "the verge",
        "engadget",
        "cbs",
        "the information",
        "financial times",
        "decrypt",
        "coindesk",
        "pymnts",
        "thestreet",
        "the street",
        "mt newswires",
        "investing.com",
        "quartz",
        "wwd",
        "the real deal",
        "bisnow",
        "techradar",
        "variety",
        "deadline",
        "semafor",
        "politico",
        "the globe and mail",
        "benzinga",
        "seeking alpha news",
    ],
    "analysis_opinion": [
        "motley fool",
        "fool.com",
        "seeking alpha",
        "seekingalpha",
        "zacks",
        "24/7 wall st",
        "247wallst",
        "simply wall st",
        "insider monkey",
        "investorplace",
        "barchart",
        "gurufocus",
        "trefis",
        "tikr",
        "stocktwits",
        "investopedia",
        "kiplinger",
        "tipranks",
        "validea",
        "moneywise",
        "gobankingrates",
        "stockstory",
        "proactiveinvestors",
    ],
    "auto_generated": ["chartmill", "marketbeat", "fiscal.ai"],
}
REASON_CUES = re.compile(
    r"\b(cit(?:ed|ing|es)|because|due to|driven by|amid|on (?:concerns?|expectations?)|"
    r"after|as |said|says|wrote|writes|noted|notes|believes?|expects?|sees|argues?|warn)",
    re.I,
)
ACTION_TERMS = re.compile(
    r"\b(price target|target|price objective|rating|upgrad\w*|downgrad\w*|overweight|"
    r"underweight|outperform|underperform|market perform|equal[- ]weight|neutral|buy|hold|"
    r"sell)\b|\$\d",
    re.I,
)
# Related-link lists, comment widgets and call transcripts name firms without giving a view.
NOT_A_VIEW = re.compile(
    r"original article|view comments|\[https?://|your next question|question comes from|"
    r"\boperator\b|asked (?:if|whether|about)",
    re.I,
)
FIRM_ALIASES = {
    "JP Morgan": ["JPMorgan", "J.P. Morgan", "JP Morgan"],
    "Citigroup": ["Citigroup", "Citi"],
    "BofA Securities": ["BofA", "Bank of America"],
    "TD Cowen": ["TD Cowen", "Cowen"],
    "Evercore ISI Group": ["Evercore"],
    "Truist Securities": ["Truist"],
    "Keybanc": ["KeyBanc"],
    "BMO Capital": ["BMO"],
    "Wells Fargo": ["Wells Fargo"],
    "Goldman Sachs": ["Goldman"],
    "Morgan Stanley": ["Morgan Stanley"],
    "Deutsche Bank": ["Deutsche Bank"],
    "Raymond James": ["Raymond James"],
    "Piper Sandler": ["Piper Sandler"],
    "Cantor Fitzgerald": ["Cantor"],
    "Loop Capital": ["Loop Capital"],
    "Rosenblatt": ["Rosenblatt"],
    "Bernstein": ["Bernstein"],
}

app = typer.Typer(add_completion=False)


COMMON_SHINGLE = 50  # a shingle in more docs than this is boilerplate and is ignored


@app.command()
def main() -> None:
    rows = [json.loads(line) for line in (DIR / "results.jsonl").open()]
    counts = json.loads((DIR / "counts.json").read_text())
    universe = {c.ticker: c for c in load_universe()}
    names = {t: _pattern([t, *c.aliases]) for t, c in universe.items()}
    docs = _finnhub_docs(rows) + _alpaca_docs(universe)
    groups = _copy_groups(docs)

    finnhub = [d for d in docs if d["source"] == "finnhub"]
    unique_finnhub = {groups[d["id"]] for d in finnhub}
    discovered = sum(
        len(json.loads((PROBE / "raw" / "finnhub" / f"company_news_{s}.json").read_text()))
        for s in ("GOOG", "GOOGL", "META", "RDDT")
    )
    funnel = {
        "discovered (4 Finnhub feeds)": discovered,
        "unique Finnhub ids": counts["discovered_ids"],
        "unique headlines": counts["unique_headlines"],
        "page requested": sum(r["fetch_error"] != "host_blocked" for r in rows),
        "readable body (full or partial)": len(finnhub),
        "after removing copies": len(unique_finnhub),
    }
    status = Counter(r["status"] for r in rows)
    by_outlet = defaultdict(Counter)
    for r in rows:
        by_outlet[r["outlet"]][r["status"]] += 1
    failures = Counter(
        r["fetch_error"] or "+".join(r["reasons"]) for r in rows if r["status"] != "full"
    )

    # One entry per copy group, named after its earliest member.
    by_group: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        by_group[groups[d["id"]]].append(d)
    union = []
    for members in by_group.values():
        first = min(members, key=lambda d: d["published"])
        tickers = sorted({t for d in members for t in d["tickers"]})
        union.append(
            {
                "sources": sorted({d["source"] for d in members}),
                "full": any(d["status"] == "full" for d in members),
                "tickers": tickers,
                "names_company": any(names[t].search(d["text"]) for d in members for t in tickers),
                # About the company: named in the headline, or 3+ times in the body.
                "about": sorted(
                    {
                        t
                        for d in members
                        for t in tickers
                        if names[t].search(d["headline"]) or len(names[t].findall(d["text"])) >= 3
                    }
                ),
                "kind": _kind(next((d["provider"] for d in members if d["provider"] != "?"), "?")),
                "provider": first["provider"],
            }
        )
    readable = [u for u in union if u["names_company"]]
    sources = ("finnhub", "alpaca")
    combined = {
        "docs": Counter(d["source"] for d in docs),
        "unique texts naming the company": len(readable),
        "of which full": sum(u["full"] for u in readable),
        "full and about the company, per ticker": {
            t: sum(1 for u in union if u["full"] and t in u["about"]) for t in universe
        },
        "full and about the company, only this source": {
            s: sum(1 for u in union if u["full"] and u["about"] and u["sources"] == [s])
            for s in ("finnhub", "alpaca")
        },
        "type mix (full, about the company)": Counter(
            u["kind"] for u in union if u["full"] and u["about"]
        ),
        "full, per ticker": {
            t: sum(1 for u in readable if u["full"] and t in u["tickers"]) for t in universe
        },
        "full, has it": {
            s: sum(1 for u in readable if u["full"] and s in u["sources"]) for s in sources
        },
        "full, only this source": {
            s: sum(1 for u in readable if u["full"] and u["sources"] == [s]) for s in sources
        },
        "type mix (full, names company)": Counter(u["kind"] for u in readable if u["full"]),
        "providers (full, names company)": Counter(
            u["provider"] for u in readable if u["full"]
        ).most_common(30),
        "unclassified providers": Counter(
            u["provider"] for u in readable if u["full"] and u["kind"] == "unclassified"
        ).most_common(20),
    }

    analyst = _analyst(docs, names)
    result = {
        "funnel": funnel,
        "status": status,
        "by_outlet": by_outlet,
        "failures": failures.most_common(15),
        "combined": combined,
        "analyst": analyst,
    }
    (PROBE / "content30.json").write_text(json.dumps(result, indent=1, default=str))
    _explorer(rows, docs, groups, names, analyst, funnel)
    typer.echo(
        json.dumps({k: v for k, v in result.items() if k != "analyst"}, indent=1, default=str)
    )
    typer.echo(json.dumps(analyst["summary"], indent=1))
    typer.echo(f"open {(PROBE / 'explorer.html').resolve()}")


TEXT_LIMIT = 20_000


def _explorer(
    rows: list[dict],
    docs: list[dict],
    groups: dict[str, str],
    names: dict[str, re.Pattern],
    analyst: dict,
    funnel: dict,
) -> None:
    """A local page for browsing the articles (data/probe/explorer.html) and a CSV of the same
    rows without text (data/probe/articles30.csv). Both hold article text or links, so they stay
    in the gitignored data/ folder."""
    by_id = {d["id"]: d for d in docs}

    def entry(doc: dict | None, **fields) -> dict:
        text = doc["text"] if doc else ""
        tickers = fields["tickers"]
        return {
            **fields,
            "provider": doc["provider"] if doc else fields["host"],
            "kind": _kind(doc["provider"] if doc else fields["host"]),
            "copy": bool(doc) and groups[doc["id"]] != doc["id"],
            "names_company": any(names[t].search(text) for t in tickers) if text else False,
            "text": text[:TEXT_LIMIT],
        }

    out = []
    for r in rows:
        out.append(
            entry(
                by_id.get(f"finnhub:{r['id']}"),
                source="finnhub",
                tickers=r["tickers"],
                published=_utc(r["published"]).isoformat(),
                outlet=r["outlet"] or "?",
                host=r["final_host"] or "",
                status=r["status"],
                why=r["fetch_error"] or ",".join(r["reasons"]),
                words=r["words"],
                headline=r["headline"],
                url=r["final_url"] or r["url"],
            )
        )
    seen = set()
    for path in sorted((PROBE / "raw" / "alpaca").glob("news_*.json")):
        if path.stem.count("_") != 1:  # news_GOOG.json, not the cached pages
            continue
        for item in json.loads(path.read_text()):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            doc = by_id.get(f"alpaca:{item['id']}")
            out.append(
                entry(
                    doc,
                    source="alpaca",
                    tickers=doc["tickers"] if doc else [path.stem.split("_")[1]],
                    published=_utc(item["created_at"]).isoformat(),
                    outlet=item.get("source") or "?",
                    host=urlsplit(item.get("url") or "").netloc,
                    status=doc["status"] if doc else "headline_only",
                    why="" if doc else "no content",
                    words=len(doc["text"].split()) if doc else 0,
                    headline=item["headline"],
                    url=item.get("url"),
                )
            )

    changes = [
        {
            "date": c["GradeDate"],
            "ticker": c["ticker"],
            "firm": c["Firm"],
            "from_grade": c["FromGrade"],
            "to_grade": c["ToGrade"],
            "prior_target": c["priorPriceTarget"],
            "target": c["currentPriceTarget"],
            "state": c["state"],
            "passages": c["sentences"],
        }
        for c in sorted(analyst["changes"], key=lambda c: c["GradeDate"], reverse=True)
    ]
    subtitle = (
        f"GOOG, META, RDDT · last 30 days · {funnel['unique headlines']:,} unique Finnhub "
        f"headlines fetched, plus Alpaca (Benzinga) content · built "
        f"{datetime.now().strftime('%b %d %H:%M')}"
    )
    data = json.dumps({"subtitle": subtitle, "rows": out, "analyst": changes}, default=str)
    template = (Path(__file__).parent / "probe_explorer.html").read_text()
    (PROBE / "explorer.html").write_text(template.replace("__DATA__", data.replace("</", "<\\/")))

    columns = [
        "source",
        "tickers",
        "published",
        "outlet",
        "host",
        "provider",
        "kind",
        "status",
        "why",
        "words",
        "copy",
        "names_company",
        "headline",
        "url",
    ]
    with (PROBE / "articles30.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in out:
            writer.writerow([" ".join(row[c]) if c == "tickers" else row[c] for c in columns])


def _finnhub_docs(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": f"finnhub:{r['id']}",
            "source": "finnhub",
            "tickers": r["tickers"],
            "published": _utc(r["published"]),
            "headline": r["headline"],
            "url": r["final_url"],
            "status": r["status"],
            "provider": _provider(r),
            "text": (DIR / "text" / f"{r['id']}.txt").read_text(),
        }
        for r in rows
        if r["status"] in ("full", "partial")
    ]


def _alpaca_docs(universe: dict) -> list[dict]:
    docs = {}
    for t in universe:
        path = PROBE / "raw" / "alpaca" / f"news_{t}.json"
        for item in json.loads(path.read_text()) if path.exists() else []:
            text = _html_text(item.get("content") or "")
            if not text.strip():
                continue
            tickers = sorted(
                {"GOOG" if s == "GOOGL" else s for s in item["symbols"]} & set(universe)
            )
            docs[item["id"]] = {
                "id": f"alpaca:{item['id']}",
                "source": "alpaca",
                "tickers": tickers or [t],
                "published": _utc(item["created_at"]),
                "headline": item["headline"],
                "url": item.get("url"),
                "status": _length_status(text),
                "provider": "Benzinga" if item.get("source") == "benzinga" else item.get("source"),
                "text": text,
            }
    return list(docs.values())


def _copy_groups(docs: list[dict]) -> dict[str, str]:
    """Copies share a group: 8-word shingle containment >= 0.8 within 72 h and a shared
    ticker. Returns doc id -> group id (the earliest member's id)."""
    shingles = {d["id"]: _shingles(d["text"]) for d in docs}
    index = defaultdict(set)
    for d in docs:
        for sh in shingles[d["id"]]:
            index[sh].add(d["id"])
    parent = {d["id"]: d["id"] for d in docs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_id = {d["id"]: d for d in docs}
    for d in docs:
        overlap = Counter(
            o
            for sh in shingles[d["id"]]
            if len(index[sh]) <= COMMON_SHINGLE
            for o in index[sh]
            if o != d["id"]
        )
        for other, shared in overlap.items():
            o = by_id[other]
            if not set(o["tickers"]) & set(d["tickers"]):
                continue
            if abs(o["published"] - d["published"]) > COPY_WINDOW:
                continue
            smaller = min(len(shingles[other]), len(shingles[d["id"]]))
            if smaller and shared / smaller >= COPY_CONTAINMENT:
                a, b = find(d["id"]), find(other)
                if a != b:
                    first = min(a, b, key=lambda x: by_id[x]["published"])
                    parent[a] = parent[b] = first
    return {d["id"]: find(d["id"]) for d in docs}


def _length_status(text: str) -> str:
    # These sources give no paywall signals, so only the length rule applies.
    return "full" if len(text.split()) >= 150 else "partial"


def _html_text(html: str) -> str:
    if not html.strip():
        return ""
    try:
        return lxml.html.fromstring(html).text_content()
    except (lxml.etree.LxmlError, ValueError):
        return html


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None) - (parsed.utcoffset() or timedelta(0))


def _shingles(text: str) -> set[int]:
    words = re.findall(r"\w+", text.lower())
    return {
        int(hashlib.md5(" ".join(words[i : i + SHINGLE]).encode()).hexdigest()[:12], 16)
        for i in range(max(0, len(words) - SHINGLE + 1))
    }


def _provider(row: dict) -> str:
    path = DIR / "html" / f"{row['id']}.html.gz"
    if path.exists():
        try:
            tree = lxml.html.fromstring(gzip.decompress(path.read_bytes()))
        except (lxml.etree.LxmlError, ValueError):
            tree = None
        for script in (
            tree.xpath('//script[@type="application/ld+json"]') if tree is not None else []
        ):
            try:
                data = json.loads(script.text_content())
            except ValueError:
                continue
            for node in data if isinstance(data, list) else [data]:
                if not isinstance(node, dict):
                    continue
                for key in ("provider", "publisher"):
                    value = node.get(key)
                    name = value.get("name") if isinstance(value, dict) else value
                    if isinstance(name, str) and name and not name.startswith("Yahoo! Finance"):
                        return name
    return row["final_host"] or "?"


def _kind(provider: str) -> str:
    p = provider.lower()
    for kind, keys in TYPES.items():
        if any(k in p for k in keys):
            return kind
    return "unclassified"


def _analyst(docs: list[dict], names: dict[str, re.Pattern]) -> dict:
    changes = json.loads((PROBE / "yfinance_ratings_30d.json").read_text())
    by_ticker = defaultdict(list)
    for d in docs:
        for t in d["tickers"]:
            by_ticker[t].append(d)
    linked = []
    for change in changes:
        firm = _pattern(FIRM_ALIASES.get(change["Firm"], [change["Firm"]]))
        when = datetime.fromisoformat(change["GradeDate"])
        named = [
            d
            for d in by_ticker[change["ticker"]]
            if timedelta(days=-1) <= d["published"] - when <= timedelta(days=2)
            and (firm.search(d["headline"]) or firm.search(d["text"]))
        ]
        company = names[change["ticker"]]
        sentences = []
        for d in named:
            split = re.split(r"(?<=[.!?])\s+", d["text"])
            for i, sentence in enumerate(split):
                # The firm's sentence plus the next one: the reason often follows the action.
                passage = " ".join(split[i : i + 2])
                if (
                    firm.search(sentence)
                    and ACTION_TERMS.search(sentence)
                    and REASON_CUES.search(passage)
                    and (company.search(passage) or company.search(d["headline"]))
                    and not NOT_A_VIEW.search(passage)
                    and len(passage.split()) <= 120
                ):
                    sentences.append({"source": d["source"], "url": d["url"], "passage": passage})
        linked.append(
            {
                **change,
                "state": "candidate passage"
                if sentences
                else "firm named, no candidate"
                if named
                else "no article",
                "articles": len(named),
                "sources": sorted({d["source"] for d in named}),
                "sentences": sentences[:4],
            }
        )
    return {
        "summary": {"rating changes": len(changes), **Counter(c["state"] for c in linked)},
        "changes": linked,
    }


def _pattern(names: list[str]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I)


if __name__ == "__main__":
    app()
