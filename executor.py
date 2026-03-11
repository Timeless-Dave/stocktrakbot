"""
executor.py – The Hands
Uses Playwright to automate a persistent headless Chromium session on
Stock-Trak: log in once, then execute BUY/SELL orders on demand.

Selectors verified live against https://app.stocktrak.com on 2026-03-10.
"""
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

    # Trading page  (https://app.stocktrak.com/trading/equities)
    _TRADING_URL     = "https://app.stocktrak.com/trading/equities"

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
    # Success banner after order is placed
    _SEL_SUCCESS     = ".order-confirmation, text=Order Confirmation Number, text=was sent to the market"

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
        else:
            # stocks, ETFs, crypto, bonds all use the equities trading page
            return self._execute_equities(ticker, action, quantity, notes)

    def _execute_mutual_fund(self, ticker: str, action: str,
                             quantity: int, notes: str = "") -> bool:
        """Place a trade on the /trading/mutualfunds page."""
        # TODO: Implement mutual fund trading logic
        print(f"[Executor][Warning] Mutual fund trading not yet implemented for {ticker}.")
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
            try:
                self._page.wait_for_selector(self._SEL_SUCCESS, timeout=10_000)
                print(f"[Executor] ✓ {action} order for {ticker} confirmed.")
                return True
            except PlaywrightTimeoutError:
                # Page may still have navigated successfully; check URL
                current_url = self._page.url
                if "confirmation" in current_url or "orderhistory" in current_url:
                    print(f"[Executor] ✓ {action} order likely confirmed (URL: {current_url}).")
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
        Try to click the first autocomplete result that matches ticker.
        Returns True if one was clicked, False otherwise.
        """
        try:
            # Wait up to 3 s for at least one result to appear
            self._page.wait_for_selector(self._SEL_AUTOCMPLT, timeout=3_000)
            # Prefer an exact ticker match first
            items = self._page.locator(self._SEL_AUTOCMPLT)
            count = items.count()
            for i in range(count):
                item = items.nth(i)
                text = item.inner_text().upper()
                if ticker.upper() in text:
                    item.click(timeout=5_000)
                    return True
            # No exact match — click the very first result
            if count > 0:
                items.first.click(timeout=5_000)
                return True
        except PlaywrightTimeoutError:
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
            path = f"debug_{name}.png"
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
