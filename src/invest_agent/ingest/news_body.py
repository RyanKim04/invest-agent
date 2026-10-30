"""Publisher page → article text and `body_status` (rules: docs/01-system-overview.md#news-bodies).

The thresholds are starting values that F1 tunes."""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

import lxml.html
import trafilatura

from invest_agent.ingest.http import FetchError, Page, PoliteClient

EXTRACTOR = f"trafilatura-{trafilatura.__version__}"
MIN_WORDS = 150
MIN_WORDCOUNT_RATIO = 0.7

# Anywhere in the extracted text.
PAYWALL_PHRASES = (
    "subscribe to continue reading",
    "subscribe to read",
    "subscribers only",
    "this article is for subscribers",
    "this content is for subscribers",
    "already a subscriber",
    "sign in to continue reading",
    "log in to continue reading",
    "create a free account to continue",
    "unlock this article",
    "you have reached your limit",
    "you've reached your limit",
    "free articles remaining",
)
# Only in the last 300 characters, where a teaser ends.
TAIL_PHRASES = ("continue reading", "read the full article", "read more at", "read the rest")
# A 200 page that is really a bot check.
CHALLENGE_PHRASES = (
    "verify you are human",
    "are you a robot",
    "checking your browser",
    "enable javascript and cookies",
    "access denied",
    "captcha",
)
JS_PHRASES = ("enable javascript", "javascript is disabled", "requires javascript")
ARTICLE_TYPES = {
    "Article",
    "NewsArticle",
    "ReportageNewsArticle",
    "AnalysisNewsArticle",
    "OpinionNewsArticle",
    "BackgroundNewsArticle",
    "BlogPosting",
    "LiveBlogPosting",
}

BodyStatus = Literal["full", "partial", "headline_only"]


@dataclass(frozen=True)
class JsonLd:
    accessible_for_free: bool | None = None
    word_count: int | None = None


@dataclass(frozen=True)
class Body:
    status: BodyStatus
    text: str | None
    words: int = 0
    fetch_error: str | None = None
    reasons: tuple[str, ...] = ()
    jsonld: JsonLd = field(default_factory=JsonLd)


def fetch_body(client: PoliteClient, url: str) -> tuple[Body, Page | None, str]:
    """Also returns the last URL reached: the final page, or the hop that failed."""
    try:
        page = client.get(url)
    except FetchError as e:
        return Body("headline_only", None, fetch_error=e.code), None, e.url
    if "html" not in page.content_type.lower():
        kind = page.content_type.split(";")[0].strip() or "unknown"
        return Body("headline_only", None, fetch_error=f"not_html:{kind}"), page, page.final_url
    return classify(page.body, page.final_url), page, page.final_url


def classify(html: bytes, url: str | None = None) -> Body:
    meta = read_jsonld(html)
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True)
    lowered_html = html.decode("utf-8", errors="replace").lower()
    if not text:
        error = "js_rendered" if _has(lowered_html, JS_PHRASES) else "empty_extraction"
        return Body("headline_only", None, fetch_error=error, jsonld=meta)

    words = len(text.split())
    lowered = text.lower()
    if words < MIN_WORDS:
        if _has(lowered, CHALLENGE_PHRASES):
            return Body("headline_only", None, fetch_error="bot_challenge", jsonld=meta)
        if _has(lowered, JS_PHRASES):
            return Body("headline_only", None, fetch_error="js_rendered", jsonld=meta)

    reasons = []
    if meta.accessible_for_free is False:
        reasons.append("jsonld_not_free")
    if words < MIN_WORDS:
        reasons.append("short")
    if _has(lowered, PAYWALL_PHRASES) or _has(lowered[-300:], TAIL_PHRASES):
        reasons.append("paywall_phrase")
    if meta.word_count and words < MIN_WORDCOUNT_RATIO * meta.word_count:
        reasons.append("below_wordcount")
    status: BodyStatus = "partial" if reasons else "full"
    return Body(status, text, words, reasons=tuple(reasons), jsonld=meta)


def read_jsonld(html: bytes) -> JsonLd:
    try:
        tree = lxml.html.fromstring(html)
    except (lxml.etree.LxmlError, ValueError):
        return JsonLd()
    found = JsonLd()
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        try:
            data = json.loads(script.text_content())
        except ValueError:
            continue
        for node in _nodes(data):
            if not _is_article(node) and "isAccessibleForFree" not in node:
                continue
            free = _bool(node.get("isAccessibleForFree"))
            for part in _nodes(node.get("hasPart")):
                if _bool(part.get("isAccessibleForFree")) is False:
                    free = False
            found = JsonLd(
                accessible_for_free=free if free is not None else found.accessible_for_free,
                word_count=_int(node.get("wordCount")) or found.word_count,
            )
            if _is_article(node):
                return found
    return found


def _nodes(data: object) -> Iterator[dict]:
    if isinstance(data, list):
        for item in data:
            yield from _nodes(item)
    elif isinstance(data, dict):
        yield data
        yield from _nodes(data.get("@graph"))


def _is_article(node: dict) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(k in ARTICLE_TYPES for k in kinds if isinstance(k, str))


def _bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return None


def _int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        return int(value)
    return None


def _has(text: str, phrases: tuple[str, ...]) -> bool:
    return any(p in text for p in phrases)
