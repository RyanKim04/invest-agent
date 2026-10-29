CREATE TABLE ops.runs (
    run_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slot_key    text,  -- morning:2026-10-30; null for manual and backfill runs
    trigger     text NOT NULL CHECK (trigger IN ('schedule', 'manual', 'backfill', 'test')),
    git_sha     text,
    config_hash text,
    status      text NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'succeeded', 'failed', 'skipped')),
    error       text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE TABLE ops.run_steps (
    run_id      uuid NOT NULL REFERENCES ops.runs,
    step        text NOT NULL,
    status      text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'skipped')),
    counts      jsonb NOT NULL DEFAULT '{}',
    error       text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    PRIMARY KEY (run_id, step)
);
