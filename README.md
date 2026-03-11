## 📈 StockTrakBot — HBCUStockMarketChallenge2026

An AI-powered, terminal-based trading bot for the **Stock-Trak paper trading simulation**. It uses live market data, OpenAI (gpt-4o-mini with Structured Outputs) for analysis, and Playwright browser automation to execute trades — all from a single terminal window with zero UI required.

---

### 🏗️ Architecture — The 3-Pillar System

```
stock_bot/
│
├── main.py            ← 🎯 Orchestrator: runs the continuous trading loop
├── config.py          ← ⚙️  All settings + env variable loader
├── data_fetcher.py    ← 👁️  The Eyes: yfinance + RSI/MACD/SMA via `ta`
├── brain.py           ← 🧠  The Brain: OpenAI API → structured JSON decisions
├── executor.py        ← 🤝  The Hands: Playwright automates Stock-Trak UI
│
├── requirements.txt   ← Python dependencies
├── .env.example       ← Template for credentials (copy → .env)
├── .gitignore         ← Keeps .env out of git
├── pyrightconfig.json ← Type-checker config (fixes VS Code false positives)
└── README.md          ← You are here
```

#### How It Works

```
Every 15 minutes (market hours only):
  For each ticker in watchlist:
    1. Eyes   → fetch live OHLCV + RSI-14, MACD, SMA-20
    2. Brain  → send data to OpenAI → get { action, confidence, reasoning }
    3. Hands  → if confidence ≥ 75 and action is BUY/SELL → execute on Stock-Trak
    4. Log    → print timestamped result to terminal
```

---

### ⚡ Quickstart

#### 1. Clone & Install

```bash
git clone https://github.com/Timeless-Dave/stocktrakbot.git
cd stocktrakbot
pip install -r requirements.txt
python -m playwright install chromium
```

#### 2. Configure Credentials

```bash
cp .env.example .env
```

Edit `.env`:
```env
OPENAI_API_KEY=your_openai_api_key_here
STOCKTRAK_USER=your_stocktrak_username
STOCKTRAK_PASS=your_stocktrak_password
```
 
Get an OpenAI API key from the [OpenAI dashboard](https://platform.openai.com/).

#### 3. Fix Selectors (one-time setup)

Open `executor.py` and update the CSS selectors marked `# ← UPDATE` by:
1. Logging into Stock-Trak manually in your browser
2. Right-click each input field → **Inspect**
3. Copy the `id` or `name` attribute into the matching line in `executor.py`

Set `HEADLESS = False` in `config.py` while doing this so you can watch the bot.

#### 4. Run

```bash
python main.py
```

Press `Ctrl+C` to stop. The browser closes cleanly.

---

### 🔧 Configuration (`config.py`)

| Setting | Default | Description |
|---------|---------|-------------|
| `TARGET_TICKERS` | `["PLTR","MCD","JNJ","PEP","NOW","INTU"]` | Stocks to monitor |
| `TRADE_QUANTITY` | `15` | Shares per order |
| `CONFIDENCE_THRESHOLD` | `75` | Min model confidence to execute |
| `CYCLE_SLEEP_SECONDS` | `900` | Time between full cycles (15 min) |
| `HEADLESS` | `False` | `True` = invisible browser, `False` = visible |

---

### 📊 Sample Terminal Output

```
[09:35:01] [System] Initialising HBCUStockMarketChallenge2026 Bot…
[Executor] Login successful — session active.
[09:35:06] [System] All systems online. Monitoring: ['PLTR', 'MCD', ...]
────────────────────────────────────────────────────────────
[09:35:06] [System] ── Starting new analysis cycle ──
[09:35:07] [Data]  PLTR @ $330.83
[09:35:09] [Brain] 🟡 PLTR | HOLD | Confidence: 52% | RSI neutral, MACD bearish crossover forming.
[09:35:09] [Skip]  PLTR → confidence below threshold or HOLD.
[09:35:14] [Data]  MCD @ $292.10
[09:35:16] [Brain] 🟢 MCD  | BUY  | Confidence: 82% | RSI oversold at 28.5, price below SMA-20.
[09:35:18] [SUCCESS] Executed BUY for 15 shares of MCD.
────────────────────────────────────────────────────────────
[09:35:18] [System] Cycle complete. Next run in 15 minutes.
```

---

### 🛡️ Error Handling

- **Exponential back-off** on data fetch failures (1s → 2s → 4s)
- **Playwright TimeoutError** caught per-trade — never crashes the main loop
- **AI failsafe** — any analysis error returns `HOLD` with 0% confidence
- **Market hours gate** — bot sleeps automatically outside 9:30 AM – 4:00 PM ET
- **Graceful shutdown** — `Ctrl+C` closes the browser cleanly via `finally` block

---

### 📦 Tech Stack

| Layer | Library | Purpose |
|-------|---------|---------|
| Market Data | `yfinance` | Free OHLCV data (NYSE/NASDAQ) |
| Indicators | `ta` | RSI-14, MACD (12/26/9), SMA-20 |
| AI Analysis | `openai` (gpt-4o-mini Structured Outputs) | Structured JSON trade decisions |
| Automation | `playwright` (Chromium) | Headless Stock-Trak browser control |
| Config | `python-dotenv` | Secure credential management |

---

### ⚠️ Disclaimer

This bot is built for the **HBCUStockMarketChallenge2026 paper trading simulation** only.
It does not interact with real financial markets or real money.
