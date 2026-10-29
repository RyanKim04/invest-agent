-- Group roles. Login users are created per environment and granted one of these.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invest_app') THEN
        CREATE ROLE invest_app NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invest_dbt') THEN
        CREATE ROLE invest_dbt NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invest_reader') THEN
        CREATE ROLE invest_reader NOLOGIN;
    END IF;
END
$$;

CREATE EXTENSION IF NOT EXISTS vector;

-- Python owns these four schemas.
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS content;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS report;

GRANT USAGE ON SCHEMA raw, content, ops, report TO invest_app, invest_dbt;

-- Default privileges cover tables that later migrations create as this same owner.
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, content, ops, report
    GRANT SELECT, INSERT, UPDATE ON TABLES TO invest_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops
    GRANT DELETE ON TABLES TO invest_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, content, ops, report
    GRANT USAGE ON SEQUENCES TO invest_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, content, ops, report
    GRANT SELECT ON TABLES TO invest_dbt;

-- dbt creates and owns staging, intermediate and marts.
DO $$
BEGIN
    EXECUTE format('GRANT CREATE ON DATABASE %I TO invest_dbt', current_database());
END
$$;
