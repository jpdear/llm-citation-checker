from pathlib import Path

import click

from lcc.models import init_db


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

    # TODO: Have the system parse source for urls

    print(f"Found {len(candidate_urls)} urls")


if __name__ == "__main__":
    app()
