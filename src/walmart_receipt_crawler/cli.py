from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich import box
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeElapsedColumn,
)

from .crawler import WalmartCrawler, Receipt
from .pdf_utils import merge_pdfs

console = Console()

DEFAULT_DAYS = 90


def _parse_date(value: Optional[str], default: datetime) -> datetime:
    if value is None:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise click.BadParameter("Use YYYY-MM-DD format, e.g., 2025-01-31")


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--start",
    "start_str",
    metavar="YYYY-MM-DD",
    help=f"Start date (default: today - {DEFAULT_DAYS} days)",
)
@click.option(
    "--end",
    "end_str",
    metavar="YYYY-MM-DD",
    help="End date (default: today)",
)
@click.option(
    "--out-dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=Path("receipts"),
    show_default=True,
    help="Directory to save receipt PDFs",
)
@click.option(
    "--combined/--separate",
    default=False,
    show_default=True,
    help="Combine all receipts into a single PDF or save separately",
)
@click.option(
    "--headful/--headless",
    default=True,
    show_default=True,
    help="Run a visible browser (recommended to pass bot checks)",
)
@click.option(
    "--profile-dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=Path(".playwright/walmart-profile"),
    show_default=True,
    help="Persistent browser profile directory to keep your Walmart session",
)
@click.option(
    "--browser",
    type=click.Choice(["chromium", "chrome"], case_sensitive=False),
    default="chromium",
    show_default=True,
    help="Browser engine/channel to use; 'chrome' may reduce bot checks",
)
@click.option(
    "--use-existing-browser",
    is_flag=True,
    default=False,
    help="Connect to existing browser (launch Edge/Chrome with: msedge --remote-debugging-port=9222)",
)
@click.option(
    "--remote-debugging-port",
    type=int,
    default=9222,
    show_default=True,
    help="CDP port for connecting to existing browser",
)
@click.option(
    "--max",
    "max_count",
    type=int,
    default=None,
    help="Max number of receipts to download (for quick runs)",
)
@click.option(
    "--timeout",
    type=int,
    default=45,
    show_default=True,
    help="Navigation timeout in seconds",
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug dumps (HTML, screenshot, receipt extraction logs)",
)
@click.version_option()
def main(
    start_str: Optional[str],
    end_str: Optional[str],
    out_dir: Path,
    combined: bool,
    headful: bool,
    profile_dir: Path,
    browser: str,
    use_existing_browser: bool,
    remote_debugging_port: int,
    max_count: Optional[int],
    timeout: int,
    debug: bool,
) -> None:
    """Crawl Walmart order receipts and export to PDF.

    Example:
      uv run walmart-receipt-crawler --start 2025-01-01 --end 2025-03-31 --combined
    """
    # No credentials collected; you'll log in manually in the opened browser if needed.

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = _parse_date(start_str, today - timedelta(days=DEFAULT_DAYS))
    end = _parse_date(end_str, today)
    if end < start:
        raise click.BadParameter("End date must be on or after start date")

    out_dir.mkdir(parents=True, exist_ok=True)

    console.print(
        Panel(
            f"Crawling Walmart receipts from [bold]{start.date()}[/] to [bold]{end.date()}[/]",
            title="walmart-receipt-crawler",
            box=box.ROUNDED,
        )
    )

    if use_existing_browser:
        console.print(
            Panel(
                f"[cyan]Connecting to existing browser on port {remote_debugging_port}...[/cyan]\n\n"
                f"Make sure you've started Edge/Chrome with:\n"
                f"[yellow]msedge.exe --remote-debugging-port={remote_debugging_port}[/yellow]\n\n"
                f"And logged in to Walmart before running this tool.",
                title="ℹ️  Using Existing Browser",
                box=box.ROUNDED,
            )
        )

    combined_path: Optional[Path] = None
    if combined:
        combined_name = f"walmart_receipts_{start.date()}_to_{end.date()}.pdf"
        combined_path = out_dir / combined_name

    try:
        with WalmartCrawler(
            headless=not headful,
            timeout=timeout,
            console=console,
            profile_dir=profile_dir,
            browser=browser,
            use_existing_browser=use_existing_browser,
            remote_debugging_port=remote_debugging_port,
            debug=debug,
        ) as crawler:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TimeElapsedColumn(),
                console=console,
                transient=True,
            ) as progress:
                t = progress.add_task(
                    "Opening Walmart orders (login if prompted)...", total=None
                )
                crawler.ensure_logged_in_and_open_orders()
                progress.remove_task(t)

            receipts: list[Receipt] = crawler.collect_receipts(
                start=start, end=end, max_count=max_count
            )

            if not receipts:
                console.print(
                    Panel(
                        "No receipts found in the specified date range.", style="yellow"
                    )
                )
                return

            saved_paths: list[Path] = []
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(
                    "Saving receipts as PDF...", total=len(receipts)
                )
                for r in receipts:
                    pdf_path = crawler.save_receipt_pdf(r, out_dir=out_dir)
                    saved_paths.append(pdf_path)
                    progress.advance(task)

            if combined and saved_paths:
                assert combined_path is not None  # combined=True guarantees this
                merge_pdfs(saved_paths, combined_path)
                console.print(
                    Panel(
                        f"Combined PDF written to\n[green]{combined_path}[/]",
                        title="Done",
                        box=box.ROUNDED,
                    )
                )
            else:
                console.print(
                    Panel(
                        f"Saved {len(saved_paths)} PDF(s) to\n[green]{out_dir.resolve()}[/]",
                        title="Done",
                        box=box.ROUNDED,
                    )
                )

    except KeyboardInterrupt:
        console.print("Aborted by user.")
        sys.exit(130)
    except Exception as e:
        # Do not leak credentials
        console.print(Panel(f"[red]Error:[/] {e}", title="Failed", box=box.ROUNDED))
        raise


if __name__ == "__main__":
    main()
