from pathlib import Path

import click


@click.group()
def app():
    pass


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

    # TODO: Have the system parse source for urls

    url_list = []

    if urls:
        url_list = [url for url in urls]
    elif url_file:
        with open(url_file, "r") as file:
            url_list = [line for line in file]

    print(f"Found {len(url_list)} urls")


if __name__ == "__main__":
    app()
