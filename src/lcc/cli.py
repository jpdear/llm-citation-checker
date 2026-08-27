from pathlib import Path

import click


@click.group()
def app():
    pass


@app.command()
@click.pass_context
@click.argument("source", type=click.File("r"), default="-")
@click.option(
    "--url",
    "urls",
    multiple=True,
    help="Check SOURCE against these URLs instead of any found in SOURCE.",
)
def check(ctx, source, urls):
    file_path = Path(source)

    if not file_path.is_file():
        ctx.exit(1)


if __name__ == "__main__":
    app()
