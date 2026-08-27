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
    pass


if __name__ == "__main__":
    app()
