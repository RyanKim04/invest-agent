"""`invest` command line."""

from pathlib import Path

import typer

from invest_agent.db import fixtures, migrate
from invest_agent.settings import migrate_database_url

app = typer.Typer(no_args_is_help=True, add_completion=False)
db = typer.Typer(no_args_is_help=True, help="Schema migrations and test fixtures.")
app.add_typer(db, name="db")


@db.command("migrate")
def db_migrate() -> None:
    """Apply pending migrations."""
    applied = migrate.migrate(migrate_database_url())
    for name in applied:
        typer.echo(f"applied {name}")
    typer.echo(f"{len(applied)} migration(s) applied")


@db.command("load-fixtures")
def db_load_fixtures(directory: Path) -> None:
    """Load the .sql fixture files in DIRECTORY."""
    for name in fixtures.load(migrate_database_url(), directory):
        typer.echo(f"loaded {name}")


def _not_built(step: str) -> None:
    typer.echo(f"not built yet ({step})", err=True)
    raise typer.Exit(2)


@app.command()
def ingest() -> None:
    """Fetch new filings, news and prices."""
    _not_built("F2")


@app.command()
def process() -> None:
    """Copy check, chunks, embeddings, events and summaries."""
    _not_built("F4, F6")


@app.command()
def search() -> None:
    """Search chunks."""
    _not_built("F7")


@app.command()
def report() -> None:
    """Generate the slot's report."""
    _not_built("M1")


@app.command()
def deliver() -> None:
    """Email the slot's report."""
    _not_built("M1")


@app.command()
def backup() -> None:
    """Dump Python-owned schemas to Object Storage."""
    _not_built("F8")
