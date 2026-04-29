from flask import Flask, jsonify
from flask_cors import CORS
import requests
import traceback
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Set ALPHAVANTAGE_KEY in Render environment variables for full valuation data
# Get free key at: https://www.alphavantage.co/support/#api-key
AV_KEY = os.environ.get('ALPHAVANTAGE_KEY', '')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'https://finance.yahoo.com/',
}


def safe_float(val):
    try:
        v = float(val)
        return None if (v != v or abs(v) == float('inf')) else v
    except (TypeError, ValueError):
        return None


def yf_chart(sym, period='1d'):
    """Yahoo Finance v8 chart - works without authentication."""
    url = (
        f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'
        f'?interval=1d&range={period}'
    )
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def parse_chart_history(data):
    result = data.get('chart', {}).get('result', [None])[0]
    if not result:
        return []
    ts = result.get('timestamp', [])
    quotes = result.get('indicators', {}).get('quote', [{}])[0]
    closes = quotes.get('close', [])
    volumes = quotes.get('volume', [])
    chart = []
    for i, t in enumerate(ts):
        c = closes[i] if i < len(closes) else None
        v = volumes[i] if i < len(volumes) else 0
        if c is not None:
            chart.append({
                'date': datetime.utcfromtimestamp(t).strftime('%Y-%m-%d'),
                'close': round(float(c), 2),
                'volume': int(v or 0),
            })
    return chart


def av_overview(sym):
    """Alpha Vantage Company Overview - has PE, PB, EPS, sector, etc."""
    if not AV_KEY:
        return {}
    try:
        url = (
            f'https://www.alphavantage.co/query'
            f'?function=OVERVIEW&symbol={sym}&apikey={AV_KEY}'
        )
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if data and 'Symbol' in data:
                return data
    except Exception:
        pass
    return {}


def av_quote(sym):
    """Alpha Vantage Global Quote - current price, change, volume."""
    if not AV_KEY:
        return {}
    try:
        url = (
            f'https://www.alphavantage.co/query'
            f'?function=GLOBAL_QUOTE&symbol={sym}&apikey={AV_KEY}'
        )
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            q = data.get('Global Quote', {})
            if q and q.get('05. price'):
                return q
    except Exception:
        pass
    return {}


@app.route('/api/stock/<ticker>')
def get_stock(ticker):
    try:
        sym = ticker.upper()

        # 1. Get price from Yahoo Finance v8 (always works)
        try:
            price_data = yf_chart(sym, '1d')
        except Exception as e:
            return jsonify({'error': f'No data for "{sym}": {str(e)}'}), 404

        pr_result = price_data.get('chart', {}).get('result', [None])[0]
        if not pr_result:
            return jsonify({'error': f'No data found for "{sym}".'}), 404

        meta = pr_result.get('meta', {})
        price = safe_float(meta.get('regularMarketPrice'))
        if not price:
            return jsonify({'error': f'No price for "{sym}".'}), 404

        prev_close = safe_float(
            meta.get('chartPreviousClose') or meta.get('previousClose')
        )

        # 2. Get full chart history
        chart = []
        try:
            hist_data = yf_chart(sym, '1y')
            chart = parse_chart_history(hist_data)
        except Exception:
            pass

        # 3. Get valuation data from Alpha Vantage (needs API key)
        overview = av_overview(sym)
        aq = av_quote(sym)

        # Use AV price if available, else Yahoo v8 price
        if aq.get('05. price'):
            price = safe_float(aq.get('05. price')) or price
            prev_close = safe_float(aq.get('08. previous close')) or prev_close

        chg = safe_float(aq.get('09. change'))
        if not chg and price and prev_close:
            chg = round(price - prev_close, 4)
        chg_pct_raw = safe_float(aq.get('10. change percent', '').replace('%', ''))
        if chg_pct_raw is not None:
            chg_pct = round(chg_pct_raw / 100, 6)
        elif chg and prev_close:
            chg_pct = round(chg / prev_close, 6)
        else:
            chg_pct = None

        # Extract valuation fields from Alpha Vantage overview
        def ov(key):
            v = overview.get(key, 'None')
            return safe_float(v) if v not in ('None', '', '-') else None

        result = {
            'symbol': sym,
            'companyName': overview.get('Name') or sym,
            'sector': overview.get('Sector', ''),
            'industry': overview.get('Industry', ''),
            'price': price,
            'previousClose': prev_close,
            'change': chg,
            'changePercent': chg_pct,
            'marketCap': ov('MarketCapitalization'),
            'volume': meta.get('regularMarketVolume'),
            'avgVolume': ov('200DayMovingAverage'),
            'fiftyTwoWeekHigh': safe_float(
                overview.get('52WeekHigh') or meta.get('fiftyTwoWeekHigh')
            ),
            'fiftyTwoWeekLow': safe_float(
                overview.get('52WeekLow') or meta.get('fiftyTwoWeekLow')
            ),
            'peRatio': ov('PERatio'),
            'forwardPE': ov('ForwardPE'),
            'pbRatio': ov('PriceToBookRatio'),
            'psRatio': ov('PriceToSalesRatioTTM'),
            'evEbitda': ov('EVToEBITDA'),
            'debtEquity': None,
            'currentRatio': None,
            'roe': ov('ReturnOnEquityTTM'),
            'revenueGrowth': ov('QuarterlyRevenueGrowthYOY'),
            'earningsGrowth': ov('QuarterlyEarningsGrowthYOY'),
            'grossMargin': ov('GrossProfitTTM'),
            'operatingMargin': ov('OperatingMarginTTM'),
            'dividendYield': ov('DividendYield'),
            'beta': ov('Beta'),
            'eps': ov('EPS'),
            'targetPrice': ov('AnalystTargetPrice'),
            'chart': chart,
            'analystRecommendations': [],
            '_source': 'alphavantage' if overview.get('Symbol') else 'yahoo_v8_only',
        }

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb}), 500


if __name__ == '__main__':
    app.run(debug=True)
