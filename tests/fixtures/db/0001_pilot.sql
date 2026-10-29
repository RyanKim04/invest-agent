-- Minimal data for CI's dbt build: one run, the pilot universe, a filing and a news story.
INSERT INTO ops.runs (run_id, trigger, status)
VALUES ('00000000-0000-0000-0000-000000000001', 'test', 'succeeded');

INSERT INTO raw.universe (ticker, cik, name, aliases, lists) VALUES
    ('GOOG', '0001652044', 'Alphabet Inc.', '{Alphabet,Google,GOOGL}', '{pilot}'),
    ('META', '0001326801', 'Meta Platforms, Inc.', '{Meta Platforms,Facebook}', '{pilot}'),
    ('RDDT', '0001713445', 'Reddit, Inc.', '{Reddit}', '{pilot}');

CREATE TEMP TABLE fixture_docs AS
SELECT *, doc_id || ':' || left(encode(sha256(convert_to(body, 'UTF8')), 'hex'), 12) AS version_id
FROM (VALUES
    ('sec:0001713445-26-000001:ex99-1', 'sec', '0001713445-26-000001:ex99-1', NULL,
     'Reddit Q3 2026 results', 'filing', '8-K', '{2.02,9.01}'::text[], 1,
     'Reddit reports third quarter 2026 results.', 'full'),
    ('fixture-news-1', 'finnhub', 'fixture-1', 'https://example.com/reddit-earnings',
     'Reddit shares rise after earnings', 'news', NULL, '{}'::text[], 4,
     'Reddit shares rise after earnings', 'headline_only')
) AS d (doc_id, source, source_id, url, title, doc_type, form_type, items, source_rank,
        body, body_status);

-- Document first: its current_version_id FK is deferred until commit.
INSERT INTO raw.documents
    (doc_id, canonical_doc_id, source, source_id, canonical_url, title, doc_type, form_type,
     items, tickers, source_rank, published_at, current_version_id, run_id)
SELECT doc_id, doc_id, source, source_id, url, title, doc_type, form_type,
       items, '{RDDT}', source_rank, '2026-10-28 20:05:00+00', version_id,
       '00000000-0000-0000-0000-000000000001'
FROM fixture_docs;

INSERT INTO raw.document_versions
    (version_id, doc_id, content_hash, text, body_status, fetched_at, run_id)
SELECT version_id, doc_id, encode(sha256(convert_to(body, 'UTF8')), 'hex'), body, body_status,
       now(), '00000000-0000-0000-0000-000000000001'
FROM fixture_docs;
