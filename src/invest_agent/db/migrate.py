"""Apply the SQL files in `migrations/` in order, each exactly once.

- `NNNN_name.sql` runs in one transaction. `NNNN_name.notx.sql` runs outside one,
  for statements such as `CREATE INDEX CONCURRENTLY`.
- An applied file must never change; a changed checksum stops the run.
- Forward only: undo a migration by adding a new one.
"""

import hashlib
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import psycopg

# Any constant works; every `invest db migrate` process must use the same one.
LOCK_KEY = 4_217_001

_FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+(\.notx)?\.sql$")

_BOOTSTRAP = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.schema_migrations (
    version    text PRIMARY KEY,
    name       text NOT NULL,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str
    checksum: str
    transactional: bool


def discover(directory: Path | None = None) -> list[Migration]:
    root = directory or Path(str(files("invest_agent.db") / "migrations"))
    migrations = []
    for path in sorted(root.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if not match:
            raise MigrationError(f"bad migration file name: {path.name}")
        raw = path.read_bytes()
        migrations.append(
            Migration(
                version=match.group(1),
                name=path.name,
                sql=raw.decode(),
                checksum=hashlib.sha256(raw).hexdigest(),
                transactional=match.group(2) is None,
            )
        )
    versions = [m.version for m in migrations]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    if duplicates:
        raise MigrationError(f"duplicate migration versions: {', '.join(duplicates)}")
    return migrations


def migrate(conninfo: str, migrations: list[Migration] | None = None) -> list[str]:
    """Apply pending migrations and return the names of the files applied."""
    migrations = discover() if migrations is None else migrations
    with psycopg.connect(conninfo, autocommit=True) as conn:
        # Waits instead of failing, so a deploy and a manual run can't apply a file twice.
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            conn.execute(_BOOTSTRAP)
            rows = conn.execute("SELECT version, checksum FROM ops.schema_migrations").fetchall()
            applied = dict(rows)
            changed = [
                m.name for m in migrations if applied.get(m.version, m.checksum) != m.checksum
            ]
            if changed:
                raise MigrationError(f"applied migrations were edited: {', '.join(changed)}")

            done = []
            for m in migrations:
                if m.version in applied:
                    continue
                if m.transactional:
                    with conn.transaction():
                        _apply(conn, m)
                else:
                    _apply(conn, m)
                done.append(m.name)
            return done
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def _apply(conn: psycopg.Connection, m: Migration) -> None:
    conn.execute(m.sql)
    conn.execute(
        "INSERT INTO ops.schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
        (m.version, m.name, m.checksum),
    )
