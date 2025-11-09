# walmart-receipt-crawler

CLI utility to log in to your Walmart account (manually in a real browser) and export order receipts as PDF files (either individually or combined). Supports filtering by date range and a persistent browser profile to reduce repeated logins. Built with `click`, `rich`, `playwright`, and `pypdf`.

## Features

- Date range filtering (defaults to last 90 days)
- Separate or combined PDF output
- Progress and status UI with Rich
- Headful browser by default (recommended to pass bot checks)
- Persistent browser profile to keep your session

## Usage

### Standard (launch new browser)

```pwsh
uv run walmart-receipt-crawler --start 2025-01-01 --end 2025-03-31 --combined
```

When a browser opens, log in and complete any verification (CAPTCHA/TOTP) if prompted. Your session will be saved in `.playwright/walmart-profile` by default.

### Using Your Existing Browser (Recommended)

This method avoids repeated CAPTCHAs by using a real Edge/Chrome session you control.

1. Close all existing browser windows.
2. Start Edge (or Chrome) with a remote debugging port:

Edge:

```pwsh
& "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --remote-debugging-port=9222 --user-data-dir="$env:TEMP\\edge-debug"
```

Chrome:

```pwsh
& "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:TEMP\\chrome-debug"
```

1. In that browser window:

- Navigate to <https://www.walmart.com>
- Log in normally
- Complete any CAPTCHA / verification
- Visit <https://www.walmart.com/orders>
- Keep the browser open

1. Run the crawler in a separate terminal:

```pwsh
uv run walmart-receipt-crawler --use-existing-browser --start 2025-01-01 --end 2025-11-09 --combined
```

The tool will connect via CDP to the running browser and reuse your authenticated session.

### More Examples

Limit to first 5 receipts (quick test):
```pwsh
uv run walmart-receipt-crawler --max 5
```

Headless mode (may trigger more bot checks, use cautiously):
```pwsh
uv run walmart-receipt-crawler --headless --start 2025-09-01 --end 2025-11-01
```

## Options

Run with `--help` to see all options:

```sh
uv run walmart-receipt-crawler --help
```

## Notes / Caveats

- Site selectors may change; update `crawler.py` if receipt discovery breaks.
- PDF generation currently snapshots the whole order detail page.
- If using `--use-existing-browser` and you see no receipts, scroll manually in the Orders page first (virtualized lists may need user interaction).
- If you encounter repeated bot challenges, prefer the existing-browser mode.
- Be mindful of rate limits and do not abuse the service.
- Some Walmart order detail pages (notably **Store purchase** entries) 404 unless required query parameters are present. The crawler now probes variants like `?groupId=0&storePurchase=true` automatically; if you manually open an order and see the URL contains `groupId` or `storePurchase` flags, that is expected.

## License

See `LICENSE`.

