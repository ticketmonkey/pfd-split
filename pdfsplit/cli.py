"""Command-line interface (§9).

A thin ``click`` skin over the pure engine (§5) and emitter (§7). Three commands:
``inspect`` (read-only outline survey), ``split`` (plan → preview → emit), and a
``serve`` stub that stage 03 fills in.

Exit codes (§9): ``0`` success · ``1`` error (no outline, level not found, unreadable
file) · ``2`` refused because a book still has unresolved boundary flags. In a
multi-book run a failing book prints its error, does not abort the run, and makes the
final exit code non-zero.
"""

from __future__ import annotations

import os

import click
import fitz
from rich.console import Console
from rich.table import Table

from . import engine, sidecar, write
from .errors import LevelNotFoundError, NoOutlineError
from .model import Band

_STATUS_STYLE = {
    "ok": ("ok", "green"),
    "snap_proposed": ("SNAP", "yellow"),
    "unverified": ("unver", "red"),
    "not_applicable": ("n/a", "dim"),
}


def _book_slug(path: str) -> str:
    """The output slug for a source, cheaply — no full plan, no text extraction."""
    doc = fitz.open(path)
    try:
        title, _ = engine.derive_title(doc, path)
    finally:
        doc.close()
    return engine.slugify(title)


# --------------------------------------------------------------------------- #
# inspect
# --------------------------------------------------------------------------- #

@click.command()
@click.argument("book", type=click.Path(exists=True, dir_okay=False))
@click.option("--max-level", default=3, show_default=True,
              help="Deepest outline level to display.")
def inspect(book: str, max_level: int) -> None:
    """Survey a book's outline. Writes nothing."""
    console = Console()
    try:
        info = engine.outline_summary(book, max_level=max_level)
    except (NoOutlineError, LevelNotFoundError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise SystemExit(1)
    except Exception as exc:  # unreadable file, etc.
        console.print(f"[red]error:[/red] {book}: {exc}")
        raise SystemExit(1)

    title = info["book_title"]
    if info["author"]:
        title += f"  ·  {info['author']}"
    console.print(f"[bold]{title}[/bold]")
    layer = "yes" if info["has_text_layer"] else "no"
    console.print(
        f"{info['total_pages']} pages · text layer: {layer} · "
        f"{info['words_per_page']:.0f} words/page"
    )

    for level in sorted(info["levels"]):
        data = info["levels"][level]
        console.print(f"\n[bold]Level {level}[/bold] — {data['count']} entries")
        table = Table(show_edge=False, pad_edge=False, box=None)
        table.add_column("Title", overflow="fold")
        table.add_column("Pages", justify="right")
        table.add_column("Words", justify="right")
        for e in data["entries"]:
            table.add_row(
                e["title"],
                f"{e['start'] + 1}–{e['end'] + 1}",
                f"{e['words']:,}",
            )
        console.print(table)

    for w in info["warnings"]:
        console.print(f"[yellow]warning:[/yellow] {w}")


# --------------------------------------------------------------------------- #
# split
# --------------------------------------------------------------------------- #

def _preview(console: Console, plan) -> None:
    """Render the §9 preview table — one row per chunk, skipped ones included."""
    table = Table(show_edge=False, pad_edge=False, box=None, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Status")
    table.add_column("Contents", overflow="fold")
    table.add_column("Pages", justify="right")
    table.add_column("Words", justify="right")
    table.add_column("Note", overflow="fold")

    band = plan.band
    for c in plan.chunks:
        pages = f"{c.start + 1}–{c.end + 1}"
        words = f"{c.words:,}"
        if c.skip_reason is not None:
            table.add_row("-", "[dim]skip[/dim]", c.label, pages, words,
                          f"[dim]{c.skip_reason}[/dim]")
            continue
        if not c.include:
            table.add_row("-", "[dim]excl[/dim]", c.label, pages, words,
                          "[dim]excluded[/dim]")
            continue

        text, style = _STATUS_STYLE.get(c.verify.status, (c.verify.status, ""))
        note = ""
        if c.verify.status == "snap_proposed" and c.verify.proposed_start is not None:
            note = f"→ start p.{c.verify.proposed_start + 1}"
        elif c.words < band.floor:
            note = "[dim]under floor[/dim]"
        elif c.words > band.ceiling:
            note = "[dim]over ceiling[/dim]"
        status = f"[{style}]{text}[/{style}]" if style else text
        table.add_row(str(c.seq), status, c.label, pages, words, note)

    console.print(table)

    emitted = [c for c in plan.chunks if c.skip_reason is None and c.include]
    ok = sum(1 for c in emitted if c.verify.status == "ok")
    prop = sum(1 for c in emitted if c.verify.status == "snap_proposed")
    unver = sum(1 for c in emitted if c.verify.status == "unverified")
    bits = [f"{ok} of {len(emitted)} verified"]
    if prop:
        bits.append(f"{prop} correction{'s' if prop != 1 else ''} proposed")
    if unver:
        bits.append(f"{unver} unverified")
    console.print("  " + " · ".join(bits))


def _unresolved(plan) -> bool:
    return any(
        c.skip_reason is None and c.include
        and c.verify.status in ("snap_proposed", "unverified")
        for c in plan.chunks
    )


def _split_one(console: Console, book: str, *, level: int, notebooklm: bool,
               band: Band, out: str, prefix_book: bool, also_text: bool,
               header_page: bool, keep, drop, dry_run: bool, yes: bool,
               force: bool) -> int:
    """Plan, preview and (unless refused) emit a single book. Returns its exit code."""
    console.rule(f"[bold]{os.path.basename(book)}[/bold]")

    slug = _book_slug(book)
    sidecar_file = sidecar.sidecar_path(out, slug)
    saved = sidecar.load(sidecar_file, book)
    overrides = saved["overrides"] if saved else None

    try:
        plan = engine.plan_book(
            book, level=level, notebooklm=notebooklm, band=band,
            extra_keep=tuple(keep), extra_drop=tuple(drop), overrides=overrides,
        )
    except (NoOutlineError, LevelNotFoundError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        return 1
    except Exception as exc:
        console.print(f"[red]error:[/red] {book}: {exc}")
        return 1

    _preview(console, plan)
    for w in plan.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")

    if dry_run:
        console.print("[dim]--dry-run: nothing written[/dim]")
        return 0

    if yes:
        if _unresolved(plan) and not force:
            console.print(
                "[red]refused:[/red] this book has proposed or unverified "
                "boundaries. Review it in the web UI, or pass --force to emit "
                "them unchanged."
            )
            return 2
    else:
        if not click.confirm("Proceed with this split?", default=False):
            console.print("[dim]skipped[/dim]")
            return 0

    result = write.write_book(
        plan, out, prefix_book=prefix_book, also_text=also_text,
        header_page=header_page,
    )
    sidecar.save(
        sidecar_file, source=book, level=level, notebooklm=notebooklm,
        band=band, overrides=overrides,
    )
    console.print(
        f"[green]wrote[/green] {len(result.written)} chunks to {result.out_dir}"
    )
    return 0


@click.command()
@click.argument("books", nargs=-1, required=True,
                type=click.Path(exists=True, dir_okay=False))
@click.option("--level", default=1, show_default=True, help="Outline depth to split on.")
@click.option("--notebooklm", "-n", is_flag=True, help="Enable the merge pass.")
@click.option("--floor", default=6000, show_default=True, help="Merge below this many words.")
@click.option("--target", default=12000, show_default=True, help="Advisory ideal (preview only).")
@click.option("--ceiling", default=20000, show_default=True, help="Subdivide above this many words.")
@click.option("--out", default="./out", show_default=True, type=click.Path(),
              help="Output root; each book goes in <out>/<book_slug>/.")
@click.option("--prefix-book", is_flag=True, help="Prefix filenames with the book slug.")
@click.option("--also-text", is_flag=True, help="Also emit a .md per chunk.")
@click.option("--no-header-page", is_flag=True, help="Suppress the generated header page.")
@click.option("--keep", multiple=True, help="Repeatable regex; force-keep matching titles.")
@click.option("--drop", multiple=True, help="Repeatable regex; force-drop matching titles.")
@click.option("--dry-run", is_flag=True, help="Print the plan, write nothing.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation (batch use).")
@click.option("--force", is_flag=True, help="With --yes, proceed despite unresolved flags.")
def split(books, level, notebooklm, floor, target, ceiling, out, prefix_book,
          also_text, no_header_page, keep, drop, dry_run, yes, force) -> None:
    """Split one or more books into chapter-sized PDFs."""
    console = Console()
    band = Band(floor=floor, target=target, ceiling=ceiling)
    worst = 0
    for book in books:
        code = _split_one(
            console, book, level=level, notebooklm=notebooklm, band=band,
            out=out, prefix_book=prefix_book, also_text=also_text,
            header_page=not no_header_page, keep=keep, drop=drop,
            dry_run=dry_run, yes=yes, force=force,
        )
        # An outright error (1) is more severe than a refusal (2).
        if code == 1:
            worst = 1
        elif code == 2 and worst != 1:
            worst = 2
    if worst:
        raise SystemExit(worst)


# --------------------------------------------------------------------------- #
# serve (§10)
# --------------------------------------------------------------------------- #

@click.command()
@click.option("--library", type=click.Path(file_okay=False), default=".",
              show_default=True, help="Directory of source PDFs to review.")
@click.option("--out", type=click.Path(), default="./out", show_default=True,
              help="Output root; each book goes in <out>/<book_slug>/.")
@click.option("--port", default=8000, show_default=True, help="Localhost port.")
@click.option("--no-browser", is_flag=True, help="Do not open a browser tab.")
def serve(library, out, port, no_browser) -> None:
    """Launch the localhost boundary-review UI.

    Binds 127.0.0.1 only — these are copyrighted books you own; nothing leaves the
    machine and nothing is uploaded.
    """
    import uvicorn

    from .web import create_app

    app = create_app(library, out)
    url = f"http://127.0.0.1:{port}"
    click.echo(f"pdfsplit serve — reviewing {os.path.abspath(library)}")
    click.echo(f"listening on {url} (127.0.0.1 only)")
    if not no_browser:
        import webbrowser
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=port)


@click.group()
@click.version_option(package_name="pdfsplit", message="%(prog)s %(version)s")
def main() -> None:
    """pdfsplit — split DRM-free technical ebook PDFs into chapter-sized PDFs."""


main.add_command(inspect)
main.add_command(split)
main.add_command(serve)


if __name__ == "__main__":
    main()
