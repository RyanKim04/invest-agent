import json

import lxml.html
import pytest

from invest_agent.ingest.news_body import (
    _add_split_blocks,
    _strip_boilerplate,
    classify,
    read_jsonld,
)

WORDS = (
    "revenue guidance margin capital spending advertising cloud segment quarter growth "
    "analysts shares investors operating income outlook data centers demand pricing"
).split()


def article(words: int, jsonld: dict | None = None, tail: str = "") -> bytes:
    sentences, n = [], 0
    while n < words:
        sentence = " ".join(WORDS[(n + i) % len(WORDS)] for i in range(12)) + f" item{n}."
        sentences.append(sentence)
        n += 13
    paragraphs = "".join(
        f"<p>{' '.join(sentences[i : i + 4])}</p>" for i in range(0, len(sentences), 4)
    )
    script = f'<script type="application/ld+json">{json.dumps(jsonld)}</script>' if jsonld else ""
    return (
        f"<html><head><title>Test</title>{script}</head><body>"
        f"<nav><a href='/'>Home</a></nav><article><h1>Headline</h1>{paragraphs}"
        f"<p>{tail}</p></article><footer>Copyright</footer></body></html>"
    ).encode()


def test_long_article_is_full():
    body = classify(article(400))
    assert body.status == "full"
    assert body.words >= 350
    assert body.reasons == ()


def test_short_article_is_partial():
    body = classify(article(80))
    assert body.status == "partial"
    assert "short" in body.reasons


def test_jsonld_not_free_is_partial():
    body = classify(article(400, {"@type": "NewsArticle", "isAccessibleForFree": "False"}))
    assert body.status == "partial"
    assert body.reasons == ("jsonld_not_free",)


def test_paywalled_part_counts_as_not_free():
    ld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebPage"},
            {
                "@type": ["NewsArticle"],
                "hasPart": {"@type": "WebPageElement", "isAccessibleForFree": False},
            },
        ],
    }
    assert read_jsonld(article(10, ld)).accessible_for_free is False


def test_text_below_jsonld_word_count_is_partial():
    body = classify(article(400, {"@type": "NewsArticle", "wordCount": "1200"}))
    assert body.status == "partial"
    assert body.reasons == ("below_wordcount",)


@pytest.mark.parametrize("tail", ["Subscribe to continue reading.", "Continue reading"])
def test_paywall_phrase_is_partial(tail: str):
    body = classify(article(400, tail=tail))
    assert body.status == "partial"
    assert "paywall_phrase" in body.reasons


def test_empty_page_is_headline_only():
    body = classify(
        b"<html><body><div id='root'></div>"
        b"<noscript>Please enable JavaScript</noscript></body></html>"
    )
    assert body.status == "headline_only"
    assert body.fetch_error == "js_rendered"
    assert body.text is None


def test_bot_check_page_is_headline_only():
    html = (
        b"<html><body><article><h1>Hold on</h1>"
        b"<p>Please verify you are human before continuing.</p></article></body></html>"
    )
    body = classify(html)
    assert body.status == "headline_only"
    assert body.fetch_error in {"bot_challenge", "empty_extraction"}


def test_hidden_read_more_is_kept():
    hidden = article(300).replace(
        b"</article>",
        b"<div class='read-more-wrapper' data-testid='read-more' style='display: none'>"
        b"<p>The closing paragraph explains the valuation case and the risks investors weigh "
        b"before the next quarterly report arrives.</p></div></article>",
    )
    body = classify(hidden)
    assert "closing paragraph explains the valuation" in body.text


def test_dropped_block_before_embed_is_added_back():
    first = (
        "Shares fell sharply on Thursday after the company reported weaker search revenue "
        "and higher spending than analysts expected."
    )
    later = [
        f"Paragraph {i} discusses capital spending, cloud demand, the data center buildout "
        "and the outlook for operating margins."
        for i in range(3)
    ]
    html = (
        "<html><body><article><div class='c'><p>" + first + "</p></div><figure>chart</figure>"
        "<div class='c'>" + "".join(f"<p>{p}</p>" for p in later) + "</div>"
        "<a href='/other'><p class='read-next-card-teaser'>Another story about bond yields "
        "and the Federal Reserve that a reader might click on next.</p></a>"
        "</article></body></html>"
    )
    text = _add_split_blocks(lxml.html.fromstring(html), "\n".join(later))
    assert text.split("\n") == [first, *later]


def test_boilerplate_lines_are_removed():
    text = "\n".join(
        [
            "Reddit grew revenue 48% in the quarter, ahead of guidance.",
            "Advertisement|Remove ads.",
            "RDDT Reddit",
            "NYSE · As of 4:00 PM ET 10/29/26",
            "At $29.96 per share, Cadre trades at 21.5x forward P/E. Is now a good time to buy? "
            "See for yourself in our in-depth research report, it's free.",
            "Don't Miss:",
            "- A link to another story",
            "Margins widened as advertising prices rose.",
            "Building a resilient portfolio means thinking beyond a single asset.",
            "Sponsor copy that rotates every week.",
        ]
    )
    assert _strip_boilerplate(text).split("\n") == [
        "Reddit grew revenue 48% in the quarter, ahead of guidance.",
        "At $29.96 per share, Cadre trades at 21.5x forward P/E.",
        "Margins widened as advertising prices rose.",
    ]


def test_headline_only_stub_has_no_body():
    stub = article(300, tail="This headline only article is a sample of real-time intelligence.")
    body = classify(stub)
    assert body.status == "headline_only"
    assert body.fetch_error == "no_body"


def test_menu_page_has_no_body():
    menu = "".join(f"<li><a href='/{i}'>Section {i}</a></li>" for i in range(80))
    html = (
        f"<html><body><div><ul>{menu}</ul></div>"
        "<p>Watch the interview about advertising demand.</p></body></html>"
    ).encode()
    body = classify(html)
    assert body.status == "headline_only"
    assert body.fetch_error in {"no_body", "empty_extraction"}


def test_bot_check_text_in_a_long_page_is_partial():
    body = classify(article(400, tail="Please enable Javascript and cookies to continue."))
    assert body.status == "partial"
    assert "paywall_phrase" in body.reasons
