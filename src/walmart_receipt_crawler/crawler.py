from __future__ import annotations

import random
from dataclasses import dataclass
import re
from urllib.parse import urlparse
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
    order_date: Optional[datetime] = None
    detail_url: Optional[str] = None
    pdf_filename: Optional[str] = None
    # optional metadata to build correct detail URL
    group_id: Optional[int] = None
    store_purchase: Optional[bool] = None
    order_type: str = "online"  # "store", "pickup", or "online"

    def __or__(self, other: Receipt) -> Receipt:
        if self.order_id != other.order_id:
            return other
        new_id = self.order_id
        match (self.order_date, other.order_date):
            case (None, None):
                new_date = None
            case (dt, None) | (None, dt):
                new_date = dt
            case (dt1, dt2):
                new_date = dt2
        match (self.detail_url, other.detail_url):
            case (None, None):
                new_url = None
            case (url, None) | (None, url):
                new_url = url
            case (url1, url2):
                new_url = url2
        match (self.pdf_filename, other.pdf_filename):
            case (None, None):
                new_pdf = None
            case (pdf, None) | (None, pdf):
                new_pdf = pdf
            case (pdf1, pdf2):
                new_pdf = pdf2
        match (self.group_id, other.group_id):
            case (None, None):
                new_group = None
            case (gid, None) | (None, gid):
                new_group = gid
            case (gid1, gid2):
                new_group = gid2
        match (self.store_purchase, other.store_purchase):
            case (None, None):
                new_store = None
            case (sp, None) | (None, sp):
                new_store = sp
            case (sp1, sp2):
                new_store = sp1 or sp2
        match (self.order_type, other.order_type):
            case ("online", ot) | (ot, "online"):
                new_type = ot
            case (ot1, ot2):
                new_type = ot2
        return Receipt(
            order_id=new_id,
            order_date=new_date,
            detail_url=new_url,
            pdf_filename=new_pdf,
            group_id=new_group,
            store_purchase=new_store,
            order_type=new_type,
        )


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
        debug: bool = False,
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
        self.debug = debug
        self.debug_dir = Path("debug_dumps") if debug else None
        if self.debug_dir:
            self.debug_dir.mkdir(parents=True, exist_ok=True)

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

        # Second attempt: new structure with buttons (data-automation-id="view-order-details-link-<orderId>")
        def parse_order_containers():
            if receipts:  # Skip if we already collected using anchors
                return
            containers = page.query_selector_all("div[data-testid^='order-']")
            collected: dict[str, Receipt] = {}
            for c in containers:
                try:
                    # Order ID from view details button or start-return link
                    btn = c.query_selector(
                        "button[data-automation-id^='view-order-details-link-']"
                    )
                    order_id = None
                    if btn:
                        attr = btn.get_attribute("data-automation-id") or ""
                        if attr.startswith("view-order-details-link-"):
                            order_id = attr.split("view-order-details-link-")[-1]
                    if not order_id:
                        ret_link = c.query_selector(
                            "a[data-automation-id^='start-return-link-']"
                        )
                        if ret_link:
                            attr = ret_link.get_attribute("data-automation-id") or ""
                            if attr.startswith("start-return-link-"):
                                order_id = attr.split("start-return-link-")[-1]
                    if not order_id:
                        continue
                    # Date from header text (h2) e.g. "Nov 08, 2025 purchase" or embedded in aria-label
                    h2 = c.query_selector("h2")
                    date_text = h2.inner_text() if h2 else None
                    if date_text:
                        # Extract canonical date substring
                        m = re.search(
                            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{2},\s+\d{4}",
                            date_text,
                        )
                        date_sub = m.group(0) if m else date_text
                    else:
                        # Fallback: try aria-label on button
                        date_sub = None
                        if btn:
                            aria = btn.get_attribute("aria-label") or ""
                            # First try full date with year
                            m = re.search(
                                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{2},\s+\d{4}",
                                aria,
                            )
                            if m:
                                date_sub = m.group(0)
                            else:
                                # Try date without year (e.g., "Nov 05" in aria-labels)
                                # Assume current year if not specified
                                m = re.search(
                                    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{2}",
                                    aria,
                                )
                                if m:
                                    # Append current year
                                    current_year = datetime.now().year
                                    date_sub = f"{m.group(0)}, {current_year}"
                    order_date = self._parse_order_date(date_sub) if date_sub else None
                    if not order_date:
                        continue
                    if order_date < start or order_date > end:
                        continue
                    group_id = 0  # heuristic default for single-group orders
                    # Determine order type from text content
                    # Check in priority order: curbside (most specific) > store > online
                    all_text = c.inner_text()
                    is_curbside = "Curbside" in all_text or "curbside" in all_text
                    store_purchase = "Store purchase" in all_text

                    # Determine order type
                    if is_curbside:
                        order_type = "pickup"
                        store_purchase = (
                            False  # Override, it's not actually a store purchase
                        )
                    elif store_purchase:
                        order_type = "store"
                    else:
                        order_type = "online"
                    if order_id in collected:
                        collected[order_id] = collected[order_id] | Receipt(
                            order_id=order_id,
                            order_date=order_date,
                            detail_url=None,
                            pdf_filename=f"walmart_{order_date.date()}_{order_id}.pdf",
                            group_id=group_id,
                            store_purchase=store_purchase,
                            order_type=order_type,
                        )
                    else:
                        collected[order_id] = Receipt(
                            order_id=order_id,
                            order_date=order_date,
                            detail_url=None,
                            pdf_filename=f"walmart_{order_date.date()}_{order_id}.pdf",
                            group_id=group_id,
                            store_purchase=store_purchase,
                            order_type=order_type,
                        )
                except Exception:
                    continue
            for r in collected.values():
                # Construct appropriate detail URL based on order type
                if r.order_type == "store":
                    detail_url = f"https://www.walmart.com/orders/{r.order_id}?groupId={r.group_id}&storePurchase=true"
                elif r.order_type == "pickup":
                    # Pickup orders use the same base URL as online orders but may have different details page
                    detail_url = f"https://www.walmart.com/orders/{r.order_id}?groupId={r.group_id}"
                else:  # online
                    detail_url = f"https://www.walmart.com/orders/{r.order_id}"
                r.detail_url = detail_url
                receipts.append(r)
            if self.debug:
                try:
                    assert self.debug_dir is not None
                    (self.debug_dir / "parse_log.txt").open(
                        "a", encoding="utf-8"
                    ).write(
                        f"Parsed {len(containers)} order containers, collected {len(receipts)} receipts via containers\n"
                    )
                except Exception:
                    pass

        parse_order_containers()

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
            parse_order_containers()
            if max_count and len(receipts) >= max_count:
                break
        # Dedupe receipts by order_id
        if receipts:
            unique = {}
            for r in receipts:
                unique[r.order_id] = r
            receipts = list(unique.values())

        # Debug artifacts
        if self.debug:
            try:
                assert self.debug_dir is not None
                if not receipts:
                    (self.debug_dir / "orders_page.html").write_text(
                        page.content(), encoding="utf-8"
                    )
                    page.screenshot(
                        path=str(self.debug_dir / "orders_page.png"), full_page=True
                    )
                # Always record structured state JSON for inspection
                import json

                state_path = self.debug_dir / "orders_state.json"
                state = [
                    {
                        "order_id": r.order_id,
                        "order_date": r.order_date.isoformat()
                        if r.order_date
                        else None,
                        "detail_url": r.detail_url,
                        "pdf_filename": r.pdf_filename,
                        "group_id": r.group_id,
                        "store_purchase": r.store_purchase,
                        "order_type": r.order_type,
                    }
                    for r in receipts
                ]
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            except Exception:
                pass

        if max_count:
            receipts = receipts[:max_count]
        return receipts

    # Receipt PDF -------------------------------------------------------------------------
    def save_receipt_pdf(self, receipt: Receipt, out_dir: Path) -> Path:
        page = self._ensure_page()
        assert receipt.pdf_filename is not None
        pdf_path = out_dir / receipt.pdf_filename
        # Probe candidate detail URLs, preserving required query params
        base = f"https://www.walmart.com/orders/{receipt.order_id}"
        group_id = receipt.group_id if receipt.group_id is not None else 0
        parsed = urlparse(receipt.detail_url) if receipt.detail_url else None
        existing_qs = f"?{parsed.query}" if parsed and parsed.query else ""
        is_store = bool(
            (receipt.store_purchase is True)
            or (parsed and "storePurchase=true" in (parsed.query or ""))
        )
        candidates = []
        # Always try the provided detail_url first (preserves all args exactly)
        if receipt.detail_url:
            candidates.append(receipt.detail_url)
        # Derive a /details variant but keep the same query string if any
        if existing_qs:
            candidates.append(f"{base}/details{existing_qs}")
        else:
            # If no query yet, build one based on heuristics
            if is_store:
                candidates.append(f"{base}?groupId={group_id}&storePurchase=true")
                candidates.append(
                    f"{base}/details?groupId={group_id}&storePurchase=true"
                )
            else:
                # Try variants for pickup and online orders
                candidates.append(f"{base}?groupId={group_id}")
                candidates.append(f"{base}/details?groupId={group_id}")
                # Also try without groupId for pickup orders
                candidates.append(f"{base}/details")
                candidates.append(base)
        success = False
        for url in candidates:
            try:
                page.goto(url)
                page.wait_for_load_state("domcontentloaded")
                # Heuristic: presence of return link or print word indicates details page
                has_return_link = bool(
                    page.query_selector(
                        f"a[href*='/orders/{receipt.order_id}/returns']"
                    )
                )
                content_lower = page.content().lower()
                has_print_word = "print" in content_lower and "receipt" in content_lower
                if has_return_link or has_print_word:
                    receipt.detail_url = url  # update with confirmed working URL
                    success = True
                    break
            except Exception:
                continue
        if not success:
            # As a robust fallback, return to orders list and click the button for this order id
            try:
                page.goto(ORDERS_URL)
                page.wait_for_load_state("domcontentloaded")
                btn = page.query_selector(
                    f"button[data-automation-id='view-order-details-link-{receipt.order_id}']"
                )
                if btn:
                    btn.click()
                    page.wait_for_load_state("domcontentloaded")
                    # Preserve the final URL including its query params
                    try:
                        receipt.detail_url = page.url
                    except Exception:
                        pass
                    success = True
            except Exception:
                pass
        try:
            page.pdf(path=str(pdf_path), format="A4")
        except Exception as e:
            if self.debug:
                try:
                    assert self.debug_dir is not None
                    (self.debug_dir / "parse_log.txt").open(
                        "a", encoding="utf-8"
                    ).write(
                        f"Failed to generate PDF for order {receipt.order_id} (last URL: {page.url}): {e}\n"
                    )
                except Exception:
                    pass
            raise
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
