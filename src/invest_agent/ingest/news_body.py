"""Publisher page → article text and `body_status` (rules: docs/01-system-overview.md#news-bodies).

The thresholds and line rules come from F1's 30-day set and hand check (docs/source-probe.md)."""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

import lxml.html
import trafilatura
from trafilatura.utils import load_html  # decodes by the page's charset, unlike lxml

from invest_agent.ingest.http import FetchError, Page, PoliteClient

EXTRACTOR = f"trafilatura-{trafilatura.__version__}+rules-2"
MIN_WORDS = 150
MIN_WORDCOUNT_RATIO = 0.7
# A page whose text is mostly 1-3 word lines is a menu, not an article (CNBC video pages).
MAX_SHORT_LINE_SHARE = 0.3
# A paragraph trafilatura dropped is added back from this length up (bodies split around embeds).
MIN_SPLIT_WORDS = 15

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
    "enable javascript and cookies",  # Seeking Alpha teasers sit behind a bot check
)
# The page says it has no article body.
NO_BODY_PHRASES = ("this headline only article",)  # Benzinga
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
# Publisher template lines from the F1 30-day set, matched at the start of a line.
BOILERPLATE = re.compile(
    "|".join(
        (
            # Stocktwits
            r"advertisement\b",
            r"see also:",
            r"comments posted here will also appear",
            r"for updates and corrections, email",
            # 24/7 Wall St
            r"contact .{0,60} for any questions or corrections",
            # Motley Fool quote card
            r"(nasdaq|nyse|nyse american|amex|otc\w*) · as of ",
            # CNBC video embeds
            r"watch now$",
            r"video ?(\d{1,2}:\d{2}){1,2}$",
            r"\d{1,2}:\d{2}$",
            r"load more$",
            # Benzinga
            r"market news and data brought to you by benzinga",
            r"never miss a trade again",
            r"want the fastest, most accurate stock market intelligence",
            # Trefis
            r"there is a smarter (approach|choice)\. since its inception, the trefis",
            r"spot an error or want more like this",
            r"for questions about this article or underlying research",
            r"our purpose is to inform you with unique data",
            # TIKR
            r"tikr lets you",
            r"now live: discover how much upside",
            r"estimate a company.s fair value instantly",
            # Zacks, StockStory, Simply Wall St, Insider Monkey on Yahoo
            r"want the latest recommendations from zacks",
            r"click here for in-depth analysis of the latest stock market news",
            r"since \d{4}, our top stock-picking strategies",
            r"stocks that made our list in \d{4} include",
            r".*: free stock analysis report$",
            r"not finding the right stocks\?",
            r".*\bfree report\b.*\b(grab|get|download) it here",
            r"trending:",
            r"this article by simply wall st is general in nature",
            r"there is a second opinion on .* worth weighing",
            r".*with other companies using the \d+ high quality",
            r"(discover|explore) why this narrative",
            r".*→ uncover",
            r"those are three of them\.",
            r"disclosure: none\. follow insider monkey",
            r"if you.re looking for an? .* stock .*check out our",
            r"click to continue reading",
            r"unlocked: \d+ new trades every week",
            r"don.t just follow the ticker",
            # Yahoo and wire footers
            r"read the latest financial and business news from yahoo finance",
            r"have feedback on this article\?",
            r"this (article|story) .*originally (published|featured|appeared) (on|at|by|in)",
            r"read also:",
            r"photo (courtesy|credit)",
            r"read next:",
            r"©\s?\d{4}",
            r"for immediate release$",
            r"a photo accompanying this announcement is available at",
            r"original article$",
            r"view (original content|source version)",
            r"this is a paid press release",
            r"get the latest stock analysis from benzinga",
            r"companies discussed in this article include",
            r"this article provides information only and should not be construed as advice",
            r"image (source|via|credit)s?\b",
            # Proactive, ResearchAndMarkets
            r"proactive (financial news|news team|will on occasion)",
            r"we are experts in medium and small-cap markets",
            r"our human content creators are equipped",
            r"the team delivers news and unique insights",
            r"(about )?researchandmarkets\.com",
            r"for more information about this report visit",
            # Newsletter sign-ups
            r"join [\d,]+\+ readers",
            r"by subscribing, you agree to our terms",
        )
    ),
    re.IGNORECASE,
)
# A promo sentence inside a real paragraph (StockStory on Yahoo); only the sentence goes.
INLINE_PROMO = re.compile(
    r"(is now [^?]{0,60}\? )?(see for yourself in our|read our free) "
    r"[^.]{0,80}research report[^.]*\.?",
    re.IGNORECASE,
)
# A heading followed by "- " lines that link to other articles.
LINK_LIST = re.compile(
    r"(don.t miss|read next|what to read next|read more|also read|must read|most read from .*):?$",
    re.IGNORECASE,
)
# Everything from this line on is promotion or contacts (Benzinga sponsors on Yahoo, StockStory,
# press release footers). Only cut after the first third of the text.
TAIL = re.compile(
    r"building wealth across more than just the market$|"
    r"building a resilient portfolio means thinking beyond|"
    r"stocks we like even more|while you.re here|high-quality stocks for all market conditions|"
    r"hunt for the next|does this mean you should act on|"
    r"(media )?contacts?:?$",
    re.IGNORECASE,
)
QUOTE_CARD = re.compile(r"(nasdaq|nyse|nyse american|amex|otc\w*) · as of ", re.IGNORECASE)
TICKER_LINE = re.compile(r"[A-Z][A-Z.]{0,5} \S")
# Paragraphs in these containers are never added back.
NOT_BODY_TAGS = {"aside", "nav", "footer", "figure", "figcaption", "form", "li"}
NOT_BODY_CLASS = re.compile(
    r"related|recirc|promo|newsletter|subscribe|advert|sponsor|share|social|comment|taboola|outbrain|"
    r"author|bio|modal|continue-reading|read-next|teaser|card",
    re.IGNORECASE,
)

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
    tree = load_html(html)
    source: bytes | str = html
    if tree is not None:
        _unhide(tree)
        source = lxml.html.tostring(tree, encoding="unicode")
    text = trafilatura.extract(source, url=url, include_comments=False, include_tables=True)
    lowered_html = html.decode("utf-8", errors="replace").lower()
    if not text:
        error = "js_rendered" if _has(lowered_html, JS_PHRASES) else "empty_extraction"
        return Body("headline_only", None, fetch_error=error, jsonld=meta)

    # Strip before adding blocks too, so a footer line isn't taken for the end of the body.
    text = _strip_boilerplate(text)
    if tree is not None:
        text = _strip_boilerplate(_add_split_blocks(tree, text))
    words = len(text.split())
    lowered = text.lower()
    if words < MIN_WORDS:
        if _has(lowered, CHALLENGE_PHRASES):
            return Body("headline_only", None, fetch_error="bot_challenge", jsonld=meta)
        if _has(lowered, JS_PHRASES):
            return Body("headline_only", None, fetch_error="js_rendered", jsonld=meta)
    if (
        not text
        or _has(lowered, NO_BODY_PHRASES)
        or _short_line_share(text) >= MAX_SHORT_LINE_SHARE
    ):
        return Body("headline_only", None, fetch_error="no_body", jsonld=meta)

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


def _unhide(tree: lxml.html.HtmlElement) -> None:
    """Yahoo hides the end of an article behind "Story continues", and trafilatura skips it."""
    for el in tree.xpath('//*[@data-testid="read-more" or contains(@class, "read-more-wrapper")]'):
        el.attrib.pop("style", None)


def _add_split_blocks(tree: lxml.html.HtmlElement, text: str) -> str:
    """Add back paragraphs trafilatura dropped when the body is split around an embed.

    trafilatura keeps the largest block, so the paragraphs before a chart (Telegraph) or a
    video intro above its transcript (Yahoo) go missing. Only paragraphs before the last kept
    one are added, from the same <article> (or the kept paragraphs' common container)."""
    # Compared without spaces: trafilatura sometimes drops the space at an inline tag.
    flat = _squash(text)
    long = {p for p in tree.iter("p") if len(_norm(p.text_content()).split()) >= MIN_SPLIT_WORDS}
    kept = [p for p in tree.iter("p") if p in long and _squash(p.text_content())[:70] in flat]
    if not kept:
        return text
    scope = _scope(kept)
    if scope.tag in ("body", "html"):
        return text
    kept_set = set(kept)
    lines = text.split("\n")
    before: dict[int, list[str]] = {}
    pending: list[str] = []
    for p in scope.iter("p"):
        if p in kept_set:
            if pending:
                key = _squash(p.text_content())[:35]
                at = next((i for i, line in enumerate(lines) if key in _squash(line)), None)
                if at is not None:
                    before.setdefault(at, []).extend(pending)
                pending = []
        elif p in long and _is_body_paragraph(p, scope):
            pending.append(_norm(p.text_content()))
    if not before:
        return text
    out = []
    for i, line in enumerate(lines):
        out += before.get(i, [])
        out.append(line)
    return "\n".join(out)


def _scope(kept: list[lxml.html.HtmlElement]) -> lxml.html.HtmlElement:
    common = set(kept[0].iterancestors())
    for p in kept[1:]:
        common &= set(p.iterancestors())
    container = next(a for a in kept[0].iterancestors() if a in common)
    articles = (a for a in (container, *container.iterancestors()) if a.tag == "article")
    return next(articles, container)


def _is_body_paragraph(p: lxml.html.HtmlElement, scope: lxml.html.HtmlElement) -> bool:
    text = _norm(p.text_content())
    if sum(len(_norm(a.text_content())) for a in p.iter("a")) > len(text) / 2:
        return False
    for el in (p, *p.iterancestors()):
        if el is scope:
            return True
        names = f"{el.get('class', '')} {el.get('id', '')}"
        hidden = "display:none" in el.get("style", "").replace(" ", "")
        if el.tag in NOT_BODY_TAGS or NOT_BODY_CLASS.search(names) or hidden:
            return False
        if el.tag in ("article", "a"):  # a card for another article
            return False
    return True


def _strip_boilerplate(text: str) -> str:
    out: list[str] = []
    total, seen = len(text.split()), 0
    in_list = False
    for line in text.split("\n"):
        line = INLINE_PROMO.sub("", line).rstrip()
        flat = _norm(line)
        if not flat:
            continue
        if TAIL.match(flat) and seen >= total / 3:
            break
        seen += len(flat.split())
        if in_list and flat.startswith("- "):
            continue
        in_list = bool(LINK_LIST.match(flat))
        if QUOTE_CARD.match(flat) and out and TICKER_LINE.match(out[-1].strip()):
            if len(out[-1].split()) <= 5:
                out.pop()  # the "RDDT Reddit" line above the quote card
        if not (in_list or BOILERPLATE.match(flat.removeprefix("- "))):
            out.append(line)
    return "\n".join(out).strip()


def _short_line_share(text: str) -> float:
    lines = [line.split() for line in text.split("\n")]
    words = sum(map(len, lines))
    return sum(len(line) for line in lines if len(line) <= 3) / words if words else 0.0


def _norm(text: str) -> str:
    return " ".join(text.split())


def _squash(text: str) -> str:
    return "".join(text.split())


def read_jsonld(html: bytes) -> JsonLd:
    tree = load_html(html)
    if tree is None:
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
