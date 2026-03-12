"""
executor.py – The Hands
Uses Playwright to automate a persistent headless Chromium session on
Stock-Trak: log in once, then execute BUY/SELL orders on demand.

Selectors verified live against https://app.stocktrak.com on 2026-03-10.
"""
import os
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


class StockTrakExecutor:
    """Manages a live browser session to execute trades on Stock-Trak."""

    # ── Selectors (verified live) ──────────────────────────────────────────────
    # Login page  (https://app.stocktrak.com/members/login)
    _LOGIN_URL       = "https://app.stocktrak.com/members/login"
    _SEL_USERNAME    = "#tbLoginUserName"
    _SEL_PASSWORD    = "#Password"
    # The login submit button — target by type to be robust
    _SEL_LOGIN_BTN   = "button[type='submit']"

    # Post-login dashboard landmark
    _DASHBOARD_URL   = "https://app.stocktrak.com/dashboard/standard"

    # Trading pages
    _TRADING_URL     = "https://app.stocktrak.com/trading/equities"
    _CRYPTO_URL      = "https://app.stocktrak.com/trading/cryptocurrency"

    # Trading form fields
    _SEL_SYMBOL      = "#tbSymbol"
    # Autocomplete dropdown populated after typing a ticker
    _SEL_AUTOCMPLT   = "ul.ui-autocomplete li.ui-menu-item"
    # Buy / Sell are <label> toggle buttons — we click by visible text
    # e.g.  page.locator("label.button", has_text="Buy").click()
    _SEL_QUANTITY    = "#tbQuantity"
    _SEL_ORDER_TYPE  = "#ddlOrderType"          # defaults to Market — leave as-is
    # Trading notes field (professor may require this)
    # Trading notes textarea — ONLY appears on the Order Review page
    _SEL_NOTES       = "textarea#trade-notes"             # confirmed live 2026-03-10
    _SEL_PREVIEW     = "#btnPreviewOrder"
    _SEL_CONFIRM     = "#btnPlaceOrder"                   # confirmed live 2026-03-10
    # Success banner after order is placed.
    # NOTE: wait_for_selector only accepts CSS/XPath — Playwright `text=` pseudo-selectors
    # are locator-only and CANNOT be joined by comma here. Use ordered CSS-safe candidates.
    _SEL_SUCCESS_CANDIDATES = [
        ".order-confirmation",           # preferred: dedicated confirmation div
        "#order-confirmation",           # alternative id form
        ".alert-success",                # generic Bootstrap success alert
    ]
    # Text to look for as a last-resort string check if no selector matched
    _SUCCESS_TEXTS = ["Order Confirmation Number", "was sent to the market", "order has been placed"]
    # Direct URL to the open positions page (href from the nav dropdown anchor)
    _POSITIONS_URL = "https://app.stocktrak.com/account/openpositions"
    _DEBUG_DIR = "debug_screenshots"

    # ─────────────────────────────────────────────────────────────────────────
    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._pw       = sync_playwright().start()
        self._browser  = self._pw.chromium.launch(headless=headless)
        self._context  = self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self._page     = self._context.new_page()
        self.logged_in = False

    # ─────────────────────────────────────────────────────────────────────────
    def login(self, username: str, password: str) -> bool:
        """Navigate to the login page, authenticate, and verify dashboard loads."""
        print("[Executor] Logging in to Stock-Trak…")
        try:
            self._page.goto(self._LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(1)  # Let JS hydrate the form

            # Fill credentials
            self._page.fill(self._SEL_USERNAME, username, timeout=10_000)
            self._page.fill(self._SEL_PASSWORD, password, timeout=10_000)
            self._page.click(self._SEL_LOGIN_BTN, timeout=10_000)

            # Wait for redirect to dashboard
            self._page.wait_for_url("**/dashboard/**", timeout=25_000)
            self.logged_in = True
            print("[Executor] Login successful — session active.")
            return True

        except PlaywrightTimeoutError:
            print("[Executor][Error] Login timed out. Check credentials / selectors.")
            self._debug_screenshot("login_timeout")
            return False
        except Exception as exc:
            print(f"[Executor][Error] Login failed: {exc}")
            self._debug_screenshot("login_error")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    def sync_positions(self, known_tickers: list[str] | None = None) -> dict[str, str]:
        """
        Best-effort scrape of current positions from Stock-Trak UI.
        Returns a dict like { "AAPL": "long", ... }.

        This allows SELL decisions to be portfolio-aware even if positions were opened manually
        or in a previous bot session.
        """
        if not self.logged_in:
            print("[Executor][Warning] Not logged in — cannot sync positions.")
            return {}

        known_upper = {t.upper() for t in (known_tickers or []) if t}
        owned: dict[str, str] = {}

        try:
            # Navigate directly to the open positions page (the menu item's href)
            self._page.goto(self._POSITIONS_URL, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(1.5)
            self._dismiss_overlays()

            # Try to find position data — StockTrak may use a table OR a div-based layout
            # Strategy 1: standard <table>
            rows = None
            try:
                self._page.wait_for_selector("table", timeout=8_000)
                rows = self._page.locator("table tbody tr")
                if rows.count() == 0:
                    rows = self._page.locator("table tr")
            except PlaywrightTimeoutError:
                pass  # No table — try div-based rows below

            # Strategy 2: div/li rows (some SPA dashboards use these)
            if rows is None or rows.count() == 0:
                for sel in [
                    "[class*='position-row']",
                    "[class*='holding']",
                    "[class*='portfolio-row']",
                    ".open-positions tr",
                    "#openPositions tr",
                ]:
                    candidate = self._page.locator(sel)
                    if candidate.count() > 0:
                        rows = candidate
                        break

            if rows is None or rows.count() == 0:
                print("[Executor] Synced positions: no position rows found (may be an empty portfolio).")
                return {}

            n = rows.count()
            for i in range(n):
                try:
                    txt = (rows.nth(i).inner_text() or "").upper()
                except Exception:
                    continue
                if not txt.strip():
                    continue

                # If we have a known universe, only accept those tickers.
                if known_upper:
                    for t in known_upper:
                        if t in txt:
                            owned[t] = "long"
                else:
                    tokens = [w.strip() for w in txt.replace("\n", " ").split(" ") if w.strip()]
                    for tok in tokens:
                        if 1 <= len(tok) <= 10 and tok.isascii() and any(c.isalpha() for c in tok):
                            if tok.endswith("-USD") or (tok.isalpha() and tok.isupper() and 1 <= len(tok) <= 5):
                                owned[tok] = "long"

            if owned:
                print(f"[Executor] Synced positions: {len(owned)} open tickers detected.")
            else:
                print("[Executor] Synced positions: none detected (portfolio may be empty).")
            return owned

        except Exception as exc:
            print(f"[Executor][Warning] Position sync failed: {exc}")
            self._debug_screenshot("positions_sync_failed")
            return {}

    # ─────────────────────────────────────────────────────────────────────────
    def execute_trade(
        self,
        ticker: str,
        action: str,
        quantity: int,
        asset_class: str = "stocks",
        notes: str = "",
    ) -> bool:
        """
        Route to the correct trading page based on asset_class, then execute.

        Parameters
        ----------
        ticker       : Symbol, e.g. "PLTR", "BTC-USD", "VFIAX"
        action       : "BUY" or "SELL"
        quantity     : Shares / units to trade
        asset_class  : "stocks"|"etfs"|"crypto"|"bonds"|"mutual"
        notes        : Trading notes text (filled on review page)
        """
        if asset_class == "mutual":
            return self._execute_mutual_fund(ticker, action, quantity, notes)
        if asset_class == "crypto":
            # Crypto uses the dedicated Trade Cryptos UI; route via crypto flow.
            return self._execute_crypto(ticker, action, quantity, notes)
        # stocks, ETFs, bonds all use the equities trading page
        return self._execute_equities(ticker, action, quantity, notes)

    def _execute_mutual_fund(self, ticker: str, action: str,
                             quantity: int, notes: str = "") -> bool:
        """Place a trade on the /trading/mutualfunds page."""
        # TODO: Implement mutual fund trading logic
        print(f"[Executor][Warning] Mutual fund trading not yet implemented for {ticker}.")
        return False

    def _execute_crypto(self, ticker: str, action: str,
                        quantity: int, notes: str = "") -> bool:
        """
        Dedicated crypto execution engine.
        Uses #tbSymbol, #tbQuantity; Buy/Sell via label.button; #btnPreviewOrder, #btnPlaceOrder.
        """
        if not self.logged_in:
            print("[Executor][Warning] Not logged in — skipping crypto trade.")
            return False

        action = action.upper()
        print(f"[Executor] Navigating to Crypto UI for {ticker}…")

        try:
            self._page.goto(self._CRYPTO_URL, wait_until="domcontentloaded", timeout=30_000)
            # Wait until the Trade Cryptos form is fully wired
            self._page.wait_for_selector(self._SEL_SYMBOL, timeout=15_000)
            time.sleep(1.5)
            self._dismiss_overlays()

            # 1. Symbol — for crypto, search base (e.g. BTC) instead of BTC-USD
            base_symbol = ticker.split("-")[0] if "-" in ticker else ticker
            symbol_box = self._page.locator(self._SEL_SYMBOL)
            symbol_box.click(timeout=5_000)
            symbol_box.fill("", timeout=5_000)
            symbol_box.type(base_symbol, delay=80)
            time.sleep(1.5)   # Let ui-autocomplete-input dropdown appear
            # Prefer clicking an autocomplete result if present; else press Enter.
            if not self._select_autocomplete(base_symbol):
                self._page.keyboard.press("Enter")
            time.sleep(2.5)    # Let live quote / form populate

            # 2. Buy/Sell — radio labels: "Buy" / "Sell" (same pattern as equities)
            label_text = "Buy" if action == "BUY" else "Sell"
            self._page.locator("label.button", has_text=label_text).first.click(timeout=8_000)
            time.sleep(0.5)

            # 3. Quantity
            qty_box = self._page.locator(self._SEL_QUANTITY)
            qty_box.click(timeout=5_000)
            qty_box.fill(str(quantity), timeout=5_000)
            time.sleep(0.5)

            # 4. Review Order → Confirm Order (same IDs as equities)
            self._page.locator(self._SEL_PREVIEW).click(timeout=8_000)
            time.sleep(2.0)
            confirm_btn = self._page.locator(self._SEL_CONFIRM)
            confirm_btn.wait_for(state="visible", timeout=12_000)
            confirm_btn.click(timeout=10_000)
            time.sleep(2.5)

            # 5. Verify success
            if self._verify_success(ticker, action, label="CRYPTO"):
                return True
            self._debug_screenshot(f"crypto_timeout_{ticker}")
            print("[Executor][Warning] Could not verify crypto success banner.")
            return False

        except PlaywrightTimeoutError as exc:
            print(f"[Executor][Error] Crypto UI timed out for {ticker}: {exc}")
            self._debug_screenshot(f"crypto_timeout_{ticker}")
            return False
        except Exception as exc:
            print(f"[Executor][Error] Unexpected error on crypto {ticker}: {exc}")
            self._debug_screenshot(f"crypto_error_{ticker}_{action}")
            return False

    def _execute_equities(self, ticker: str, action: str,
                          quantity: int, notes: str = "") -> bool:
        """Place a trade on the /trading/equities page (stocks, ETFs, crypto, bonds)."""
        if not self.logged_in:
            print("[Executor][Warning] Not logged in — skipping trade.")
            return False

        action = action.upper()
        print(f"[Executor] {action} × {quantity} of {ticker} via equities page…")

        try:
            # ── 1. Navigate to trading page ───────────────────────────────────
            self._page.goto(self._TRADING_URL, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(1.5)  # Give JS time to wire up the form

            # Dismiss any cookie banner / tour modal that may block clicks
            self._dismiss_overlays()

            # ── 2. Enter Ticker symbol ────────────────────────────────────────
            symbol_box = self._page.locator(self._SEL_SYMBOL)
            symbol_box.click(timeout=8_000)
            symbol_box.fill("", timeout=5_000)          # clear first
            symbol_box.type(ticker, delay=80)           # type slowly → triggers autocomplete
            time.sleep(1.5)                             # wait for autocomplete list

            # Click the first autocomplete result that matches our ticker
            autocomplete_clicked = self._select_autocomplete(ticker)
            if not autocomplete_clicked:
                # Fallback: press Tab to accept whatever is in the field
                print(f"[Executor][Warning] Autocomplete not found for {ticker}; pressing Tab.")
                symbol_box.press("Tab")
            time.sleep(1.2)   # Let price widget populate

            # ── 3. Select Buy / Sell via label toggle buttons ─────────────────
            # StockTrak uses <label> buttons, not a <select> element
            label_text = "Buy" if action == "BUY" else "Sell"
            action_label = self._page.locator(f"label.button", has_text=label_text).first
            action_label.click(timeout=8_000)
            time.sleep(0.5)

            # ── 4. Enter Quantity ─────────────────────────────────────────────
            qty_box = self._page.locator(self._SEL_QUANTITY)
            qty_box.click(timeout=5_000)
            qty_box.fill(str(quantity), timeout=5_000)
            time.sleep(0.5)

            # ── 5. Fill Trading Notes (required by some classes) ──────────────
            note_text = notes or self._auto_note(ticker, action)
            self._fill_notes(note_text)

            # ── 6. Click "Review Order" (Preview) ────────────────────────────
            self._page.locator(self._SEL_PREVIEW).click(timeout=8_000)
            time.sleep(2.0)  # Review / confirmation page loads

            # ── 7. Fill trading notes on the review page ──────────────────────
            # The notes field (textarea#trade-notes) only exists on the review page
            self._fill_notes(note_text)

            # ── 8. Confirm the order ──────────────────────────────────────────
            confirm_btn = self._page.locator(self._SEL_CONFIRM)
            confirm_btn.wait_for(state="visible", timeout=12_000)
            confirm_btn.click(timeout=10_000)
            time.sleep(2.5)

            # ── 9. Verify success ─────────────────────────────────────────────
            if self._verify_success(ticker, action):
                return True
            self._debug_screenshot(f"no_success_{ticker}")
            print("[Executor][Warning] Could not verify success banner — order may still have gone through.")
            return False  # Conservative: treat as failure so operator can inspect

        except PlaywrightTimeoutError as exc:
            print(
                f"[Executor][Error] Timed out during {action} for {ticker}. "
                f"Detail: {exc}"
            )
            self._debug_screenshot(f"timeout_{ticker}_{action}")
            return False
        except Exception as exc:
            print(f"[Executor][Error] Unexpected error trading {ticker}: {exc}")
            self._debug_screenshot(f"error_{ticker}_{action}")
            return False

    # ── Private helpers ────────────────────────────────────────────────────────
    def _select_autocomplete(self, ticker: str) -> bool:
        """
        Try to click the autocomplete result that best matches ticker.
        Matching priority:
          1. Exact ticker symbol match (whole word, e.g. "GE" must not match "AGEN")
          2. Ticker appears as the first token of the autocomplete item text
          3. First result if nothing better found (last resort)
        Returns True if one was clicked, False otherwise.
        """
        import re
        ticker_up = ticker.upper()
        try:
            # Wait up to 3 s for at least one result to appear
            self._page.wait_for_selector(self._SEL_AUTOCMPLT, timeout=3_000)
            items = self._page.locator(self._SEL_AUTOCMPLT)
            count = items.count()
            if count == 0:
                return False

            # Collect (index, text) pairs
            candidates: list[tuple[int, str]] = []
            for i in range(count):
                try:
                    candidates.append((i, items.nth(i).inner_text().upper().strip()))
                except Exception:
                    pass

            # Priority 1: whole-word exact ticker match using word boundary regex
            pattern = re.compile(r'\b' + re.escape(ticker_up) + r'\b')
            for i, text in candidates:
                if pattern.search(text):
                    items.nth(i).click(timeout=5_000)
                    return True

            # Priority 2: ticker is first whitespace-delimited token
            for i, text in candidates:
                first_token = text.split()[0] if text.split() else ""
                if first_token == ticker_up:
                    items.nth(i).click(timeout=5_000)
                    return True

            # Priority 3: last resort — first result only (log a warning)
            print(f"[Executor][Warning] No exact autocomplete match for {ticker}; "
                  f"picking first result: '{candidates[0][1] if candidates else '?'}'")
            items.first.click(timeout=5_000)
            return True
        except PlaywrightTimeoutError:
            pass
        return False

    def _verify_success(self, ticker: str, action: str, label: str = "") -> bool:
        """
        Check for an order-success indicator using multiple strategies:
        1. Try each CSS selector candidate.
        2. Fall back to a URL check.
        3. Fall back to page-text keyword scan.
        """
        prefix = f"[Executor] ✓ {action}{' ' + label if label else ''} order for {ticker}"
        # Strategy 1: CSS selector candidates
        for sel in self._SEL_SUCCESS_CANDIDATES:
            try:
                self._page.wait_for_selector(sel, timeout=6_000)
                print(f"{prefix} confirmed (selector: {sel}).")
                return True
            except PlaywrightTimeoutError:
                continue
        # Strategy 2: URL check
        current_url = self._page.url
        if any(kw in current_url for kw in ("confirmation", "orderhistory", "order-history")):
            print(f"{prefix} likely confirmed (URL: {current_url}).")
            return True
        # Strategy 3: Page text keyword scan
        try:
            body_text = self._page.locator("body").inner_text(timeout=3_000)
            if any(phrase.lower() in body_text.lower() for phrase in self._SUCCESS_TEXTS):
                print(f"{prefix} confirmed (success text found in page).")
                return True
        except Exception:
            pass
        return False

    def _fill_notes(self, note_text: str) -> None:
        """
        Fill the trading notes textarea (only present on the review page).
        Selector confirmed live: textarea#trade-notes
        Silently does nothing if the field is not present on the current page.
        """
        if not note_text:
            return
        try:
            locator = self._page.locator(self._SEL_NOTES).first
            locator.wait_for(state="visible", timeout=2_500)
            locator.fill(note_text, timeout=4_000)
            print(f"[Executor] Trading notes filled ({len(note_text)} chars).")
        except Exception:
            pass  # Notes field not on this page — that's fine

    @staticmethod
    def _auto_note(ticker: str, action: str) -> str:
        """Generate a minimal trading note when the caller doesn't supply one."""
        return (
            f"{action} signal generated by AI analysis of {ticker}. "
            "Based on RSI, MACD, and volume indicators from the model."
        )

    def _dismiss_overlays(self) -> None:
        """Click through common cookie banners and tour modals."""
        for selector in [
            "button:has-text('Ok')",
            "button:has-text('OK')",
            "button:has-text('Skip')",
            "button:has-text('Skip Tour')",
            "button:has-text('Got it')",
            ".cookie-notice button",
        ]:
            try:
                btn = self._page.locator(selector).first
                btn.wait_for(state="visible", timeout=800)
                btn.click(timeout=1_000)
            except Exception:
                pass

    def _debug_screenshot(self, name: str) -> None:
        """Save a screenshot to help diagnose selector problems."""
        try:
            os.makedirs(self._DEBUG_DIR, exist_ok=True)
            safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(self._DEBUG_DIR, f"{ts}__{safe}.png")
            self._page.screenshot(path=path)
            print(f"[Executor][Debug] Screenshot saved → {path}")
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    def close(self) -> None:
        """Cleanly shut down the browser and Playwright instance."""
        print("[Executor] Closing browser session…")
        for obj, method in [
            (self._context, "close"),
            (self._browser,  "close"),
            (self._pw,       "stop"),
        ]:
            try:
                getattr(obj, method)()
            except Exception:
                pass


# ── Standalone smoke test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    user = os.getenv("STOCKTRAK_USER", "")
    pwd  = os.getenv("STOCKTRAK_PASS", "")

    if not user or not pwd:
        print("Set STOCKTRAK_USER and STOCKTRAK_PASS in your .env file first.")
    else:
        # headless=False lets you watch the bot during debugging
        bot = StockTrakExecutor(headless=True)
        logged_in = bot.login(user, pwd)
        if logged_in:
            print("Session is active — attempting a test BUY of 1 share of AAPL…")
            result = bot.execute_trade(
                "AAPL", "BUY", 1,
                notes="Test trade — verifying bot selectors. Please disregard."
            )
            print(f"Trade result: {'SUCCESS' if result else 'FAILED'}")
        bot.close()
