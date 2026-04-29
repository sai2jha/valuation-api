from flask import Flask, jsonify
from flask_cors import CORS
import yfinance as yf
import traceback
import time

app = Flask(__name__)
CORS(app)

def get_info_with_retry(ticker, max_retries=3):
    """Fetch ticker info with exponential backoff on rate limit."""
    t = yf.Ticker(ticker.upper())
    for attempt in range(max_retries):
        try:
            info = t.info
            if info and (info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')):
                return t, info
            # If info is empty, wait and retry
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
        except Exception as e:
            err_str = str(e).lower()
            if 'rate' in err_str or '429' in err_str or 'too many' in err_str:
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)
                    time.sleep(wait)
                    continue
            raise
    return t, {}

@app.route('/api/stock/<ticker>')
def get_stock(ticker):
    try:
        t, info = get_info_with_retry(ticker)

        if not info or (info.get('currentPrice') is None and info.get('regularMarketPrice') is None and info.get('previousClose') is None):
            return jsonify({'error': f'No data found for {ticker.upper()}'}), 404

        # Price data
        price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
        prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
        change = round(price - prev_close, 4) if price and prev_close else None
        change_pct = round(change / prev_close, 6) if change and prev_close else None

        # Chart history
        hist = t.history(period='1y')
        chart = [{'date': d.strftime('%Y-%m-%d'), 'close': round(float(r['Close']), 2), 'volume': int(r['Volume'])} for d, r in hist.iterrows()]

        # Financials
        income = {}
        try:
            fin = t.financials
            if fin is not None and not fin.empty:
                for col in fin.columns[:4]:
                    yr = str(col.year)
                    income[yr] = {
                        'revenue': int(fin.loc['Total Revenue', col]) if 'Total Revenue' in fin.index else None,
                        'grossProfit': int(fin.loc['Gross Profit', col]) if 'Gross Profit' in fin.index else None,
                        'operatingIncome': int(fin.loc['Operating Income', col]) if 'Operating Income' in fin.index else None,
                        'netIncome': int(fin.loc['Net Income', col]) if 'Net Income' in fin.index else None,
                    }
        except Exception:
            pass

        # Analyst recommendations
        recs = []
        try:
            rec_df = t.recommendations
            if rec_df is not None and not rec_df.empty:
                if 'period' in rec_df.columns:
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

        def safe_float(val):
            try:
                v = float(val)
                return None if (v != v or v == float('inf') or v == float('-inf')) else v
            except (TypeError, ValueError):
                return None

        result = {
            'symbol': info.get('symbol', ticker.upper()),
            'companyName': info.get('longName') or info.get('shortName', ''),
            'sector': info.get('sector', ''),
            'industry': info.get('industry', ''),
            'price': safe_float(price),
            'previousClose': safe_float(prev_close),
            'change': safe_float(change),
            'changePercent': safe_float(change_pct),
            'marketCap': info.get('marketCap'),
            'volume': info.get('volume') or info.get('regularMarketVolume'),
            'avgVolume': info.get('averageVolume'),
            'fiftyTwoWeekHigh': safe_float(info.get('fiftyTwoWeekHigh')),
            'fiftyTwoWeekLow': safe_float(info.get('fiftyTwoWeekLow')),
            'peRatio': safe_float(info.get('trailingPE')),
            'forwardPE': safe_float(info.get('forwardPE')),
            'pbRatio': safe_float(info.get('priceToBook')),
            'psRatio': safe_float(info.get('priceToSalesTrailing12Months')),
            'evEbitda': safe_float(info.get('enterpriseToEbitda')),
            'debtEquity': safe_float(info.get('debtToEquity')),
            'currentRatio': safe_float(info.get('currentRatio')),
            'roe': safe_float(info.get('returnOnEquity')),
            'revenueGrowth': safe_float(info.get('revenueGrowth')),
            'earningsGrowth': safe_float(info.get('earningsGrowth')),
            'grossMargin': safe_float(info.get('grossMargins')),
            'operatingMargin': safe_float(info.get('operatingMargins')),
            'dividendYield': safe_float(info.get('dividendYield')),
            'beta': safe_float(info.get('beta')),
            'eps': safe_float(info.get('trailingEps')),
            'targetPrice': safe_float(info.get('targetMeanPrice')),
            'chart': chart,
            'analystRecommendations': recs,
            'financials': income,
        }
        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        err = str(e)
        if 'rate' in err.lower() or '429' in err or 'too many' in err.lower():
            return jsonify({'error': 'Too Many Requests. Rate limited. Try after a while.', 'trace': tb}), 429
        return jsonify({'error': err, 'trace': tb}), 500

if __name__ == '__main__':
    app.run(debug=True)
