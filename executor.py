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
    _CRYPTO_URL      = "https://app.stocktrak.com/trading/crypto"

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
    _SUCCESS_TEXTS = [
        "Order Confirmation Number",
        "was sent to the market",
        "order has been placed",
        "order confirmation",
        "trade again",          # StockTrak's "Trade Again" button only appears on success
        "view your portfolio",  # Also only present on the success page
    ]
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
        Returns a dict like { "AAPL": "long", "BTC": "long", ... }.

        Tries multiple URL strategies to navigate to the open-positions view,
        since StockTrak sometimes uses tabs on the portfolio/account page.
        """
        if not self.logged_in:
            print("[Executor][Warning] Not logged in — cannot sync positions.")
            return {}

        known_upper = {t.upper() for t in (known_tickers or []) if t}
        # Also index the base symbol for crypto (BTC-USD → BTC)
        known_base = {
            t.split("-")[0].upper() if "-" in t else t.upper()
            for t in (known_tickers or []) if t
        }
        owned: dict[str, str] = {}

        # URLs/strategies to try in order
        candidate_urls = [
            self._POSITIONS_URL,                                   # direct link
            "https://app.stocktrak.com/portfolio/positions",       # alternate path
            "https://app.stocktrak.com/account/portfolio",         # alternate path
        ]

        rows = None
        for url in candidate_urls:
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                time.sleep(1.5)
                self._dismiss_overlays()

                # Some pages have an "Open Positions" tab — try clicking it
                for tab_text in ["Open Positions", "Positions", "Portfolio"]:
                    try:
                        tab = self._page.locator(f"a:has-text('{tab_text}'), "
                                                  f"button:has-text('{tab_text}'), "
                                                  f"li:has-text('{tab_text}')").first
                        tab.wait_for(state="visible", timeout=2_000)
                        tab.click(timeout=3_000)
                        time.sleep(1.0)
                        break
                    except Exception:
                        pass

                # Strategy A: standard <table>
                try:
                    self._page.wait_for_selector("table", timeout=6_000)
                    candidate = self._page.locator("table tbody tr")
                    if candidate.count() == 0:
                        candidate = self._page.locator("table tr")
                    if candidate.count() > 0:
                        rows = candidate
                        break
                except PlaywrightTimeoutError:
                    pass

                # Strategy B: div/li-based rows
                for sel in [
                    "[class*='position-row']",
                    "[class*='holding']",
                    "[class*='portfolio-row']",
                    ".open-positions tr",
                    "#openPositions tr",
                    "[class*='OpenPosition'] tr",
                ]:
                    candidate = self._page.locator(sel)
                    if candidate.count() > 0:
                        rows = candidate
                        break
                if rows is not None and rows.count() > 0:
                    break

            except Exception:
                continue  # try next URL

        if rows is None or rows.count() == 0:
            print("[Executor] Synced positions: no position rows found (may be an empty portfolio).")
            self._debug_screenshot("positions_sync_empty")
            return {}

        # Execute a JS script to pull symbol/qty from the open-positions tables only.
        #
        # The StockTrak open-positions page has multiple <table> elements:
        #   • Open positions tables  → all-caps headers (SYMBOL, QTY, …), no COMPANY col
        #   • Historical/order tables → mixed-case headers with a COMPANY column
        #
        # We only read from tables whose header row is all-caps and has no COMPANY column.
        # Within those tables, we locate the correct SYMBOL and QTY columns by header name.
        script = """
        () => {
            const result = {};
            document.querySelectorAll('table').forEach(table => {
                // Collect header texts
                const ths = Array.from(table.querySelectorAll('th'));
                if (!ths.length) return;
                const headers = ths.map(th => th.innerText.trim().toUpperCase());

                // Skip tables that look like order-history (have a COMPANY column)
                if (headers.includes('COMPANY')) return;
                // Must have both SYMBOL and QTY columns
                const symIdx = headers.indexOf('SYMBOL');
                const qtyIdx = headers.indexOf('QTY');
                if (symIdx === -1 || qtyIdx === -1) return;

                table.querySelectorAll('tbody tr, tr').forEach(tr => {
                    if (tr.querySelector('th')) return; // skip header rows
                    const tds = tr.querySelectorAll('td');
                    if (tds.length <= Math.max(symIdx, qtyIdx)) return;
                    const sym = tds[symIdx].innerText.trim().toUpperCase();
                    if (!sym || sym.length > 10) return;
                    const qtyStr = tds[qtyIdx].innerText.replace(/,/g, '').trim();
                    const qty = parseFloat(qtyStr);
                    if (sym && !isNaN(qty) && qty > 0) {
                        result[sym] = qty;
                    }
                });
            });
            return result;
        }
        """
        try:
            qty_map = self._page.evaluate(script)
        except Exception as e:
            print(f"[Executor] Failed to parse quantities from positions table: {e}")
            qty_map = {}

        # Fallback raw parsing just to be safe if table format changes
        n = rows.count()
        for i in range(n):
            try:
                txt = (rows.nth(i).inner_text() or "").upper()
            except Exception:
                continue
            if not txt.strip():
                continue

            if known_upper:
                for t in known_upper:
                    if t in txt and t not in owned:
                        # Only accept if the JS script found this ticker in the open-positions
                        # tables.  Do NOT fall back to a hardcoded quantity — that caused ghost
                        # positions for tickers mentioned in other tables (e.g. order history).
                        qty = qty_map.get(t)
                        if qty is not None and qty > 0:
                            owned[t] = qty
                # Also match base symbols (e.g. BTC in "BTC BITCOIN")
                for base in known_base:
                    if base in txt:
                        # Map back to full ticker if possible
                        full = next((t for t in known_upper if t.startswith(base)), base)
                        if full not in owned:
                            qty = qty_map.get(base)
                            if qty is not None and qty > 0:
                                owned[full] = qty
            else:
                tokens = [w.strip() for w in txt.replace("\n", " ").split(" ") if w.strip()]
                for tok in tokens:
                    if 1 <= len(tok) <= 10 and tok.isascii() and any(c.isalpha() for c in tok):
                        if tok.endswith("-USD") or (tok.isalpha() and tok.isupper() and 1 <= len(tok) <= 5):
                            if tok not in owned:
                                owned[tok] = qty_map.get(tok, 15.0)

        if owned:
            print(f"[Executor] Synced positions: {len(owned)} open tickers detected.")
        else:
            print("[Executor] Synced positions: none detected (portfolio may be empty).")
        return owned

    # ─────────────────────────────────────────────────────────────────────────
    def execute_trade(
        self,
        ticker: str,
        action: str,
        quantity: float,
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
        # Crypto uses its own dedicated page: /trading/crypto
        if asset_class == "crypto":
            return self._execute_crypto(ticker, action, quantity, notes)
        # stocks, ETFs, bonds all use the equities trading page
        return self._execute_equities(ticker, action, quantity, notes)

    def _execute_mutual_fund(self, ticker: str, action: str,
                             quantity: float, notes: str = "") -> bool:
        """Place a trade on the /trading/mutualfunds page."""
        # TODO: Implement mutual fund trading logic
        print(f"[Executor][Warning] Mutual fund trading not yet implemented for {ticker}.")
        return False

    def _execute_crypto(self, ticker: str, action: str,
                        quantity: float, notes: str = "") -> bool:
        """
        Dedicated crypto execution engine — uses https://app.stocktrak.com/trading/crypto.
        Shares the same form selectors as equities:
        #tbSymbol, #tbQuantity, label.button, #btnPreviewOrder, #btnPlaceOrder.
        The base symbol is used for search (e.g. 'BTC' from 'BTC-USD').
        """
        if not self.logged_in:
            print("[Executor][Warning] Not logged in — skipping crypto trade.")
            return False

        action = action.upper()
        print(f"[Executor] Navigating to Crypto UI for {ticker}…")

        # Error phrases that block order progression — checked at multiple points
        _ERROR_PHRASES = [
            "requires an existing long position",
            "cancel pending orders",
            "you do not have sufficient",
            "available buying power",
            "order cannot be placed",
            "cannot place a sell order",
            "no shares to sell",
            "short selling is not allowed",
            "exceed maximum position size",
            "require you to enter a note",
        ]

        def _page_has_error() -> str | None:
            """
            Scan the page for known error phrases using three strategies:
            1. JS evaluate (most reliable for dynamically injected content)
            2. Playwright body inner_text fallback
            3. Visible alert/error CSS elements
            Returns the matched phrase (or element text) or None.
            """
            try:
                txt = self._page.evaluate("document.body.innerText").lower()
                for phrase in _ERROR_PHRASES:
                    if phrase in txt:
                        return phrase
            except Exception:
                pass
            try:
                txt = self._page.locator("body").inner_text(timeout=2_000).lower()
                for phrase in _ERROR_PHRASES:
                    if phrase in txt:
                        return phrase
            except Exception:
                pass
            try:
                for sel in [
                    ".alert-danger", ".alert-warning", ".alert-error",
                    "[class*='error-message']", "[class*='errorMsg']",
                    "[class*='alert']:not(.alert-info):not(.alert-success)",
                ]:
                    el = self._page.locator(sel).first
                    if el.count() > 0 and el.is_visible(timeout=500):
                        el_txt = el.inner_text(timeout=1_000).lower()
                        for phrase in _ERROR_PHRASES:
                            if phrase in el_txt:
                                return phrase
                        if el_txt.strip():
                            return el_txt.strip()[:200]
            except Exception:
                pass
            return None

        try:
            self._page.goto(self._CRYPTO_URL, wait_until="domcontentloaded", timeout=30_000)
            # Wait until the Trade Cryptos form is fully wired
            self._page.wait_for_selector(self._SEL_SYMBOL, timeout=15_000)
            time.sleep(1.5)
            self._dismiss_overlays()
            self._ensure_trade_form_ready()

            # ── Guard: check for page-level errors immediately after load ─────
            err = _page_has_error()
            if err:
                print(f"[Executor][Warning] Crypto page error for {ticker} before form fill: '{err}'.")
                self._debug_screenshot(f"crypto_blocked_early_{ticker}_{action}")
                return False

            # 1. Symbol — for crypto, search base (e.g. BTC) instead of BTC-USD
            base_symbol = ticker.split("-")[0] if "-" in ticker else ticker
            symbol_box = self._page.locator(self._SEL_SYMBOL)
            symbol_box.scroll_into_view_if_needed()
            symbol_box.click(timeout=5_000, force=True)
            symbol_box.fill("", timeout=5_000)
            
            # Fill the ticker slowly to trigger the JS event
            symbol_box.fill(base_symbol)
            
            # Wait for the autocomplete dropdown to physically appear on the screen
            try:
                self._page.wait_for_selector(".ui-autocomplete", timeout=5_000)
            except Exception:
                pass
            
            # Give it 1 extra second to ensure the list is fully populated
            time.sleep(1.0)
            
            # Press Tab to lock in the top selection explicitly
            self._page.keyboard.press("Tab")
            
            # Wait for the live price quote to load on the screen before moving to the quantity
            time.sleep(2.0)

            # ── Guard: check for errors shown at symbol-load time ─────────────
            err = _page_has_error()
            if err:
                print(f"[Executor][Warning] Crypto trade blocked for {ticker} at symbol load: '{err}'.")
                self._debug_screenshot(f"crypto_blocked_load_{ticker}_{action}")
                return False

            # Ensure the core trade controls are present before selecting action/qty
            self._ensure_trade_controls_ready()

            # 2. Buy/Sell — action selector can be a <select> OR label buttons depending on StockTrak UI version
            self._set_and_verify_trade_action(action)
            time.sleep(0.5)

            # ── Guard: check for errors after action selection ────────────────
            err = _page_has_error()
            if err:
                print(f"[Executor][Warning] Crypto order blocked for {ticker} after action select: '{err}'.")
                self._debug_screenshot(f"crypto_blocked_{ticker}_{action}")
                return False

            # 3. Quantity
            qty_box = self._page.locator(self._SEL_QUANTITY)
            qty_box.scroll_into_view_if_needed()
            qty_box.click(timeout=5_000, force=True)
            qty_box.fill(str(quantity), timeout=5_000)
            time.sleep(0.5)

            # ── 3b. Fill Trading Notes (may appear on order form page) ─────────
            note_text = notes or self._auto_note(ticker, action)
            self._fill_notes(note_text)

            # 4. Review Order → Confirm Order (same IDs as equities)
            # Re-verify side right before preview in case UI resets
            self._set_and_verify_trade_action(action)
            self._page.locator(self._SEL_PREVIEW).click(timeout=8_000, force=True)
            time.sleep(3.0)  # Increased from 2s — review page can be slow

            # ── 4b. Detect "stuck on form" — if the Preview button is still
            #         visible, Review Order was rejected (error banner on form itself).
            try:
                if self._page.locator(self._SEL_PREVIEW).is_visible(timeout=500):
                    err = _page_has_error() or "Review Order did not advance (still on trade form)"
                    print(f"[Executor][Warning] Crypto order stuck on form for {ticker}: '{err}'. Aborting.")
                    self._debug_screenshot(f"crypto_stuck_on_form_{ticker}_{action}")
                    return False
            except Exception:
                pass

            # ── 4c. Guard: check for error banners on the review page ─────────
            err = _page_has_error()
            if err:
                print(f"[Executor][Warning] Crypto order blocked for {ticker} on review page: '{err}'.")
                self._debug_screenshot(f"crypto_blocked_review_{ticker}_{action}")
                return False

            # ── 4d. Ground-truth action check on review page ──────────────────
            try:
                review_action = self._page.evaluate("""
                () => {
                    const tables = document.querySelectorAll("table");
                    for (const t of tables) {
                        const ths = Array.from(t.querySelectorAll("th"));
                        const hdrs = ths.map(h => h.innerText.trim().toLowerCase());
                        const ai = hdrs.indexOf("action");
                        if (ai === -1) continue;
                        const rows = t.querySelectorAll("tbody tr, tr");
                        for (const row of rows) {
                            if (row.querySelector("th")) continue;
                            const tds = row.querySelectorAll("td");
                            if (tds.length <= ai) continue;
                            const v = tds[ai].innerText.trim().toLowerCase();
                            if (v === "buy" || v === "sell") return v.toUpperCase();
                        }
                    }
                    return null;
                }
                """)
                if review_action and review_action != action:
                    print(f"[Executor][Error] Crypto review page shows {review_action} but wanted {action}. "
                          f"Wrong side — cancelling order.")
                    self._debug_screenshot(f"crypto_review_wrong_action_{ticker}_{action}")
                    try:
                        cancel = self._page.locator("button:has-text('Cancel')").first
                        if cancel.count() > 0:
                            cancel.click(force=True)
                    except Exception:
                        pass
                    return False
            except Exception:
                pass

            # ── 4e. Fill trading notes on the review page ──────────────────────
            self._fill_notes(note_text)

            confirm_btn = self._page.locator(self._SEL_CONFIRM)
            confirm_btn.wait_for(state="visible", timeout=12_000)
            confirm_btn.click(timeout=10_000, force=True)

            # 5. Verify success — _verify_success polls internally for confirmation text
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
                          quantity: float, notes: str = "") -> bool:
        """Place a trade on the /trading/equities page (stocks, ETFs, crypto, bonds)."""
        if not self.logged_in:
            print("[Executor][Warning] Not logged in — skipping trade.")
            return False

        action = action.upper()
        # For crypto tickers like BTC-USD, we already strip to base (BTC) upstream
        print(f"[Executor] {action} × {quantity} of {ticker} via equities page…")

        # Error phrases that block order progression — checked at multiple points
        _ERROR_PHRASES = [
            "requires an existing long position",
            "cancel pending orders",
            "you do not have sufficient",
            "available buying power",
            "order cannot be placed",
            "cannot place a sell order",
            "no shares to sell",
            "short selling is not allowed",
            "exceed maximum position size",
            "require you to enter a note",
        ]

        def _page_has_error() -> str | None:
            """
            Scan the page for known error phrases using three strategies:
            1. JS evaluate (most reliable for dynamically injected content)
            2. Playwright body inner_text fallback
            3. Visible alert/error CSS elements
            Returns the matched phrase (or element text) or None.
            """
            # Strategy 1: JS evaluate — catches dynamically injected banners
            try:
                txt = self._page.evaluate("document.body.innerText").lower()
                for phrase in _ERROR_PHRASES:
                    if phrase in txt:
                        return phrase
            except Exception:
                pass
            # Strategy 2: Playwright binding fallback
            try:
                txt = self._page.locator("body").inner_text(timeout=2_000).lower()
                for phrase in _ERROR_PHRASES:
                    if phrase in txt:
                        return phrase
            except Exception:
                pass
            # Strategy 3: Visible alert/error elements (catches aria-hidden or off-flow banners)
            try:
                for sel in [
                    ".alert-danger", ".alert-warning", ".alert-error",
                    "[class*='error-message']", "[class*='errorMsg']",
                    "[class*='alert']:not(.alert-info):not(.alert-success)",
                ]:
                    el = self._page.locator(sel).first
                    if el.count() > 0 and el.is_visible(timeout=500):
                        el_txt = el.inner_text(timeout=1_000).lower()
                        for phrase in _ERROR_PHRASES:
                            if phrase in el_txt:
                                return phrase
                        if el_txt.strip():
                            return el_txt.strip()[:200]
            except Exception:
                pass
            return None

        try:
            # ── 1. Navigate to trading page ───────────────────────────────────
            self._page.goto(self._TRADING_URL, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(1.5)  # Give JS time to wire up the form

            # Dismiss any cookie banner / tour modal that may block clicks
            self._dismiss_overlays()
            self._ensure_trade_form_ready()

            # ── Guard: check for page-level errors immediately after load ─────
            err = _page_has_error()
            if err:
                print(f"[Executor][Warning] Trade page error for {ticker} before form fill: '{err}'.")
                self._debug_screenshot(f"blocked_early_{ticker}_{action}")
                return False

            # ── 2. Enter Ticker symbol ────────────────────────────────────────
            symbol_box = self._page.locator(self._SEL_SYMBOL)
            symbol_box.scroll_into_view_if_needed()
            symbol_box.click(timeout=8_000, force=True)
            symbol_box.fill("", timeout=5_000)          # clear first
            
            # Fill the ticker to trigger the JS event
            symbol_box.fill(ticker)
            
            # Wait for the autocomplete dropdown to physically appear on the screen
            try:
                self._page.wait_for_selector(".ui-autocomplete", timeout=5_000)
            except Exception:
                print(f"[Executor][Warning] Autocomplete dropdown not visible for {ticker}")
            
            # Give it 1 extra second to ensure the list is fully populated with PLTR, not PLOO
            time.sleep(1.0)
            
            # Press Tab to lock in the top selection explicitly
            self._page.keyboard.press("Tab")
            
            # Wait for the live price quote to load on the screen before moving to the quantity
            time.sleep(2.0)

            # ── Guard: check for errors shown at symbol-load time ─────────────
            # e.g. "Sell order requires an existing long position" appears here
            # when the ticker has no open position before we even touch Buy/Sell.
            err = _page_has_error()
            if err:
                print(f"[Executor][Warning] Trade blocked for {ticker} at symbol load: '{err}'.")
                self._debug_screenshot(f"blocked_load_{ticker}_{action}")
                return False

            # Ensure the core trade controls are present before selecting action/qty
            self._ensure_trade_controls_ready()

            # ── 3. Select Buy / Sell (robust to UI changes) ───────────────────
            self._set_and_verify_trade_action(action)
            time.sleep(0.8)

            # ── Guard: check for errors after action selection ────────────────
            # e.g. "requires an existing long position" appears as soon as Sell is clicked
            err = _page_has_error()
            if err:
                print(f"[Executor][Warning] Order blocked for {ticker} after action select: '{err}'.")
                self._debug_screenshot(f"blocked_{ticker}_{action}")
                return False

            # ── 4. Enter Quantity ─────────────────────────────────────────────
            qty_box = self._page.locator(self._SEL_QUANTITY)
            qty_box.scroll_into_view_if_needed()
            qty_box.click(timeout=5_000, force=True)
            qty_box.fill(str(quantity), timeout=5_000)
            time.sleep(0.5)

            # ── 5. Fill Trading Notes (may appear on order form page) ─────────
            note_text = notes or self._auto_note(ticker, action)
            self._fill_notes(note_text)

            # ── 6. Click "Review Order" (Preview) ────────────────────────────
            # Re-verify side right before preview in case UI resets
            self._set_and_verify_trade_action(action)
            self._page.locator(self._SEL_PREVIEW).click(timeout=8_000, force=True)
            time.sleep(3.0)  # Review / confirmation page loads (increased from 2s)

            # ── 6b. Detect "stuck on form" — if the Preview button is still
            #         visible we never advanced to the review page, which means
            #         the order was rejected (error banner on the form itself).
            try:
                if self._page.locator(self._SEL_PREVIEW).is_visible(timeout=500):
                    err = _page_has_error() or "Review Order did not advance (still on trade form)"
                    print(f"[Executor][Warning] Order stuck on form for {ticker}: '{err}'. Aborting.")
                    self._debug_screenshot(f"stuck_on_form_{ticker}_{action}")
                    return False
            except Exception:
                pass

            # ── 6c. Fail fast on error banners on the review page ─────────────
            err = _page_has_error()
            if err:
                print(f"[Executor][Warning] Order blocked for {ticker} on review page: '{err}'.")
                self._debug_screenshot(f"blocked_review_{ticker}_{action}")
                return False

            # ── 6d. Ground-truth action check on review page ──────────────────
            # The review table always shows the actual submitted action (Buy/Sell).
            # This is more reliable than reading the form button state, because
            # the form button may be visually updated without the underlying value.
            try:
                review_action = self._page.evaluate("""
                () => {
                    const tables = document.querySelectorAll("table");
                    for (const t of tables) {
                        const ths = Array.from(t.querySelectorAll("th"));
                        const hdrs = ths.map(h => h.innerText.trim().toLowerCase());
                        const ai = hdrs.indexOf("action");
                        if (ai === -1) continue;
                        const rows = t.querySelectorAll("tbody tr, tr");
                        for (const row of rows) {
                            if (row.querySelector("th")) continue;
                            const tds = row.querySelectorAll("td");
                            if (tds.length <= ai) continue;
                            const v = tds[ai].innerText.trim().toLowerCase();
                            if (v === "buy" || v === "sell") return v.toUpperCase();
                        }
                    }
                    return null;
                }
                """)
                if review_action and review_action != action:
                    print(f"[Executor][Error] Review page shows {review_action} but wanted {action}. "
                          f"Wrong side — cancelling order.")
                    self._debug_screenshot(f"review_wrong_action_{ticker}_{action}")
                    try:
                        cancel = self._page.locator("button:has-text('Cancel')").first
                        if cancel.count() > 0:
                            cancel.click(force=True)
                    except Exception:
                        pass
                    return False
            except Exception:
                pass

            # ── 7. Fill trading notes on the review page ──────────────────────
            self._fill_notes(note_text)

            # ── 8. Confirm the order ──────────────────────────────────────────
            confirm_btn = self._page.locator(self._SEL_CONFIRM)
            confirm_btn.wait_for(state="visible", timeout=12_000)
            confirm_btn.click(timeout=10_000, force=True)

            # ── 9. Verify success ─────────────────────────────────────────────
            # _verify_success polls internally (up to ~15 s) for confirmation text.
            if self._verify_success(ticker, action):
                return True
            self._debug_screenshot(f"no_success_{ticker}")
            print("[Executor][Warning] Could not verify success banner — order may still have gone through.")
            return False

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

    # ─────────────────────────────────────────────────────────────────────────
    def sync_rank(self) -> int | None:
        """
        Scrape the current portfolio ranking from the StockTrak leaderboard.
        Returns the integer rank (1 = first place) or None if it cannot be determined.

        Tries multiple strategies:
          1. Highlighted / special-class table row
          2. CSS-class scan on every row
          3. Username text match across every row
          4. "Your rank" / summary text anywhere on page
        """
        if not self.logged_in:
            return None

        rank_urls = [
            "https://app.stocktrak.com/account/ranking",   # confirmed correct URL
            "https://app.stocktrak.com/leaderboard",
            "https://app.stocktrak.com/account/rankings",   # old/wrong — last resort
        ]

        # We need the username to match our own row
        from config import STOCKTRAK_USER
        own_user = (STOCKTRAK_USER or "").lower()

        for url in rank_urls:
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                time.sleep(1.5)
                self._dismiss_overlays()

                # ── Strategy 1: highlighted row by CSS class ──────────────────
                for sel in ["tr.active", "tr.current-user", "tr.myrow",
                            "tr.highlight", "tr.me", "tr.own"]:
                    try:
                        row = self._page.locator(sel).first
                        row.wait_for(state="visible", timeout=2_000)
                        txt = row.inner_text()
                        rank_str = txt.strip().split()[0].replace("#", "").replace(".", "")
                        if rank_str.isdigit():
                            print(f"[Executor] Rank detected via highlighted row: #{rank_str}")
                            return int(rank_str)
                    except Exception:
                        continue

                # ── Strategy 2: scan all rows — class attr OR username match ──
                rows = self._page.locator("table tbody tr")
                n = rows.count()
                for i in range(n):
                    try:
                        row_el = rows.nth(i)
                        cls = (row_el.get_attribute("class") or "").lower()
                        row_text = row_el.inner_text().lower()
                        cells = row_el.locator("td")
                        if cells.count() < 1:
                            continue
                        rank_cell = cells.nth(0).inner_text().strip().replace("#", "").replace(".", "")
                        if not rank_cell.isdigit():
                            continue
                        # Match by highlighted class
                        if any(k in cls for k in ("active", "current", "highlight", "myrow", "self", "me", "own")):
                            print(f"[Executor] Rank detected via row class: #{rank_cell}")
                            return int(rank_cell)
                        # Match by username text
                        if own_user and own_user in row_text:
                            print(f"[Executor] Rank detected via username match: #{rank_cell}")
                            return int(rank_cell)
                    except Exception:
                        continue

                # ── Strategy 3: free-text search for "rank" / "position" ──────
                try:
                    import re
                    body = self._page.locator("body").inner_text(timeout=3_000)
                    # Look for patterns like "Rank: 2", "#2", "Your rank is 2", "Place: 2"
                    for pattern in [
                        r"(?:your\s+)?rank(?:\s+is)?[:\s#]+([0-9]+)",
                        r"(?:place|position)[:\s#]+([0-9]+)",
                        r"#([0-9]+)\s+(?:of|out\s+of)",
                    ]:
                        m = re.search(pattern, body.lower())
                        if m:
                            rank_val = int(m.group(1))
                            print(f"[Executor] Rank detected via page text pattern: #{rank_val}")
                            return rank_val
                except Exception:
                    pass

                # ── Strategy 4: Fallback CSS selectors for Stock-Trak's UI ────
                try:
                    rank_el = self._page.locator(".portfolio-rank, #rank, .ranking-number, .student-rank").first
                    rank_el.wait_for(state="visible", timeout=2_000)
                    txt = rank_el.inner_text()
                    rank_str = "".join(c for c in txt if c.isdigit())
                    if rank_str:
                        rank_val = int(rank_str)
                        print(f"[Executor] Rank detected via fallback CSS selector: #{rank_val}")
                        return rank_val
                except Exception:
                    pass

            except Exception:
                continue  # try next URL

        # If all strategies fail, quietly default without cluttering debug folder
        print("[Executor][Debug] Could not determine rank from any rankings page.")
        return None

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
        Check for an order-success indicator using multiple strategies.
        Polls for up to ~15 s to handle StockTrak's variable AJAX delay.

        0. wait_for_function (Playwright-native async wait for confirmation text)
        1. JS evaluate body text with retry loop
        2. URL check
        3. CSS selector candidates (last resort)
        """
        prefix = f"[Executor] {action}{' ' + label if label else ''} order confirmed for {ticker}"
        success_js = (
            "document.body.innerText.toLowerCase().includes('was sent to the market')"
            "|| document.body.innerText.toLowerCase().includes('order confirmation number')"
            "|| document.body.innerText.toLowerCase().includes('trade again')"
        )

        # Strategy 0: Playwright wait_for_function — blocks until text appears or 25 s pass.
        # StockTrak's backend can take 15–20 s to process and return a confirmation.
        try:
            self._page.wait_for_function(success_js, timeout=25_000)
        except Exception:
            pass  # timeout — fall through to text scan below

        # Strategy 1: JS evaluate with retry (5 attempts, 1 s apart = up to 5 more s)
        for attempt in range(5):
            try:
                body_text = self._page.evaluate("document.body.innerText")
                if any(phrase.lower() in body_text.lower() for phrase in self._SUCCESS_TEXTS):
                    print(f"{prefix}.")
                    return True
            except Exception:
                pass
            if attempt < 4:
                time.sleep(1.0)

        # Strategy 2: URL check
        try:
            current_url = self._page.url
            if any(kw in current_url for kw in ("confirmation", "orderhistory", "order-history")):
                print(f"{prefix} likely confirmed (URL: {current_url}).")
                return True
        except Exception:
            pass

        # Strategy 3: CSS selector candidates (2 s each)
        for sel in self._SEL_SUCCESS_CANDIDATES:
            try:
                self._page.wait_for_selector(sel, timeout=2_000)
                print(f"{prefix} confirmed (selector: {sel}).")
                return True
            except PlaywrightTimeoutError:
                continue

        # Strategy 4: Final sweep after a short extra wait —
        # catches confirmations that arrive after strategy 1 already ran.
        time.sleep(3.0)
        try:
            body_text = self._page.evaluate("document.body.innerText")
            if any(phrase.lower() in body_text.lower() for phrase in self._SUCCESS_TEXTS):
                print(f"{prefix} confirmed (delayed confirmation detected).")
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
        """Click through common cookie banners and tour modals (incl. Shepherd.js tours)."""
        # Some overlays re-appear after navigation; try a couple of quick passes.
        for _ in range(3):
            clicked_any = False

            # Shepherd.js tour: press Escape first, then try close buttons
            try:
                if self._page.locator("svg.shepherd-modal-overlay-container").count() > 0:
                    self._page.keyboard.press("Escape")
                    time.sleep(0.3)
                    clicked_any = True
            except Exception:
                pass

            for selector in [
                # Shepherd.js tour close/skip buttons
                ".shepherd-cancel-icon",
                "button.shepherd-button",
                "button[data-shepherd-step-id]",
                "button[aria-label*='close' i]",
                "button[aria-label*='skip' i]",
                "button[aria-label*='dismiss' i]",
                # Cookie / consent banners
                "button:has-text('Ok')",
                "button:has-text('OK')",
                "button:has-text('Accept')",
                "button:has-text('I agree')",
                "button:has-text('Skip')",
                "button:has-text('Skip Tour')",
                "button:has-text('Got it')",
                "button:has-text('Next')",
                ".cookie-notice button",
                "div:has-text('This website uses cookies') button",
            ]:
                try:
                    btn = self._page.locator(selector).first
                    if btn.count() == 0:
                        continue
                    btn.wait_for(state="visible", timeout=800)
                    btn.scroll_into_view_if_needed()
                    btn.click(timeout=1_500, force=True)
                    clicked_any = True
                    time.sleep(0.3)
                except Exception:
                    pass

            if not clicked_any:
                break

    def _ensure_trade_form_ready(self) -> None:
        """
        Best-effort: ensure the trade form isn't blocked by cookie banners and
        the key controls exist on the page before interacting.
        """
        try:
            self._page.wait_for_selector(self._SEL_SYMBOL, timeout=10_000)
        except Exception:
            return
        # Cookie banner often sits at the bottom and can block clicks on lower controls
        self._dismiss_overlays()

    def _ensure_trade_controls_ready(self) -> None:
        """
        Ensure BUY/SELL + Quantity + Preview controls are reachable.
        StockTrak sometimes renders these below the fold and a cookie banner can block clicks.
        """
        self._dismiss_overlays()

        # Try a few scroll passes to bring the trade panel controls into view.
        for _ in range(6):
            try:
                qty = self._page.locator(self._SEL_QUANTITY).first
                prev = self._page.locator(self._SEL_PREVIEW).first
                if qty.count() > 0 and prev.count() > 0:
                    # "attached" is enough; we'll scroll right before interaction
                    qty.wait_for(state="attached", timeout=2_000)
                    prev.wait_for(state="attached", timeout=2_000)
                    return
            except Exception:
                pass

            # Scroll down a bit and retry
            try:
                self._page.evaluate("window.scrollBy(0, 650)")
            except Exception:
                pass
            time.sleep(0.4)
            self._dismiss_overlays()

        # If we can't even see the core controls, abort rather than risk wrong-side orders.
        self._debug_screenshot("trade_controls_not_ready")
        raise PlaywrightTimeoutError("Trade controls (qty/preview) not available; aborting to avoid wrong-side trade.")

    def _get_current_trade_action(self) -> str | None:
        """
        Best-effort read of current selected side.
        Returns "BUY", "SELL", or None if cannot be determined reliably.

        Covers StockTrak UI variants:
          - <input type="radio"> pairs (value/id/name/label text)
          - <select> dropdowns (actionDropdown or any side/action select)
          - <label.button> toggles (aria-pressed or CSS active class)
          - Plain <button> or <a> toggles (CSS active class or background colour)
        """
        try:
            which = self._page.evaluate(
                """
                () => {
                    // ── 1. Radio inputs ───────────────────────────────────────────
                    const radios = Array.from(document.querySelectorAll("input[type='radio']"));
                    for (const r of radios) {
                        if (!r.checked) continue;
                        const v = (r.value || r.id || r.name || "").toLowerCase();
                        if (v.includes("sell")) return "SELL";
                        if (v.includes("buy"))  return "BUY";
                        // Check label text associated with this radio
                        const lbl = document.querySelector("label[for='" + r.id + "']")
                                    || r.closest("label");
                        if (lbl) {
                            const lt = lbl.textContent.toLowerCase().trim();
                            if (lt === "sell" || lt.startsWith("sell")) return "SELL";
                            if (lt === "buy"  || lt.startsWith("buy"))  return "BUY";
                        }
                    }

                    // ── 2. Select dropdowns ───────────────────────────────────────
                    const selectors = [
                        "#actionDropdown",
                        "select[id*='action' i]",
                        "select[name*='action' i]",
                        "select[id*='side' i]",
                        "select[name*='side' i]",
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (!el || el.tagName.toLowerCase() !== "select") continue;
                        const txt = (el.options[el.selectedIndex]?.textContent || "").toLowerCase();
                        if (txt.includes("sell")) return "SELL";
                        if (txt.includes("buy"))  return "BUY";
                    }

                    // ── 3. Toggle labels / buttons (aria-pressed, active class, or
                    //        computed background colour for plain <button> UI) ─────
                    const ACTIVE_KEYWORDS = ["active", "selected", "is-active", "checked", "dark", "primary", "success"];
                    const candidates = Array.from(document.querySelectorAll(
                        "label.button, label.btn, button, a.btn, a.button"
                    ));
                    for (const el of candidates) {
                        const txt = el.textContent.trim().toLowerCase();
                        if (txt !== "buy" && txt !== "sell") continue;

                        const aria = (el.getAttribute("aria-pressed") || "").toLowerCase();
                        const cls  = (el.getAttribute("class")        || "").toLowerCase();
                        const isActive = aria === "true"
                            || ACTIVE_KEYWORDS.some(k => cls.includes(k));

                        if (isActive) {
                            if (txt === "sell") return "SELL";
                            if (txt === "buy")  return "BUY";
                        }
                    }

                    // ── 4. Background-colour heuristic (StockTrak uses a dark pill
                    //        for the selected side, white/light for the other) ──────
                    const btnCandidates = Array.from(document.querySelectorAll(
                        "label.button, label.btn, button, a.btn, a.button"
                    )).filter(el => {
                        const t = el.textContent.trim().toLowerCase();
                        return t === "buy" || t === "sell";
                    });
                    if (btnCandidates.length >= 2) {
                        for (const el of btnCandidates) {
                            const bg = window.getComputedStyle(el).backgroundColor;
                            // Anything other than transparent/white is the active side
                            const isLight = !bg
                                || bg === "rgba(0, 0, 0, 0)"
                                || bg === "transparent"
                                || bg === "rgb(255, 255, 255)"
                                || bg.startsWith("rgba(255, 255, 255");
                            if (!isLight) {
                                const t = el.textContent.trim().toLowerCase();
                                if (t === "sell") return "SELL";
                                if (t === "buy")  return "BUY";
                            }
                        }
                    }

                    return null;
                }
                """
            )
            if which in ("BUY", "SELL"):
                return which
        except Exception:
            pass

        return None

    def _select_trade_action(self, action: str) -> None:
        """
        Robustly select BUY/SELL on StockTrak:
        1. <select id="actionDropdown"> or any action/side select
        2. <label.button> or <label.btn> toggle
        3. Plain <button> or <a> with matching text  (StockTrak current UI)
        4. JS direct radio activation (last resort)
        """
        action = (action or "").upper()
        label_text = "Buy" if action == "BUY" else "Sell"
        action_lower = action.lower()

        # 1) Select dropdowns
        for sel in [
            "#actionDropdown",
            "select[id*='action' i]",
            "select[name*='action' i]",
            "select[id*='side' i]",
            "select[name*='side' i]",
        ]:
            try:
                dd = self._page.locator(sel).first
                if dd.count() == 0:
                    continue
                dd.wait_for(state="visible", timeout=3_000)
                dd.scroll_into_view_if_needed()
                dd.select_option(label=label_text, timeout=4_000)
                return
            except Exception:
                continue

        # 2) Label/button toggles (label.button, label.btn)
        for sel in [f"label.button:has-text('{label_text}')",
                    f"label.btn:has-text('{label_text}')"]:
            try:
                btn = self._page.locator(sel).first
                if btn.count() == 0:
                    continue
                btn.wait_for(state="visible", timeout=4_000)
                btn.scroll_into_view_if_needed()
                btn.click(timeout=5_000, force=True)
                return
            except Exception:
                continue

        # 3) Plain <button> or <a> elements with exact text match (StockTrak current UI)
        _clicked_btn = False
        for sel in [
            f"button:has-text('{label_text}')",
            f"a.btn:has-text('{label_text}')",
            f"a:has-text('{label_text}')",
        ]:
            try:
                btn = self._page.locator(sel).first
                if btn.count() == 0:
                    continue
                btn.wait_for(state="visible", timeout=4_000)
                btn.scroll_into_view_if_needed()
                btn.click(timeout=5_000, force=True)
                _clicked_btn = True
                break
            except Exception:
                continue

        # 4) JS radio/input activation — ALWAYS run after button clicks to ensure
        #    the underlying form value (not just visual state) is updated.
        #    When an overlay is present, force=True dispatches the click event but may
        #    not fire the full JS handler chain that updates hidden inputs.
        try:
            activated = self._page.evaluate(
                f"""
                () => {{
                    // Try radio inputs first
                    const radios = Array.from(document.querySelectorAll("input[type='radio']"));
                    for (const r of radios) {{
                        const v = (r.value || r.id || r.name || "").toLowerCase();
                        const lbl = document.querySelector("label[for='" + r.id + "']")
                                    || r.closest("label");
                        const lt = (lbl ? lbl.textContent : "").toLowerCase().trim();
                        if (v.includes("{action_lower}") || lt.startsWith("{action_lower}")) {{
                            r.checked = true;
                            r.dispatchEvent(new Event("change", {{bubbles: true}}));
                            r.dispatchEvent(new Event("input",  {{bubbles: true}}));
                            r.dispatchEvent(new Event("click",  {{bubbles: true}}));
                            if (lbl) lbl.click();
                            return true;
                        }}
                    }}
                    // Try select elements
                    const selects = Array.from(document.querySelectorAll("select"));
                    for (const s of selects) {{
                        for (const opt of s.options) {{
                            if (opt.text.toLowerCase().includes("{action_lower}") ||
                                opt.value.toLowerCase().includes("{action_lower}")) {{
                                s.value = opt.value;
                                s.dispatchEvent(new Event("change", {{bubbles: true}}));
                                return true;
                            }}
                        }}
                    }}
                    return false;
                }}
                """
            )
            if activated or _clicked_btn:
                return
        except Exception:
            if _clicked_btn:
                return

        # No reliable action control found — abort to avoid wrong-side orders.
        self._debug_screenshot(f"cannot_select_action_{action}")
        raise PlaywrightTimeoutError(f"Could not locate a reliable BUY/SELL control to select '{label_text}'.")

    def _set_and_verify_trade_action(self, action: str) -> None:
        """
        Set BUY/SELL and verify the page reflects the requested action.
        If verification fails, retry once and then abort (do NOT place an order).
        """
        want = (action or "").upper()
        self._dismiss_overlays()
        self._select_trade_action(want)
        time.sleep(0.3)
        self._dismiss_overlays()

        got = self._get_current_trade_action()
        if got == want:
            return

        # Retry once with a fresh scroll pass
        try:
            self._page.evaluate("window.scrollBy(0, 450)")
        except Exception:
            pass
        time.sleep(0.3)
        self._dismiss_overlays()
        self._select_trade_action(want)
        time.sleep(0.3)
        got2 = self._get_current_trade_action()
        if got2 == want:
            return

        self._debug_screenshot(f"action_mismatch_want_{want}_got_{got2 or got or 'unknown'}")
        raise PlaywrightTimeoutError(f"Trade side mismatch: wanted {want}, but page shows {got2 or got or 'unknown'}. Aborting.")

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
