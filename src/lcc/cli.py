from collections.abc import Iterable
from pathlib import Path

import click
from peewee import SqliteDatabase

from lcc.fetch import guard
from lcc.models import Source, ValidationError, init_db, normalize_url


def _clean_urls(raw_urls: Iterable[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Split candidates into normalized-and-deduped and rejected-with-reason."""
    seen: dict[str, None] = {}
    rejected: list[tuple[str, str]] = []

    for raw in raw_urls:
        try:
            canonical = normalize_url(raw)
            guard(canonical)
        except ValidationError as exc:
            rejected.append((raw, str(exc)))
        else:
            seen.setdefault(canonical, None)

    return list(seen), rejected


def get_db(ctx: click.Context) -> SqliteDatabase:
    """Open the database on first use and close it when the command exits."""
    state = ctx.ensure_object(dict)

    if "db" not in state:
        state["db"] = init_db(state.get("db_path"))
        ctx.call_on_close(state["db"].close)

    return state["db"]


@click.group()
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    envvar="LCC_DB",
    help="Database file to use. Defaults to a per-user application data directory.",
)
@click.pass_context
def app(ctx: click.Context, db_path: str) -> None:
    ctx.ensure_object(dict)["db_path"] = db_path


@app.command()
@click.argument("source", type=click.File("r"), default="-")
@click.option(
    "--url",
    "urls",
    multiple=True,
    help="Check SOURCE against these URLs instead of any found in SOURCE.",
)
@click.option(
    "--url-file",
    "url_file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    default=None,
    help="Check SOURCE against URLs in a separate file instead of any found in SOURCE.",
)
@click.pass_context
def check(ctx: click.Context, source, urls, url_file):
    if urls and url_file:
        raise click.UsageError("--url and --url-file are mutually exclusive.")

    candidate_urls = list(urls)

    if url_file:
        candidate_urls = url_file.read_text(encoding="utf-8").split()

    candidate_urls, rejected = _clean_urls(candidate_urls)

    for raw, reason in rejected:
        click.echo(f"Skipped {raw!r}: {reason}", err=True)

    database = get_db(ctx)

    with database.atomic():
        sources = [Source.for_url(url) for url in candidate_urls]

    count = len(sources)

    click.echo(f"Found {count} usable URL{'' if count == 1 else 's'}")


if __name__ == "__main__":
    app()
