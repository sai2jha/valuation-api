from flask import Flask, jsonify
from flask_cors import CORS
import requests
import traceback
import json
import re
from datetime import datetime

app = Flask(__name__)
CORS(app)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}


def safe_float(val):
    try:
        v = float(val)
        return None if (v != v or abs(v) == float('inf')) else v
    except (TypeError, ValueError):
        return None


def parse_raw(val):
    if isinstance(val, dict):
        return safe_float(val.get('raw'))
    return safe_float(val)


def fetch_chart_data(sym, session, period='1d'):
    url = (
        f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'
        f'?interval=1d&range={period}'
    )
    r = session.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_yahoo_html(sym, session):
    url = f'https://finance.yahoo.com/quote/{sym}/'
    r = session.get(url, headers=HEADERS, timeout=20)
    # May get redirected to consent page - handle gracefully
    if r.status_code != 200:
        return None
    return r.text


def extract_store_data(html):
    if not html:
        return {}
    # Yahoo Finance embeds data in window.YAHOO_FINANCE_DATA or similar
    patterns = [
        r'root\.App\.main\s*=\s*({.+?});\s*(?:\(function|</script>)',
        r'"QuoteSummaryStore":\s*({.+?}),\s*"QuoteHeaderInfoStore"',
        r'context\.dispatcher\.stores\s*=\s*({.+?});',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                return data
            except Exception:
                pass
    return {}


def fetch_fmp_quote(sym):
    """FMP free tier - 250 req/day, no key needed for basic quote."""
    try:
        url = f'https://financialmodelingprep.com/api/v3/quote/{sym}?apikey=demo'
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list):
                return data[0]
    except Exception:
        pass
    return {}


def fetch_fmp_ratios(sym):
    """FMP TTM ratios."""
    try:
        url = f'https://financialmodelingprep.com/api/v3/ratios-ttm/{sym}?apikey=demo'
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list):
                return data[0]
    except Exception:
        pass
    return {}


def fetch_fmp_profile(sym):
    """FMP company profile."""
    try:
        url = f'https://financialmodelingprep.com/api/v3/profile/{sym}?apikey=demo'
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list):
                return data[0]
    except Exception:
        pass
    return {}


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


@app.route('/api/stock/<ticker>')
def get_stock(ticker):
    try:
        sym = ticker.upper()

        session = requests.Session()
        session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            )
        })

        # 1. Get price data from Yahoo Finance v8 chart (reliable, no auth)
        try:
            price_raw = fetch_chart_data(sym, session, '1d')
        except Exception as e:
            return jsonify({'error': f'No data for "{sym}": {str(e)}'}), 404

        pr_result = price_raw.get('chart', {}).get('result', [None])[0]
        if not pr_result:
            return jsonify({'error': f'No data for "{sym}".'}), 404

        meta = pr_result.get('meta', {})
        price = safe_float(meta.get('regularMarketPrice'))
        if not price:
            return jsonify({'error': f'No price for "{sym}".'}), 404

        prev_close = safe_float(
            meta.get('chartPreviousClose') or meta.get('previousClose')
        )
        chg = round(price - prev_close, 4) if price and prev_close else None
        chg_pct = round(chg / prev_close, 6) if chg and prev_close else None

        # 2. Get valuation ratios from FMP (free tier)
        fmp_q = fetch_fmp_quote(sym)
        fmp_r = fetch_fmp_ratios(sym)
        fmp_p = fetch_fmp_profile(sym)

        # 3. Get 1-year chart history
        chart = []
        try:
            hist_raw = fetch_chart_data(sym, session, '1y')
            chart = parse_chart_history(hist_raw)
        except Exception:
            pass

        result = {
            'symbol': sym,
            'companyName': fmp_p.get('companyName') or fmp_q.get('name') or sym,
            'sector': fmp_p.get('sector', ''),
            'industry': fmp_p.get('industry', ''),
            'price': price,
            'previousClose': prev_close or safe_float(fmp_q.get('previousClose')),
            'change': chg or safe_float(fmp_q.get('change')),
            'changePercent': chg_pct,
            'marketCap': safe_float(fmp_q.get('marketCap') or fmp_p.get('mktCap')),
            'volume': meta.get('regularMarketVolume') or fmp_q.get('volume'),
            'avgVolume': safe_float(fmp_q.get('avgVolume')),
            'fiftyTwoWeekHigh': safe_float(
                meta.get('fiftyTwoWeekHigh') or fmp_q.get('yearHigh')
            ),
            'fiftyTwoWeekLow': safe_float(
                meta.get('fiftyTwoWeekLow') or fmp_q.get('yearLow')
            ),
            # Valuation ratios from FMP
            'peRatio': safe_float(fmp_q.get('pe') or fmp_r.get('peRatioTTM')),
            'forwardPE': safe_float(fmp_r.get('priceEarningsToGrowthRatioTTM')),
            'pbRatio': safe_float(fmp_r.get('priceToBookRatioTTM')),
            'psRatio': safe_float(fmp_r.get('priceToSalesRatioTTM')),
            'evEbitda': safe_float(fmp_r.get('enterpriseValueMultipleTTM')),
            'debtEquity': safe_float(fmp_r.get('debtEquityRatioTTM')),
            'currentRatio': safe_float(fmp_r.get('currentRatioTTM')),
            'roe': safe_float(fmp_r.get('returnOnEquityTTM')),
            'revenueGrowth': safe_float(fmp_r.get('revenueGrowthTTM')),
            'earningsGrowth': safe_float(fmp_r.get('netIncomeGrowthTTM')),
            'grossMargin': safe_float(fmp_r.get('grossProfitMarginTTM')),
            'operatingMargin': safe_float(fmp_r.get('operatingProfitMarginTTM')),
            'dividendYield': safe_float(
                fmp_q.get('dividendYield') or fmp_r.get('dividendYieldTTM')
            ),
            'beta': safe_float(fmp_p.get('beta') or fmp_q.get('priceAvg200')),
            'eps': safe_float(fmp_q.get('eps')),
            'targetPrice': safe_float(fmp_q.get('priceAvg200')),
            'chart': chart,
            'analystRecommendations': [],
        }

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb}), 500


if __name__ == '__main__':
    app.run(debug=True)
