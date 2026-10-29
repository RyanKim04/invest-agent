"""Load SQL fixture files into the database, in file-name order, in one transaction."""

from pathlib import Path

import psycopg


def load(conninfo: str, directory: Path) -> list[str]:
    paths = sorted(directory.glob("*.sql"))
    if not paths:
        raise SystemExit(f"no .sql files in {directory}")
    with psycopg.connect(conninfo) as conn:
        for path in paths:
            conn.execute(path.read_text())
    return [p.name for p in paths]
