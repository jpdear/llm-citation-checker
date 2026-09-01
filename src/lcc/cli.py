from collections.abc import Iterable
from pathlib import Path

import click

from lcc.fetch import guard
from lcc.models import ValidationError, init_db, normalize_url


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
def app(ctx, db_path):
    database = init_db(db_path)

    ctx.call_on_close(database.close)


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
def check(source, urls, url_file):
    if urls and url_file:
        raise click.UsageError("--url and --url-file are mutually exclusive.")

    candidate_urls = list(urls)

    if url_file:
        candidate_urls = url_file.read_text(encoding="utf-8").split()

    candidate_urls, rejected = _clean_urls(candidate_urls)

    for raw, reason in rejected:
        click.echo(f"Skipped {raw!r}: {reason}", err=True)

    click.echo(f"Found {len(candidate_urls)} usable URLs")


if __name__ == "__main__":
    app()
