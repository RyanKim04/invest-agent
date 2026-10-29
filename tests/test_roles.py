import psycopg
import pytest

from invest_agent.db.migrate import migrate

RUN_ID = "00000000-0000-0000-0000-000000000001"

INSERT_DOCUMENT = """
INSERT INTO raw.documents
    (doc_id, canonical_doc_id, source, source_id, title, doc_type, source_rank,
     published_at, run_id)
VALUES ('d1', 'd1', 'finnhub', 'x1', 'title', 'news', 4, now(), %s)
"""


@pytest.fixture
def migrated_db(empty_db: str) -> str:
    migrate(empty_db)
    with psycopg.connect(empty_db) as conn:
        conn.execute("INSERT INTO ops.runs (run_id, trigger) VALUES (%s, 'test')", (RUN_ID,))
    return empty_db


def test_dbt_role_cannot_write_python_tables(migrated_db: str):
    with psycopg.connect(migrated_db) as conn:
        conn.execute("SET ROLE invest_dbt")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(INSERT_DOCUMENT, (RUN_ID,))


def test_dbt_role_can_read_python_tables(migrated_db: str):
    with psycopg.connect(migrated_db) as conn:
        conn.execute("SET ROLE invest_dbt")
        conn.execute("SELECT count(*) FROM raw.documents").fetchone()


def test_app_role_can_write_python_tables(migrated_db: str):
    with psycopg.connect(migrated_db) as conn:
        conn.execute("SET ROLE invest_app")
        conn.execute(INSERT_DOCUMENT, (RUN_ID,))


def test_app_role_cannot_touch_migration_history(migrated_db: str):
    with psycopg.connect(migrated_db) as conn:
        conn.execute("SET ROLE invest_app")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM ops.schema_migrations")


def test_reader_role_cannot_read_raw(migrated_db: str):
    with psycopg.connect(migrated_db) as conn:
        conn.execute("SET ROLE invest_reader")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM raw.documents")
