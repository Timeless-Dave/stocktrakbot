"""
executor.py – The Hands
Uses Playwright to automate a persistent headless Chromium session on
Stock-Trak: log in once, then execute BUY/SELL orders on demand.

NOTE: The CSS selectors below are best-effort placeholders.
      Run with headless=False, watch the bot, inspect the live HTML, and
      update any selector marked "# ← UPDATE" before going headless.
"""
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


class StockTrakExecutor:
    """Manages a live browser session to execute trades on Stock-Trak."""

    # ── Selectors ─────────────────────────────────────────────────────────────
    # Login page
    _SEL_USERNAME   = "input[name='UserName']"       # ← UPDATE if needed
    _SEL_PASSWORD   = "input[name='Password']"       # ← UPDATE if needed
    _SEL_LOGIN_BTN  = "button[type='submit']"        # ← UPDATE if needed
    _SEL_POST_LOGIN = "text=Portfolio Value"          # ← Confirms successful login

    # Trading page
    _SEL_SYMBOL     = "#symbolInput"                 # ← UPDATE
    _SEL_AUTOCMPLT  = ".autocomplete-result"         # ← UPDATE (if autocomplete exists)
    _SEL_ACTION     = "#actionDropdown"              # ← UPDATE
    _SEL_QUANTITY   = "input[name='quantity']"       # ← UPDATE
    _SEL_PREVIEW    = "#previewOrderButton"          # ← UPDATE
    _SEL_CONFIRM    = "#confirmOrderButton"          # ← UPDATE
    _SEL_SUCCESS    = "text=Order Executed"          # ← UPDATE

    _TRADING_PATH   = "/trading/equities"

    # ─────────────────────────────────────────────────────────────────────────
    def __init__(self, base_url: str = "https://www.stocktrak.com", headless: bool = True) -> None:
        self.base_url = base_url
        self._pw       = sync_playwright().start()
        self._browser  = self._pw.chromium.launch(headless=headless)
        self._context  = self._browser.new_context(
            viewport={"width": 1280, "height": 800},
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
        """Navigate to login page, authenticate, and verify the dashboard loads."""
        print("[Executor] Logging in to Stock-Trak…")
        try:
            self._page.goto(f"{self.base_url}/login", wait_until="domcontentloaded", timeout=20_000)
            self._page.fill(self._SEL_USERNAME, username, timeout=10_000)
            self._page.fill(self._SEL_PASSWORD, password, timeout=10_000)
            self._page.click(self._SEL_LOGIN_BTN, timeout=10_000)
            # Wait for a landmark element that only appears post-login
            self._page.wait_for_selector(self._SEL_POST_LOGIN, timeout=20_000)
            self.logged_in = True
            print("[Executor] Login successful — session active.")
            return True

        except PlaywrightTimeoutError:
            print("[Executor][Error] Login timed out. Check your credentials or selectors.")
            return False
        except Exception as exc:
            print(f"[Executor][Error] Login failed: {exc}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    def execute_trade(self, ticker: str, action: str, quantity: int) -> bool:
        """
        Navigate to the trading page and submit a BUY or SELL order.

        Parameters
        ----------
        ticker   : Stock symbol (e.g. "PLTR")
        action   : "BUY" or "SELL"
        quantity : Number of shares
        """
        if not self.logged_in:
            print("[Executor][Warning] Not logged in — skipping trade.")
            return False

        print(f"[Executor] Attempting {action} × {quantity} shares of {ticker}…")
        try:
            # ── 1. Navigate ──────────────────────────────────────────────────
            self._page.goto(
                f"{self.base_url}{self._TRADING_PATH}",
                wait_until="domcontentloaded",
                timeout=20_000,
            )

            # ── 2. Enter Ticker ──────────────────────────────────────────────
            self._page.fill(self._SEL_SYMBOL, ticker, timeout=10_000)
            self._page.keyboard.press("Enter")

            # Handle autocomplete popup if it exists
            try:
                self._page.wait_for_selector(self._SEL_AUTOCMPLT, timeout=2_000)
                self._page.click(self._SEL_AUTOCMPLT)
            except PlaywrightTimeoutError:
                pass  # No autocomplete — symbol accepted directly

            self._page.wait_for_timeout(1_000)  # Let price widget settle

            # ── 3. Select Action ─────────────────────────────────────────────
            self._page.select_option(self._SEL_ACTION, action.upper(), timeout=5_000)

            # ── 4. Enter Quantity ────────────────────────────────────────────
            self._page.fill(self._SEL_QUANTITY, str(quantity), timeout=5_000)

            # ── 5. Preview & Confirm ─────────────────────────────────────────
            self._page.click(self._SEL_PREVIEW, timeout=5_000)
            self._page.click(self._SEL_CONFIRM, timeout=10_000)

            # ── 6. Verify Success ────────────────────────────────────────────
            self._page.wait_for_selector(self._SEL_SUCCESS, timeout=10_000)
            print(f"[Executor] ✓ {action} order for {ticker} confirmed.")
            return True

        except PlaywrightTimeoutError:
            print(
                f"[Executor][Error] Timed out during {action} for {ticker}. "
                "The page may have been slow or a selector is incorrect."
            )
            return False
        except Exception as exc:
            print(f"[Executor][Error] Unexpected error trading {ticker}: {exc}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    def close(self) -> None:
        """Cleanly shut down the browser and Playwright instance."""
        print("[Executor] Closing browser session…")
        try:
            self._context.close()
        except Exception:
            pass  # Connection may already be gone if process was interrupted
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass


# ── Standalone test ───────────────────────────────────────────────────────────
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
        bot = StockTrakExecutor(headless=False)
        logged_in = bot.login(user, pwd)
        if logged_in:
            print("Session is active. Bot is ready to trade.")
            # Uncomment to test a live trade:
            # bot.execute_trade("AAPL", "BUY", 5)
        bot.close()
