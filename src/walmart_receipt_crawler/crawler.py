from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from rich.console import Console

# NOTE: Using Playwright for reliability; fallback could be requests+BS4 if needed.
try:
    from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
except ImportError as _e:  # pragma: no cover - runtime dependency check
    raise RuntimeError(
        "playwright is required. Install with 'uv add playwright' and run 'playwright install'."
    ) from _e


ORDERS_URL = "https://www.walmart.com/orders"


@dataclass
class Receipt:
    order_id: str
    order_date: datetime
    detail_url: str
    pdf_filename: str


class WalmartCrawler:
    def __init__(
        self,
        headless: bool = True,
        timeout: int = 45,
        console: Optional[Console] = None,
        profile_dir: Optional[Path] = None,
        browser: str = "chromium",
        use_existing_browser: bool = False,
        remote_debugging_port: int = 9222,
    ):
        self.headless = headless
        self.timeout = timeout * 1000  # playwright ms
        self.console = console or Console()
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self.profile_dir = profile_dir
        self.browser = browser
        self.use_existing_browser = use_existing_browser
        self.remote_debugging_port = remote_debugging_port

    def __enter__(self) -> "WalmartCrawler":
        self._playwright = sync_playwright().start()

        if self.use_existing_browser:
            # Connect to existing browser via CDP
            try:
                self._browser = self._playwright.chromium.connect_over_cdp(
                    f"http://localhost:{self.remote_debugging_port}"
                )
                # Use the first existing context/page or create new one
                contexts = self._browser.contexts
                if contexts:
                    self._context = contexts[0]
                    pages = self._context.pages
                    if pages:
                        self._page = pages[0]
                    else:
                        self._page = self._context.new_page()
                else:
                    self._context = self._browser.new_context()
                    self._page = self._context.new_page()
            except Exception as e:
                raise RuntimeError(
                    f"Failed to connect to browser on port {self.remote_debugging_port}. "
                    f"Make sure browser is running with --remote-debugging-port={self.remote_debugging_port}. "
                    f"Error: {e}"
                ) from e
        else:
            # Launch new browser with persistent context (original behavior)
            engine = self._playwright.chromium
            if self.browser.lower() == "chrome":
                engine = self._playwright.chromium  # playwright uses channel
            # Use persistent context to reduce logins and lower bot suspicion
            if self.profile_dir is None:
                self.profile_dir = Path(".playwright/walmart-profile")
            self.profile_dir.parent.mkdir(parents=True, exist_ok=True)
            self._context = engine.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                channel="chrome" if self.browser.lower() == "chrome" else None,
                viewport={"width": 1380, "height": 820},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            # Set a realistic user agent
            # Access user agent (optional; may be used for future heuristic decisions)
            try:
                _ = self._context.user_agent  # noqa: F841
            except Exception:
                pass
            self._page = self._context.new_page()
            self._page.set_extra_http_headers(
                {
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            self._page.set_default_timeout(self.timeout)
            self._apply_stealth(self._page)

        self._page.set_default_timeout(self.timeout)
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: D401
        try:
            # Don't close context if using existing browser (user controls it)
            if not self.use_existing_browser and self._context:
                self._context.close()
            # Disconnect from browser if we connected via CDP
            if self.use_existing_browser and self._browser:
                self._browser.close()
        finally:
            if self._playwright:
                self._playwright.stop()

    # Authentication / navigation helpers -------------------------------------------------
    def ensure_logged_in_and_open_orders(self) -> None:
        page = self._ensure_page()

        # Skip login checks if using existing browser (assume user is already logged in)
        if self.use_existing_browser:
            # Just navigate to orders page
            if "/orders" not in page.url:
                page.goto(ORDERS_URL)
                page.wait_for_load_state("domcontentloaded")
            # Wait for orders content
            try:
                self._wait_until(
                    lambda: len(
                        page.query_selector_all(
                            "a[href*='/orders/'][href*='/details/']"
                        )
                    )
                    > 0
                    or "/orders" in page.url,
                    timeout_ms=self.timeout * 2,
                )
            except Exception:
                pass
            return

        # Original login flow for launched browser
        # Try going directly to orders
        page.goto(ORDERS_URL)
        page.wait_for_load_state("domcontentloaded")
        # If redirected to login, wait for user to complete it
        if "/account/login" in page.url:
            if self.console:
                self.console.print(
                    "[yellow]Please log in to Walmart in the opened browser and complete any verification (CAPTCHA/TOTP) if prompted.[/yellow]"
                )
            # Wait much longer for manual login/CAPTCHA completion (10x timeout, ~7.5 min default)
            self._wait_until(
                lambda: "/account/login" not in page.url, timeout_ms=self.timeout * 10
            )
        # Additional check: if we're on walmart.com but being challenged with CAPTCHA
        if "walmart.com" in page.url and (
            "/blocked" in page.url or "robot" in page.content().lower()
        ):
            if self.console:
                from rich.prompt import Prompt

                self.console.print(
                    "[yellow]⚠️  Bot/CAPTCHA challenge detected. Please complete it in the browser.[/yellow]"
                )
                Prompt.ask(
                    "Press Enter after you've completed the verification", default=""
                )
            # Give page time to process after CAPTCHA
            page.wait_for_timeout(2000)
        # Ensure we land on orders page after login completes
        if "walmart.com" in page.url and "/orders" not in page.url:
            page.goto(ORDERS_URL)
            page.wait_for_load_state("domcontentloaded")
        # Wait until orders content is likely available, without relying on networkidle
        try:
            self._wait_until(
                lambda: len(
                    page.query_selector_all("a[href*='/orders/'][href*='/details/']")
                )
                > 0
                or "/orders" in page.url,
                timeout_ms=self.timeout * 2,  # Be more patient for initial load
            )
        except Exception:
            # If not found, continue anyway; user may have no orders in range or page structure changed
            pass

    def open_orders_page(self) -> None:
        page = self._ensure_page()
        page.goto(ORDERS_URL)
        # ensure orders content
        page.wait_for_load_state("networkidle")

    # Data collection ---------------------------------------------------------------------
    def collect_receipts(
        self, start: datetime, end: datetime, max_count: Optional[int]
    ) -> List[Receipt]:
        page = self._ensure_page()
        receipts: List[Receipt] = []
        # Simplified logic: scroll/paginate and extract JSON from script tags or elements.
        # Walmart order list items could have data attributes or accessible links.
        # We add fallback selectors; actual site may require adjustments.

        def parse_order_elements():
            elements = page.query_selector_all("a[href*='/orders/'][href*='/details/']")
            for el in elements:
                href = el.get_attribute("href")
                if not href:
                    continue
                order_id = href.split("/")[-1]
                # Attempt to derive date from sibling element
                parent = el.evaluate_handle("(e) => e.closest('div')")
                date_text = None
                try:
                    date_el = parent.query_selector("time") if parent else None
                    if date_el:
                        date_text = date_el.inner_text()
                except Exception:
                    pass
                order_date = self._parse_order_date(date_text) if date_text else None
                if order_date is None:
                    # if no date, skip — could extend by opening page
                    continue
                if order_date < start or order_date > end:
                    continue
                receipts.append(
                    Receipt(
                        order_id=order_id,
                        order_date=order_date,
                        detail_url=f"https://www.walmart.com{href}",
                        pdf_filename=f"walmart_{order_date.date()}_{order_id}.pdf",
                    )
                )

        parse_order_elements()
        # Basic scrolling to load more orders (if virtualization)
        prev_len = -1
        while (max_count is None or len(receipts) < max_count) and len(
            receipts
        ) != prev_len:
            prev_len = len(receipts)
            page.mouse.wheel(0, 2000)
            # Random delay between 3-6 seconds - humans need time to scan/read orders
            delay_ms = random.randint(3000, 6000)
            page.wait_for_timeout(delay_ms)
            parse_order_elements()
            if max_count and len(receipts) >= max_count:
                break

        if max_count:
            receipts = receipts[:max_count]
        return receipts

    # Receipt PDF -------------------------------------------------------------------------
    def save_receipt_pdf(self, receipt: Receipt, out_dir: Path) -> Path:
        page = self._ensure_page()
        page.goto(receipt.detail_url)
        page.wait_for_load_state("networkidle")
        pdf_path = out_dir / receipt.pdf_filename
        # Render full page PDF; could refine to receipt container only.
        page.pdf(path=str(pdf_path), format="A4")
        # Add delay before next PDF request - humans would review each order (5-10 sec)
        delay_ms = random.randint(5000, 10000)
        page.wait_for_timeout(delay_ms)
        return pdf_path

    # Utilities ---------------------------------------------------------------------------
    def _ensure_page(self) -> Page:
        if not self._page:
            raise RuntimeError("Crawler not initialized; use context manager")
        return self._page

    def _apply_stealth(self, page: Page) -> None:
        # Best-effort stealth tweaks; cannot guarantee bypass but reduces automation signals
        page.add_init_script(
            """
            // Pass the Webdriver Test.
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            // Pass the Chrome Test.
            window.chrome = { runtime: {} };
            // Pass the Plugins Length Test.
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            // Pass the Languages Test.
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            """
        )

    def _wait_until(self, predicate, timeout_ms: int) -> None:
        from time import monotonic, sleep

        end = monotonic() + timeout_ms / 1000.0
        while monotonic() < end:
            try:
                if predicate():
                    return
            except Exception:
                pass
            sleep(0.3)
        raise TimeoutError("Timed out waiting for condition")

    @staticmethod
    def _parse_order_date(text: Optional[str]) -> Optional[datetime]:
        if not text:
            return None
        # Walmart format examples: "Jan 31, 2025"; attempt flexible parsing.
        try:
            return datetime.strptime(text.strip(), "%b %d, %Y")
        except ValueError:
            pass
        # Try alternative numeric formats
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text.strip(), fmt)
            except ValueError:
                continue
        return None
