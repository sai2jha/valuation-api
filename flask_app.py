from flask import Flask, jsonify
from flask_cors import CORS
import requests
import traceback
from datetime import datetime

app = Flask(__name__)
CORS(app)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}

API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://finance.yahoo.com/',
    'Origin': 'https://finance.yahoo.com',
}


def safe_float(val):
    try:
        v = float(val)
        return None if (v != v or abs(v) == float('inf')) else v
    except (TypeError, ValueError):
        return None


def g(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
        if isinstance(d, dict) and 'raw' in d:
            return d['raw']
    return d


def get_session_with_crumb():
    session = requests.Session()
    session.headers.update(API_HEADERS)
    crumb = None
    try:
        # First visit yahoo finance to get cookies
        session.get('https://finance.yahoo.com', headers=HEADERS, timeout=10)
        # Then get the crumb
        r = session.get(
            'https://query1.finance.yahoo.com/v1/test/getcrumb',
            timeout=10
        )
        if r.status_code == 200 and r.text and r.text.strip() != 'null':
            crumb = r.text.strip()
    except Exception:
        pass
    return session, crumb


def yf_chart_price(sym, session, crumb):
    url = (
        f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'
        f'?interval=1d&range=1d'
    )
    if crumb:
        url += f'&crumb={crumb}'
    r = session.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    result = data.get('chart', {}).get('result', [None])[0]
    if not result:
        return {}
    meta = result.get('meta', {})
    return {
        'currentPrice': safe_float(meta.get('regularMarketPrice')),
        'previousClose': safe_float(
            meta.get('chartPreviousClose') or meta.get('previousClose')
        ),
        'volume': meta.get('regularMarketVolume'),
        'fiftyTwoWeekHigh': safe_float(meta.get('fiftyTwoWeekHigh')),
        'fiftyTwoWeekLow': safe_float(meta.get('fiftyTwoWeekLow')),
        'symbol': meta.get('symbol', sym),
    }


def yf_summary(sym, session, crumb):
    modules = (
        'summaryDetail,financialData,defaultKeyStatistics,'
        'assetProfile,recommendationTrend,price'
    )
    url = (
        f'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}'
        f'?modules={modules}'
    )
    if crumb:
        url += f'&crumb={crumb}'
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
        result = data.get('quoteSummary', {}).get('result', [None])
        if result and result[0]:
            return result[0]
    except Exception:
        pass
    return {}


def yf_chart_history(sym, session, crumb, period='1y'):
    url = (
        f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'
        f'?interval=1d&range={period}'
    )
    if crumb:
        url += f'&crumb={crumb}'
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
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
    except Exception:
        return []


@app.route('/api/stock/<ticker>')
def get_stock(ticker):
    try:
        sym = ticker.upper()

        session, crumb = get_session_with_crumb()

        # 1. Basic price from v8 chart (most reliable)
        quote = yf_chart_price(sym, session, crumb)
        price = quote.get('currentPrice')

        if not price:
            return jsonify(
                {'error': f'No data found for "{sym}". Check the ticker.'}
            ), 404

        # 2. Rich valuation data from v10 quoteSummary
        summary = yf_summary(sym, session, crumb)
        sd = summary.get('summaryDetail', {})
        fd = summary.get('financialData', {})
        ks = summary.get('defaultKeyStatistics', {})
        ap = summary.get('assetProfile', {})
        pr = summary.get('price', {})
        rt = summary.get('recommendationTrend', {})

        prev_close = safe_float(
            g(sd, 'previousClose') or quote.get('previousClose')
        )
        mkt_cap = g(pr, 'marketCap') or g(sd, 'marketCap')
        chg = round(price - prev_close, 4) if price and prev_close else None
        chg_pct = (
            round(chg / prev_close, 6) if chg and prev_close else None
        )

        recs = []
        for row in rt.get('trend', []):
            recs.append({
                'period': row.get('period', ''),
                'strongBuy': row.get('strongBuy', 0),
                'buy': row.get('buy', 0),
                'hold': row.get('hold', 0),
                'sell': row.get('sell', 0),
                'strongSell': row.get('strongSell', 0),
            })

        chart = yf_chart_history(sym, session, crumb, '1y')

        result = {
            'symbol': sym,
            'companyName': (
                g(pr, 'longName') or g(pr, 'shortName') or sym
            ),
            'sector': ap.get('sector', ''),
            'industry': ap.get('industry', ''),
            'price': price,
            'previousClose': prev_close,
            'change': chg,
            'changePercent': chg_pct,
            'marketCap': safe_float(mkt_cap),
            'volume': g(pr, 'regularMarketVolume') or quote.get('volume'),
            'avgVolume': safe_float(g(sd, 'averageVolume')),
            'fiftyTwoWeekHigh': safe_float(
                g(sd, 'fiftyTwoWeekHigh') or quote.get('fiftyTwoWeekHigh')
            ),
            'fiftyTwoWeekLow': safe_float(
                g(sd, 'fiftyTwoWeekLow') or quote.get('fiftyTwoWeekLow')
            ),
            'peRatio': safe_float(g(sd, 'trailingPE')),
            'forwardPE': safe_float(g(sd, 'forwardPE')),
            'pbRatio': safe_float(g(ks, 'priceToBook')),
            'psRatio': safe_float(g(sd, 'priceToSalesTrailing12Months')),
            'evEbitda': safe_float(g(ks, 'enterpriseToEbitda')),
            'debtEquity': safe_float(g(fd, 'debtToEquity')),
            'currentRatio': safe_float(g(fd, 'currentRatio')),
            'roe': safe_float(g(fd, 'returnOnEquity')),
            'revenueGrowth': safe_float(g(fd, 'revenueGrowth')),
            'earningsGrowth': safe_float(g(fd, 'earningsGrowth')),
            'grossMargin': safe_float(g(fd, 'grossMargins')),
            'operatingMargin': safe_float(g(fd, 'operatingMargins')),
            'dividendYield': safe_float(g(sd, 'dividendYield')),
            'beta': safe_float(g(sd, 'beta')),
            'eps': safe_float(g(ks, 'trailingEps')),
            'targetPrice': safe_float(g(fd, 'targetMeanPrice')),
            'chart': chart,
            'analystRecommendations': recs,
        }

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb}), 500


if __name__ == '__main__':
    app.run(debug=True)
