import json

import pytest

from invest_agent.ingest.news_body import classify, read_jsonld

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
