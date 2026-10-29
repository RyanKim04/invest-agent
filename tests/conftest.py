"""Database tests run against a throwaway database created from INVEST_TEST_ADMIN_URL.

Point INVEST_TEST_ADMIN_URL at a superuser connection on a Postgres with pgvector, for example
postgresql://postgres:postgres@localhost:5433/postgres (the `app-db` compose service).
Without it, database tests are skipped.
"""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.conninfo import make_conninfo


@pytest.fixture
def empty_db() -> Iterator[str]:
    admin_url = os.environ.get("INVEST_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("INVEST_TEST_ADMIN_URL is not set")
    name = f"invest_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(admin_url, dbname=name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
