from pathlib import Path

import psycopg
import pytest

from invest_agent.db.migrate import MigrationError, discover, migrate


def test_shipped_migrations_are_well_formed():
    migrations = discover()
    assert [m.version for m in migrations] == sorted(m.version for m in migrations)
    assert migrations[0].name == "0001_roles_and_schemas.sql"


def test_rejects_bad_file_name(tmp_path: Path):
    (tmp_path / "1_init.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="bad migration file name"):
        discover(tmp_path)


def test_rejects_duplicate_versions(tmp_path: Path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="duplicate"):
        discover(tmp_path)


def test_notx_suffix_runs_outside_a_transaction(tmp_path: Path):
    (tmp_path / "0001_index.notx.sql").write_text("SELECT 1;")
    assert discover(tmp_path)[0].transactional is False


def test_second_run_applies_nothing(empty_db: str):
    assert len(migrate(empty_db)) == len(discover())
    assert migrate(empty_db) == []


def test_edited_migration_stops_the_run(empty_db: str, tmp_path: Path):
    path = tmp_path / "0001_t.sql"
    path.write_text("CREATE TABLE public.t (id int);")
    migrate(empty_db, discover(tmp_path))
    path.write_text("CREATE TABLE public.t (id bigint);")
    with pytest.raises(MigrationError, match="0001_t.sql"):
        migrate(empty_db, discover(tmp_path))


def test_failed_migration_leaves_nothing_behind(empty_db: str, tmp_path: Path):
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE public.ok (id int);")
    (tmp_path / "0002_bad.sql").write_text("CREATE TABLE public.half (id int); SELECT 1/0;")
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrate(empty_db, discover(tmp_path))
    with psycopg.connect(empty_db) as conn:
        assert conn.execute("SELECT to_regclass('public.half')").fetchone() == (None,)
        versions = conn.execute("SELECT version FROM ops.schema_migrations").fetchall()
    assert versions == [("0001",)]
