from data_fetcher import MarketDataFetcher
f = MarketDataFetcher()
for ticker, cls in [("SPY","etfs"),("BTC-USD","crypto")]:
    d = f.fetch_full_data(ticker, cls)
    if d:
        print(f"{ticker} ({cls}): price={d['current_price']}, rsi={d['rsi_14']}, sma50={d['sma_50']}, bb_pct={d['bb_pct']}")
        if d.get("pe_ratio"):
            print(f"  pe={d['pe_ratio']}, beta={d['beta']}")
    else:
        print(f"{ticker}: FAILED")
