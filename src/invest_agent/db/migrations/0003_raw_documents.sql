-- Synced from config/universe.yaml at the start of each run.
CREATE TABLE raw.universe (
    ticker    text PRIMARY KEY,
    cik       text NOT NULL CHECK (cik ~ '^[0-9]{10}$'),
    name      text NOT NULL,
    aliases   text[] NOT NULL DEFAULT '{}',
    sectors   text[] NOT NULL DEFAULT '{}',
    lists     text[] NOT NULL DEFAULT '{}',
    synced_at timestamptz NOT NULL DEFAULT now()
);

-- One row per document: `sec:<accession>:<file>` or sha1 of the canonical URL.
CREATE TABLE raw.documents (
    doc_id             text PRIMARY KEY,
    canonical_doc_id   text NOT NULL REFERENCES raw.documents DEFERRABLE INITIALLY DEFERRED,
    source             text NOT NULL CHECK (source IN ('sec', 'ir_rss', 'finnhub', 'gnews', 'gdelt')),
    source_id          text NOT NULL,
    canonical_url      text UNIQUE,
    title              text NOT NULL,
    doc_type           text NOT NULL
                       CHECK (doc_type IN ('filing', 'press_release', 'news', 'transcript')),
    form_type          text,
    items              text[] NOT NULL DEFAULT '{}',
    fiscal_period      text,
    tickers            text[] NOT NULL DEFAULT '{}',
    sectors            text[] NOT NULL DEFAULT '{}',
    source_rank        smallint NOT NULL CHECK (source_rank BETWEEN 1 AND 4),
    published_at       timestamptz NOT NULL,
    current_version_id text,
    amends_doc_id      text REFERENCES raw.documents DEFERRABLE INITIALLY DEFERRED,
    run_id             uuid NOT NULL REFERENCES ops.runs,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_id),
    CHECK (starts_with(current_version_id, doc_id || ':'))
);

-- One row per document x content hash. Never updated except to drop raw_gz after 30 days.
CREATE TABLE raw.document_versions (
    version_id   text PRIMARY KEY,
    doc_id       text NOT NULL REFERENCES raw.documents,
    content_hash text NOT NULL,
    text         text NOT NULL,  -- every char_start / char_end points into this
    body_status  text NOT NULL CHECK (body_status IN ('full', 'partial', 'headline_only')),
    fetch_error  text,
    feed_snippet text,
    extractor    text,
    raw_sha256   text,
    raw_gz       bytea,
    fetched_at   timestamptz NOT NULL,
    run_id       uuid NOT NULL REFERENCES ops.runs,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (doc_id, content_hash),
    CHECK (version_id = doc_id || ':' || left(content_hash, 12))
);

-- Deferred so a document and its first version can be inserted in one transaction.
ALTER TABLE raw.documents
    ADD FOREIGN KEY (current_version_id) REFERENCES raw.document_versions
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX ON raw.documents USING gin (tickers);
CREATE INDEX ON raw.documents USING gin (items);
CREATE INDEX ON raw.documents (published_at);
CREATE INDEX ON raw.documents (form_type, fiscal_period);
CREATE INDEX ON raw.documents (canonical_doc_id);
