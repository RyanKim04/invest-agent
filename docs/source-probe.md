# Source Probe (F1)

Oct 29, 2026 · News Sep 30–Oct 29 (30 days), SEC 90 days · Scripts: `scripts/probe_sources.py`, `scripts/probe_news_bodies.py`, `scripts/probe_content.py` · Raw output, `explorer.html` and the review sheets in `data/probe/` (gitignored)

## Decisions

| Source | Result | Decision |
| --- | --- | --- |
| SEC EDGAR | Every filing fetched, Items in the API | Keep |
| Company IR RSS | All 3 companies have a feed | Keep |
| Finnhub company news | Discovery and a ~22-word snippet. Cut off at ~250 items per call | Keep for discovery, one call per ticker per day |
| Alpaca News | Benzinga bodies, which block our fetches | Keep |
| yfinance | Prices and rating changes. News empty | Keep for prices and analyst data |
| Alpha Vantage | Earnings call transcripts on the free tier. News is premium | Keep for transcripts |
| FRED | All 8 series work | Keep, without VIXCLS |
| Google News RSS | robots.txt disallows `/rss/` | Drop |
| GDELT DOC API | 429 on 9 of 10 requests, with backoff | Drop |
| Tiingo | Prices only on the free plan | Drop |
| EODHD | Works, but one ticker per request at 10 calls against 20 a day | Drop |
| FMP | Free plan covers META only | Drop |
| TipRanks MCP | Terms forbid keeping data beyond a transient cache | Drop, not tested |

## Content over 30 days

What we can actually read for GOOG, META and RDDT, Sep 30–Oct 29.

**Finnhub links through our fetch path**

| Step | Count |
| --- | --- |
| Items in the 4 feeds (GOOG, GOOGL, META, RDDT) | 7,305 |
| Unique Finnhub ids | 4,330 |
| Unique headlines | 4,265 |
| Pages requested | 3,557 |
| Readable body (`full` or `partial`) | 2,733 |
| After removing copies (8-word shingle containment ≥ 0.8, 72 h) | 2,705 |

The 708 not requested are Benzinga links. After 20 straight 403s from benzinga.com, the run stopped asking it.

| Finnhub outlet | Items | full | partial | headline_only | Why not full |
| --- | --- | --- | --- | --- | --- |
| Yahoo | 3,030 | 2,312 (76%) | 139 | 579 | robots.txt `Disallow: /m/` ×356, 403 from linked sites (TheStreet, beincrypto) ×186, JSON-LD not free ×128, JavaScript-only ×27 |
| Benzinga | 751 | 0 | 0 | 751 | 403, then blocked |
| Seeking Alpha | 270 | 0 | 68 | 202 | 403 ×202, paywall ×68 |
| CNBC | 150 | 99 (66%) | 51 | 0 | short (video pages) ×46 |
| ChartMill | 64 | 64 | 0 | 0 | |
| **Total** | 4,265 | 2,475 (58%) | 258 (6%) | 1,532 (36%) | |

**With Alpaca added.** Alpaca returned 824 unique items over 30 days, 558 of them with content. Finnhub linked 751 Benzinga articles that we couldn't fetch. Alpaca has 685 of them, and 448 of those have content. Most of the rest are the bodiless rating posts.

Full texts about the company (named in the headline, or 3+ times in the body), after copies are removed across both sources:

| | GOOG | META | RDDT |
| --- | --- | --- | --- |
| Full texts in 30 days | 1,197 | 977 | 93 |

- Only Finnhub-fetched: 1,665. Only Alpaca: 380.
- Type, by the page's provider:

  | Reporting | Analysis or opinion | Press release | Auto-generated | Unclassified |
  | --- | --- | --- | --- | --- |
  | 808 (39%) | 998 (48%) | 126 (6%) | 71 (3%) | 59 (3%) |

  - Analysis includes Motley Fool, 24/7 Wall St, Zacks, Simply Wall St, TIKR, Stocktwits and Insider Monkey.
  - Press releases are mostly other companies' releases that name Google or Meta.
- **Reporting comes from few outlets.** 19 reporting outlets in all. Benzinga is half of it, then Yahoo Finance and its video desk, Investing.com, CNBC, Bloomberg, Quartz, TechCrunch, AFP and AP. For RDDT, 5 outlets reported in 30 days, mostly Benzinga (14 of 18). Reuters, WSJ and FT pages didn't come through Finnhub.

## Analyst reasons

yfinance listed 44 rating or target changes in the 30 days: GOOG 6, META 37, RDDT 1. For each one, articles from −1 to +2 days that name the firm were searched for a passage giving a reason. Every passage was read by hand; the verdicts are in `data/probe/analyst_review.csv`.

| Result | Changes |
| --- | --- |
| The article gives a reason for this change | 10 (23%) |
| The firm's view is quoted near the change, but not as the reason | 3 |
| Firm named, but the passage is about another company or another call | 7 |
| Firm named, no passage with a reason | 22 |
| No article names the firm | 2 |

Examples. All are journalists' paraphrases, often of TheFly, not the broker's report:
- META, Truist, $763 → $1,000, Oct 13: "citing early traction for its Muse AI agent" (Investing.com)
- META, Goldman Sachs, Oct 14: "lowering its target due to concerns about higher 2027 operating expenses"
- GOOG, UBS, $379 → $380, Oct 14: "concerns about potential disruption from ChatGPT advertising and greater AI integration into Google Search had eased"
- RDDT, Needham, $300 kept, Oct 9: "citing traffic tracker findings that pointed to bullish third-quarter 2026 user and revenue growth"

Notes:
- **Benzinga rating posts have no body.** Benzinga posts a short item for every rating change, but all 52 of them in Alpaca have no text.
- **yfinance has errors.** It records Goldman's Oct 14 META target as $860 → $860, while the articles say it was cut to $762. The report should show yfinance's value and the article's, and flag when they disagree.
- **Too early for GOOG.** The three GOOG target cuts after earnings (JPMorgan, TD Cowen, Scotiabank) are from Oct 29, so the +2-day window hasn't passed yet.

## SEC EDGAR

- CIKs in `universe.yaml` match `company_tickers.json`. GOOGL has the same CIK as GOOG.
- In 90 days: 6 8-Ks and 3 10-Qs. All were fetched (21 requests, 0 errors, 0.15 s apart).
- Item numbers come from the submissions API (`items: "2.02,9.01"`), and the filing index lists the same ones. No need to parse the 8-K for them.
- companyfacts (GOOG, 564 concepts) has no segment facts. Entry keys are `start, end, val, accn, fy, fp, form, filed, frame`, with no dimension keys and no repeated value for one period in one filing. Segments come from filing tables.
- Earnings 8-Ks are accepted at 16:01–16:08 ET. The 16:10 close report can miss them, but the 21:40 report won't.

| Ticker | Filed | Items | Documents (words) |
| --- | --- | --- | --- |
| GOOG | Oct 28 | 2.02, 9.01 | 8-K 712, EX-99.1 3,144 |
| GOOG | Aug 10 | 8.01, 9.01 | 8-K 1,274 |
| META | Oct 29 | 1.01, 3.03, 5.03, 9.01 | 8-K 3,654 |
| META | Oct 21 | 1.01, 2.02, 8.01, 9.01 | 8-K 3,177, EX-99.1 3,239 |
| RDDT | Oct 29 | 2.02, 9.01 | 8-K 630, EX-99.1 2,758, EX-99.2 6,573 |
| RDDT | Aug 12 | 5.02, 9.01 | 8-K 420 |

## Company IR RSS

- All three run the same IR platform with a feed at `/rss/pressrelease.aspx` (10 newest items). The listed paths are in `config/sources.yaml`.
- Release pages through the fetch path:
  - Meta: 2,048 and 1,921 words, `full`
  - Reddit: 5,508 words, `full`
  - Alphabet's Q3 release page: 83 words, `partial`. The 8-K's EX-99.1 has the full text.

## Finnhub company news

- **Fields:** `category, datetime (Unix s), headline, id, image, related, source, summary, url`.
- **Snippet:** `summary` is a median of 22 words, max 174, so it's a `feed_snippet`, not a body.
- **Cut-off:** one 7-day call for GOOG returned 248 items covering only the last 29 hours. One-day calls returned everything:

  | Ticker | Items, 8 days | Busiest day |
  | --- | --- | --- |
  | GOOG | 779 | 183 (Oct 28) |
  | GOOGL | 817 | 188 (Oct 28) |
  | META | 762 | 167 (Oct 22) |
  | RDDT | 39 | 11 (Oct 26) |

- **GOOG and GOOGL:** they share 754 ids, GOOGL has 63 more and GOOG 25 more. Query both and dedupe on `id`.
- **Links:** `url` is always a `finnhub.io/api/news?id=` redirect to the publisher. Finnhub's robots.txt allows it.
- **Outlets:**

  | Yahoo | Benzinga | Seeking Alpha | CNBC | ChartMill |
  | --- | --- | --- | --- | --- |
  | 73% | 15% | 8% | 3% | 1% |

- **`related` is just the queried symbol.** Only about 50% of headlines plus snippets name the company. In the fetched bodies, 61 of 66 do. Tag tickers from the body using `universe.yaml` aliases.
- **Rate limit:** the `x-ratelimit-limit` header says 60 per minute.
- **Delay.** RDDT reported at 16:08 ET on Oct 29.
  - At 18:10 ET, Finnhub had none of the 16 articles timestamped 16:07–19:33 ET.
  - By 21:12 ET it had all of them, and 13 fetched as `full`.
  - So articles appear 2 to 5 hours after their timestamp, and a run has to ask for yesterday and today again.
  - The first 30-day pull reused the 18:10 file from cache and missed them. It was then pulled again.
- **Other endpoints on the free key:** earnings calendar, recommendation trends, basic financials and SEC filing list work. Press releases, transcripts, price target, upgrade/downgrade and news sentiment return 403.

## News bodies, 7-day sample

The 100 articles all came from Finnhub, because Google News is blocked by robots.txt and GDELT returned 429. The sample picks round-robin across outlets for GOOG 40, META 40 and RDDT 20, and runs the F2 path: robots.txt, 1 request per second per host, the project User-Agent, trafilatura 2.3.1, then the completeness check.

| Site | n | full | partial | headline_only | Why not full |
| --- | --- | --- | --- | --- | --- |
| benzinga.com | 24 | 2 | 2 | 20 | 403 ×20, short ×2 |
| seekingalpha.com | 17 | 0 | 7 | 10 | 403 ×10, JSON-LD not free ×7 |
| cnbc.com | 17 | 13 | 4 | 0 | short ×3, JSON-LD not free ×1 |
| finance.yahoo.com | 19 | 16 | 1 | 2 | robots.txt `Disallow: /m/` ×2, JSON-LD not free ×1 |
| chartmill.com | 13 | 13 | 0 | 0 | |
| Yahoo links to other publishers (fool.com, 247wallst, telegraph, trefis, stocktwits, barchart, axios) | 10 | 7 | 1 | 2 | barchart JavaScript-only, axios 403 |
| **Total** | 100 | 51 | 15 | 34 | |

- **Weighted by real outlet share:** about 63% `full`, 10% `partial`, 27% `headline_only`.
- **Who blocks us:** Benzinga (403 on 20 of 24) and Seeking Alpha (10 of 17) return 403 to our User-Agent, even at 1 request per second. We don't work around it.
- **Completeness signals:** JSON-LD `isAccessibleForFree` appeared on 14 pages and `wordCount` on 15. The word-count and phrase checks do most of the work, so the hand check decides whether they're right.
- **Length:** `full` articles have a median of 569 words, min 225.
- **Time:** fetch p50 1.4 s, p95 5.4 s. Extraction p50 28 ms, p95 70 ms. One request per second per host, with Yahoo at 73% of links, keeps a sequential run to about 25 articles a minute. The 30-day run used 6 threads.
- **Hand check:** 30 rows, checked by a separate agent against the saved HTML (`data/probe/bodies/review.csv`, then `review_v2.csv` after the fix).
  - First pass: of 20 `full` rows, 16 were complete and 8 were main text only. All 10 `partial` rows really were cut off, but 3 for the wrong reason.
  - No miss came from the 150-word or 70% thresholds, so they stay. The misses came from page structure:

| Problem | Where | Fix (`rules-2`) |
| --- | --- | --- |
| The end of the article sits in a hidden "Story continues" section, which trafilatura skips | Yahoo | Unhide it before extraction |
| The body is split around a chart or video, and trafilatura keeps only the larger block | Telegraph, Yahoo video, CNBC live blog | Add back dropped paragraphs (15+ words) from the same `<article>`, skipping cards, lists, author boxes and teasers |
| The page is a menu or a promo stub with no article | CNBC `/video/`, Benzinga "headline only article" | `headline_only`, `fetch_error = no_body` |
| Promo, sponsor and footer lines | 12 of 20 `full` rows | Line rules taken from lines repeated across the 30-day set. A sponsor block is cut from its heading to the end, so rotating sponsors don't need new rules |
| Seeking Alpha teaser behind a bot check | Seeking Alpha | "enable javascript and cookies" counts as a paywall phrase |

  - After the fix, the agent re-checked the 17 changed rows:
    - All 4 new `headline_only` rows were right.
    - Of the 18 `full` rows that still have text, 17 are complete and 14 are main text only.
    - No article text was removed.
  - The agent's 4 remaining finds (rows 4, 9, 11 and 15) were fixed after that and checked only by grep, not by a third pass.
  - Still open:
    - The CNBC live blog misses 3 short post headlines.
    - Telegraph serves the full text but stays `partial`, because its JSON-LD says it isn't free. We keep that conservative.
    - Summary boxes ("Key Points") are dropped, but they repeat the body.
- **30-day set with `rules-2`:**
  - 52 CNBC video pages became `headline_only` and 3 short Yahoo pages became `partial`.
  - Net change is +26k words (2.05M → 2.07M), most of it Yahoo endings restored after promos were taken out.
  - Long lines that went missing were titles (stored separately), captions, legal disclaimers and promos.

## Major events, Oct 22–29

| Event | Full text from filing or IR | Full-text articles |
| --- | --- | --- |
| GOOG Q3 results (Oct 28) | 8-K EX-99.1 | 2 of 6 fetched (Yahoo, CNBC) |
| GOOG 10-Q (Oct 29) | 10-Q | — |
| META $95B stock and preferred offering (Oct 26–27) | IR releases, 8-K Oct 29 | 2 of 6 |
| META Muse AI agent | — | 2 of 6 |
| Anthropic IPO filing (Google is an investor) | — | 3 of 6 |
| Google pays SpaceX for compute | — | 2 of 2 |
| RDDT Q3 results (Oct 29) | 8-K EX-99.1, EX-99.2, IR release | 3 of 6 |
| RDDT US user growth slows | — | 3 of 3 |

Every event has a full-text source. META's Q3 results came Oct 21, the day before the window. That 8-K has a 3,239-word EX-99.1.

## Google News RSS

`news.google.com/robots.txt` has `Disallow: /` for all user agents. Its Allow lines cover only `/`, `/home`, `/topics/`, `/publications/`, `/stories/` and `/about`. `/rss/` isn't allowed, so no feed request was made. The file also names ClaudeBot and anthropic-ai explicitly.

## GDELT

- 10 requests over about 2 hours, at least 6 s apart (GDELT asks for 5 s). The last 4 came after waits of 0, 60, 180 and 600 s.
- 9 came back 429 with "Please limit requests to one every 5 seconds", including the very first. One came back 200 with 0 articles.
- Dropped. It isn't needed for discovery now that Finnhub and Alpaca cover it.

## yfinance

yfinance 1.7.0:
- `history`: daily OHLCV, indexed in America/New_York.
- `upgrades_downgrades`: 708 / 526 / 209 rows, with firm, grades, action and price targets.
- `analyst_price_targets`: current, high, low, mean, median.
- Earnings: `earnings_estimate`, `calendar` (next earnings date), `earnings_dates` (EPS estimate, reported, surprise).
- `news` returned an empty list for all three.

It's unofficial. Yahoo's terms are personal use, according to the yfinance README; Yahoo's own terms weren't read.

## Tiingo

- EOD prices work: adjusted OHLCV, `divCash`, `splitFactor`, with the date at 00:00 UTC.
- The News API returns 403 on the free plan ("You do not have permission to access the News API").
- Fundamentals are limited to the DOW 30 on free and power plans.
- There are no rate-limit headers.
- Its prices duplicate yfinance.

## Alpha Vantage

- `EARNINGS_CALL_TRANSCRIPT`, verbatim with `speaker, title, content, sentiment` per part. Analysts are titled with their firm, e.g. "Analyst (Goldman Sachs)":

  | Quarter | GOOG | META | RDDT |
  | --- | --- | --- | --- |
  | 2025Q4 | 8,416 words | | |
  | 2026Q1 | 8,180 | | |
  | 2026Q2 | 8,295 | 8,370 | 7,240 |
  | 2026Q3 | empty (call Oct 28) | 9,649 (call Oct 21) | empty (call Oct 29) |

  The delay after the call is between 1 and 8 days. F2 measures it.
- `NEWS_SENTIMENT` is premium. `TIME_SERIES_DAILY` works (100 days).
- The free tier allows 25 calls a day. 3 tickers × 1 transcript a quarter, plus retries, fits easily.

## FRED

- Series: DGS10, DGS2, T10Y2Y, DFF, CPIAUCSL, UNRATE, VIXCLS, DTWEXBGS.
- All return 200. Daily rates are updated the same afternoon. Missing values come back as `"."`.
- VIXCLS's notes carry a third-party copyright, so it's personal use only.

## Alpaca News

- `GET data.alpaca.markets/v1beta1/news` with `include_content=true`, 50 per page. Free paper-trading keys work. The `x-ratelimit-limit` header says 200 per minute.
- 30 days: GOOG 446, META 468, RDDT 22 items (824 unique). Sources are `benzinga` and `fiscal.ai`.
- 558 have content, a median of about 486 words. Content is HTML.
- Benzinga's rating posts (52) never have content. Benzinga's earnings flashes (EPS, guidance, CEO remarks on the call) are headlines only too, but they arrive within minutes.
- Earnings call transcripts arrive the same day. The RDDT Q3 transcript (author "Earnings Transcripts", 10,238 words) was there at 17:44 ET, after the 16:08 ET release. Alpha Vantage didn't have it 5 hours later. Whether it's complete gets checked against Alpha Vantage once that arrives.

## EODHD

- Free key works for GOOG.US, GOOGL.US, META.US and RDDT.US. `content` is the article body: for the same URL it ran 1.12× the length of our extraction (median, 76 pairs).
- Links are 93% Yahoo Finance and 6% Seeking Alpha news. That's the same text our fetch already gets, except the Seeking Alpha news items.
- One ticker per request ("Only one ticker is allowed in parameter s"). At 10 calls per request against 20 a day, 3 tickers don't fit. A request returns at most 1,000 items, which covered only about 3 weeks for GOOG and META.

## FMP

`/stable/grades`, `grades-historical`, `grades-consensus` and `price-target-consensus` return 402 for GOOG and RDDT ("not available under your current subscription"). They work for META. `price-target-news` is paid for all three.

## TipRanks MCP

Not tested. From the docs and terms (mcp.tipranks.com/docs, /terms):
- Free API key: 50 requests a month.
- `get_recent_analyst_ratings` returns firm, rating, target and analyst rank, but no tool returns the reason for a change.
- §3 forbids storing the data "beyond transient operational caching" and using it to "train, fine-tune, or evaluate" AI models.

## Terms of use

| Source | Terms | Read |
| --- | --- | --- |
| SEC | At most 10 requests/s total. User-Agent with a contact email | sec.gov developer pages |
| Finnhub | Free key: 60 calls a minute. Terms of service not found | **Not read.** Read before M1 |
| Alpha Vantage | Personal, non-commercial use unless agreed in writing | Search snippet of alphavantage.co/terms_of_service |
| Tiingo | Basic and Power: internal, personal use. No redistribution | api.tiingo.com/documentation |
| FRED | Show "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis." Third-party series: personal use only. No storage ban | fred.stlouisfed.org/docs/api/terms_of_use.html |
| yfinance | Yahoo data is for personal use | yfinance README. Yahoo's terms not read |
| Alpaca | Personal, non-commercial use. No redistribution. News is supplied by Benzinga, whose own terms weren't read | Alpaca terms and conditions, support page |
| Publishers | robots.txt obeyed. Text is stored for search; reports quote briefly and link | — |

Personal-use terms fit while reports go only to you. A website or MCP that other people can see would need these checked again.

## Changes this brings to F2

- Finnhub: one call per ticker per day, asking for yesterday and today on every run because items arrive 2–5 hours late. GOOG and GOOGL are deduped on `id`.
- Transcripts: Alpaca first (same day), Alpha Vantage as a backup and a check.
- Tag tickers from the article body, not from Finnhub's `related`.
- News: Finnhub finds articles and we fetch them. Alpaca supplies Benzinga bodies. IR RSS supplies company releases.
- Fetching runs hosts in parallel (PoliteClient is thread-safe, one request at a time per host). Yahoo's 1 request per second is the limit.
- Rating changes come from yfinance, and a reason is linked only from an article passage. Keep yfinance's target and the article's, and flag when they disagree.
- Extraction uses `rules-2` (see the hand check above): unhide Yahoo's read-more, add back split blocks, strip promo lines, mark menu pages and stubs `no_body`.
- robots.txt is parsed with Protego (`*`, `$`, longest match wins, per RFC 9309). The standard library's parser doesn't follow the RFC.
