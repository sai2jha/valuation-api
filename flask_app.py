from flask import Flask, jsonify
from flask_cors import CORS
import requests
import traceback
import json
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

JSON_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://finance.yahoo.com/',
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


def yf_chart(sym, session, period='1d'):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range={period}'
    r = session.get(url, headers=JSON_HEADERS, timeout=20)
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


def yf_summary_via_html(sym, session):
    """Extract financial data from Yahoo Finance HTML page's embedded JSON."""
    url = f'https://finance.yahoo.com/quote/{sym}/'
    try:
        r = session.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        if r.status_code != 200:
            return {}
        html = r.text
        # Look for the embedded JSON data in the page
        # Yahoo Finance puts data in a script tag as JSON
        import re
        patterns = [
            r'"QuoteSummaryStore":\s*({[^<]+}),\s*"[A-Z]',
            r'root\.App\.main\s*=\s*({.+?});\s*}\(this\)',
            r'"context":\s*{[^}]*"dispatcher":\s*{"stores":\s*({.+?}),"actions"',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1))
                    return data
                except Exception:
                    pass
    except Exception:
        pass
    return {}


def try_quotesummary_no_crumb(sym, session):
    """Try Yahoo Finance v10 quoteSummary via query2 host (sometimes works)."""
    modules = (
        'summaryDetail,financialData,defaultKeyStatistics,'
        'assetProfile,recommendationTrend,price'
    )
    for host in ['query2', 'query1']:
        url = (
            f'https://{host}.finance.yahoo.com/v10/finance/quoteSummary/{sym}'
            f'?modules={modules}&formatted=false&lang=en-US&region=US'
        )
        try:
            r = session.get(url, headers=JSON_HEADERS, timeout=15)
            if r.status_code == 200:
                data = r.json()
                result = data.get('quoteSummary', {}).get('result', [None])
                if result and result[0]:
                    return result[0], None
        except Exception as e:
            continue
    return {}, '401 on both hosts'


@app.route('/api/stock/<ticker>')
def get_stock(ticker):
    try:
        sym = ticker.upper()
        session = requests.Session()

        # 1. Basic price from v8 chart (reliable)
        try:
            price_data = yf_chart(sym, session, '1d')
        except Exception as e:
            return jsonify({'error': f'No data for "{sym}": {str(e)}'}), 404

        pr_result = price_data.get('chart', {}).get('result', [None])[0]
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

        # 2. Try v10 quoteSummary (may work depending on Render IP)
        summary, v10_err = try_quotesummary_no_crumb(sym, session)
        sd = summary.get('summaryDetail', {})
        fd = summary.get('financialData', {})
        ks = summary.get('defaultKeyStatistics', {})
        ap = summary.get('assetProfile', {})
        pr = summary.get('price', {})
        rt = summary.get('recommendationTrend', {})

        # Update price fields if v10 has better data
        if g(sd, 'previousClose'):
            prev_close = safe_float(g(sd, 'previousClose'))
            chg = round(price - prev_close, 4) if price and prev_close else chg
            chg_pct = (
                round(chg / prev_close, 6) if chg and prev_close else chg_pct
            )

        mkt_cap = g(pr, 'marketCap') or g(sd, 'marketCap')

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

        # 3. Chart history
        chart = []
        try:
            hist_data = yf_chart(sym, session, '1y')
            chart = parse_chart_history(hist_data)
        except Exception:
            pass

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
            'volume': (
                g(pr, 'regularMarketVolume') or meta.get('regularMarketVolume')
            ),
            'avgVolume': safe_float(g(sd, 'averageVolume')),
            'fiftyTwoWeekHigh': safe_float(
                g(sd, 'fiftyTwoWeekHigh') or meta.get('fiftyTwoWeekHigh')
            ),
            'fiftyTwoWeekLow': safe_float(
                g(sd, 'fiftyTwoWeekLow') or meta.get('fiftyTwoWeekLow')
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
            '_v10': v10_err,
        }

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb}), 500


if __name__ == '__main__':
    app.run(debug=True)
