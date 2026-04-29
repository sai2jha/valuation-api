from flask import Flask, jsonify
from flask_cors import CORS
import yfinance as yf
import traceback
import time
import random

app = Flask(__name__)
CORS(app)

def safe_float(val):
    try:
        v = float(val)
        return None if (v != v or v == float('inf') or v == float('-inf')) else v
    except (TypeError, ValueError):
        return None

def get_stock_data(ticker):
    sym = ticker.upper()
    t = yf.Ticker(sym)

    # Try fast_info first (lighter endpoint, less rate-limited)
    info = {}
    try:
        fi = t.fast_info
        info = {
            'currentPrice': safe_float(getattr(fi, 'last_price', None)),
            'previousClose': safe_float(getattr(fi, 'previous_close', None)),
            'marketCap': getattr(fi, 'market_cap', None),
            'fiftyTwoWeekHigh': safe_float(getattr(fi, 'fifty_two_week_high', None)),
            'fiftyTwoWeekLow': safe_float(getattr(fi, 'fifty_two_week_low', None)),
            'volume': getattr(fi, 'last_volume', None),
        }
    except Exception:
        pass

    # Also try full info for richer data
    full_info = {}
    try:
        full_info = t.info or {}
    except Exception:
        pass

    # Merge: full_info wins where available
    merged = {**info, **{k: v for k, v in full_info.items() if v is not None}}

    price = safe_float(merged.get('currentPrice') or merged.get('regularMarketPrice'))
    prev_close = safe_float(merged.get('previousClose') or merged.get('regularMarketPreviousClose'))

    if not price and not prev_close:
        # Last resort: use download
        try:
            dl = yf.download(sym, period='2d', progress=False, auto_adjust=True)
            if not dl.empty:
                price = safe_float(float(dl['Close'].iloc[-1]))
                if len(dl) >= 2:
                    prev_close = safe_float(float(dl['Close'].iloc[-2]))
                merged['currentPrice'] = price
                merged['previousClose'] = prev_close
        except Exception:
            pass

    if not price:
        raise ValueError(f'No price data found for {sym}')

    change = round(price - prev_close, 4) if price and prev_close else None
    change_pct = round(change / prev_close, 6) if change and prev_close else None

    # Chart: 1 year history
    chart = []
    try:
        hist = t.history(period='1y')
        chart = [
            {'date': d.strftime('%Y-%m-%d'), 'close': round(float(r['Close']), 2), 'volume': int(r['Volume'])}
            for d, r in hist.iterrows()
        ]
    except Exception:
        pass

    # Analyst recommendations
    recs = []
    try:
        rec_df = t.recommendations
        if rec_df is not None and not rec_df.empty and 'period' in rec_df.columns:
            for _, row in rec_df.iterrows():
                recs.append({
                    'period': str(row.get('period', '')),
                    'strongBuy': int(row.get('strongBuy', 0)),
                    'buy': int(row.get('buy', 0)),
                    'hold': int(row.get('hold', 0)),
                    'sell': int(row.get('sell', 0)),
                    'strongSell': int(row.get('strongSell', 0)),
                })
    except Exception:
        pass

    return {
        'symbol': sym,
        'companyName': merged.get('longName') or merged.get('shortName', sym),
        'sector': merged.get('sector', ''),
        'industry': merged.get('industry', ''),
        'price': price,
        'previousClose': prev_close,
        'change': change,
        'changePercent': change_pct,
        'marketCap': merged.get('marketCap'),
        'volume': merged.get('volume') or merged.get('regularMarketVolume'),
        'avgVolume': merged.get('averageVolume'),
        'fiftyTwoWeekHigh': safe_float(merged.get('fiftyTwoWeekHigh')),
        'fiftyTwoWeekLow': safe_float(merged.get('fiftyTwoWeekLow')),
        'peRatio': safe_float(merged.get('trailingPE')),
        'forwardPE': safe_float(merged.get('forwardPE')),
        'pbRatio': safe_float(merged.get('priceToBook')),
        'psRatio': safe_float(merged.get('priceToSalesTrailing12Months')),
        'evEbitda': safe_float(merged.get('enterpriseToEbitda')),
        'debtEquity': safe_float(merged.get('debtToEquity')),
        'currentRatio': safe_float(merged.get('currentRatio')),
        'roe': safe_float(merged.get('returnOnEquity')),
        'revenueGrowth': safe_float(merged.get('revenueGrowth')),
        'earningsGrowth': safe_float(merged.get('earningsGrowth')),
        'grossMargin': safe_float(merged.get('grossMargins')),
        'operatingMargin': safe_float(merged.get('operatingMargins')),
        'dividendYield': safe_float(merged.get('dividendYield')),
        'beta': safe_float(merged.get('beta')),
        'eps': safe_float(merged.get('trailingEps')),
        'targetPrice': safe_float(merged.get('targetMeanPrice')),
        'chart': chart,
        'analystRecommendations': recs,
    }

@app.route('/api/stock/<ticker>')
def get_stock(ticker):
    try:
        result = get_stock_data(ticker)
        return jsonify(result)
    except Exception as e:
        tb = traceback.format_exc()
        err = str(e)
        if 'rate' in err.lower() or '429' in err or 'too many' in err.lower():
            return jsonify({'error': 'Too Many Requests. Rate limited. Try after a while.', 'trace': tb}), 429
        return jsonify({'error': err, 'trace': tb}), 500

if __name__ == '__main__':
    app.run(debug=True)
