# walmart-receipt-crawler

CLI utility to log in to your Walmart account (manually in a real browser) and export order receipts as PDF files (either individually or combined). Supports filtering by date range and a persistent browser profile to reduce repeated logins. Built with `click`, `rich`, `playwright`, and `pypdf`.

## Features

- Date range filtering (defaults to last 90 days)
- Separate or combined PDF output
- Progress and status UI with Rich
- Headful browser by default (recommended to pass bot checks)
- Persistent browser profile to keep your session

## Installation

Use `uv` to manage dependencies:

```sh
uv add click rich playwright pypdf
playwright install chromium
```

## Usage

```sh
uv run walmart-receipt-crawler --start 2025-01-01 --end 2025-03-31 --combined
```

When a browser opens, log in and complete any verification (CAPTCHA/TOTP) if prompted. Your session will be saved in `.playwright/walmart-profile` by default.

## Options

Run with `--help` to see all options:

```sh
uv run walmart-receipt-crawler --help
```

## Notes / Caveats

- Site selectors may change; update `crawler.py` if receipt discovery breaks.
- PDF generation currently snapshots the whole order detail page.
- Be mindful of rate limits and do not abuse the service.

## License

See `LICENSE`.

