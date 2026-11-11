# Fear-RSI v1.4

Upbit BTC/ETH Streamlit quantitative research dashboard.

## New in v1.4
- D1/D2/D3 strategy comparison preserved.
- Per-trade maximum favorable excursion (MFE) and maximum adverse excursion (MAE).
- Exit comparison: fixed profit target, trailing stop, momentum exit.
- Download detailed trades as CSV; 70/30 chronological train/test display.
- Existing A/B/C reference comparison retained.

## Deploy
Upload `app.py`, `requirements.txt`, and `README.md` to the existing GitHub repository root. Streamlit Cloud should redeploy the existing app. No API keys or automatic orders are used.

## Caveats
OHLC-based backtests cannot reconstruct the intrabar order of events. Trailing stops use only prior bars' highs, and gaps are conservatively filled at worse opening prices. MFE/MAE are approximate. Strategies are hypotheses, not proven profitable. The holdout period must not be reused repeatedly for tuning. Partial take-profit was deliberately excluded pending proper split-position accounting.
