# Portfolio Dashboard

Dynamic portfolio tracker. Add positions (ticker + purchase price + quantity),
monitor live P&L across multiple broker accounts, with optional AI-powered
red-flag/SWOT commentary per stock.

## Cost: ₹0 to run

- Price data: free via Yahoo Finance (yfinance) — no broker login, no API key.
- Hosting: runs locally on your machine.
- Optional: DeepSeek API for per-stock commentary — only costs money if you
  click "Analyze," and it's a fraction of a rupee per call.

## Setup (Windows)

```powershell
cd "G:\E Ddrive\PortfolioDashboard"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# One-time: load your existing holdings
python seed_holdings.py

# Optional: enable DeepSeek analysis
copy .env.template .env
# then edit .env and paste your DEEPSEEK_API_KEY

# Run the dashboard
streamlit run app.py
```

This opens in your browser automatically (usually `http://localhost:8501`).

## Daily use

- **Add a new position**: use the sidebar form (ticker, company name, account
  tag, quantity, purchase price).
- **Consolidated view** (default): sums your holdings across all accounts —
  e.g. if you hold Reliance in two different brokers, this shows the combined
  position and a quantity-weighted average purchase price.
- **By Account view**: shows each broker's lots separately, with edit/delete
  buttons per lot.
- **Refresh Prices**: prices are cached for ~60 seconds to avoid hammering
  Yahoo Finance on every click; use this button to force an immediate refetch.
- **Analyze (DeepSeek)**: optional, only runs when you click it, gives a short
  strengths/risks/valuation note. Not a buy/sell recommendation.

## Data & privacy

Everything lives in a local SQLite file (`portfolio.db`) on your machine.
Nothing is uploaded anywhere except:
- Ticker symbols sent to Yahoo Finance for price lookups (no personal data)
- If you click "Analyze": the ticker + company name sent to DeepSeek (nothing
  about your position size, account, or purchase price)

`portfolio.db`, `.env`, and any `.xlsx`/`.csv` broker exports are gitignored —
**never commit these to a public or shared repo.** They contain real account
data and (in the case of broker exports) your PAN and account details.

## Adding tickers

NSE tickers use the `.NS` suffix (e.g. `RELIANCE.NS`, `TCS.NS`). For BSE-only
listings, use `.BO`. If a ticker doesn't resolve (shows blank LTP), double
check the exact symbol on nseindia.com or a broker app — company names don't
always map obviously to ticker symbols, especially for recent IPOs or renamed
companies (e.g. "SML Mahindra" trades as `SMLMAH.NS`, "LG Electronics India"
trades as `LGEINDIA.NS`).

## Known limitations (v1)

- Live prices depend on Yahoo Finance's data for NSE — can lag real-time by
  a few minutes and occasionally misses very recent IPOs for a few days after
  listing.
- No historical performance charting yet (just current snapshot P&L).
- Account tags for two of your three brokers were seeded as placeholders
  (`ACCOUNT_2`, `ACCOUNT_3`) since which file mapped to Yes Bank vs Dhan
  wasn't confirmed — rename them via Edit in the "By Account" view whenever
  convenient; doesn't affect any P&L math.
