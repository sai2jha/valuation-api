from flask import Flask, jsonify
from flask_cors import CORS
import requests
import traceback
import json
import re
from datetime import datetime

app = Flask(__name__)
CORS(app)

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Cache-Control': 'max-age=0',
}

JSON_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://finance.yahoo.com/',
    'Origin': 'https://finance.yahoo.com',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
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


def setup_session():
    session = requests.Session()
    crumb = None
    cookie_str = None

    try:
        # Step 1: Get initial cookies by visiting Yahoo Finance
        r1 = session.get(
            'https://finance.yahoo.com/quote/AAPL/',
            headers=BROWSER_HEADERS,
            timeout=15,
            allow_redirects=True
        )
        # Step 2: Accept consent if redirected to consent page
        if 'consent' in r1.url or 'guce' in r1.url:
            # Try to post consent
            session.post(
                'https://consent.yahoo.com/v2/collectConsent',
                data={'agree': ['agree', 'agree'], 'consentUUID': ''},
                headers=BROWSER_HEADERS,
                timeout=10
            )
            session.get(
                'https://finance.yahoo.com/quote/AAPL/',
                headers=BROWSER_HEADERS,
                timeout=15
            )
    except Exception:
        pass

    try:
        # Step 3: Get crumb
        r_crumb = session.get(
            'https://query1.finance.yahoo.com/v1/test/getcrumb',
            headers=JSON_HEADERS,
            timeout=10
        )
        if r_crumb.status_code == 200 and r_crumb.text and r_crumb.text.strip() != 'null':
            crumb = r_crumb.text.strip().strip('"')
    except Exception:
        pass

    return session, crumb


def fetch_chart(sym, session, crumb, period='1d'):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range={period}'
    headers = dict(JSON_HEADERS)
    if crumb:
        url += f'&crumb={crumb}'
    r = session.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_summary(sym, session, crumb):
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
    headers = dict(JSON_HEADERS)
    r = session.get(url, headers=headers, timeout=20)
    if r.status_code != 200:
        return {}, f'v10 status {r.status_code}'
    data = r.json()
    err = data.get('quoteSummary', {}).get('error')
    if err:
        return {}, str(err)
    results = data.get('quoteSummary', {}).get('result', [])
    if results and results[0]:
        return results[0], None
    return {}, 'empty result'


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

        session, crumb = setup_session()

        # Get basic price from v8 chart
        try:
            price_data = fetch_chart(sym, session, crumb, '1d')
        except Exception as e:
            return jsonify({'error': f'No data found for "{sym}": {str(e)}'}), 404

        pr_result = price_data.get('chart', {}).get('result', [None])[0]
        if not pr_result:
            return jsonify({'error': f'No data found for "{sym}".'}), 404

        meta = pr_result.get('meta', {})
        price = safe_float(meta.get('regularMarketPrice'))
        if not price:
            return jsonify({'error': f'No price data for "{sym}".'}), 404

        prev_close = safe_float(meta.get('chartPreviousClose') or meta.get('previousClose'))

        # Get rich valuation data from v10
        summary, v10_err = fetch_summary(sym, session, crumb)
        sd = summary.get('summaryDetail', {})
        fd = summary.get('financialData', {})
        ks = summary.get('defaultKeyStatistics', {})
        ap = summary.get('assetProfile', {})
        pr = summary.get('price', {})
        rt = summary.get('recommendationTrend', {})

        # Use v10 prevClose if available, else v8
        prev_close = safe_float(g(sd, 'previousClose')) or prev_close
        mkt_cap = g(pr, 'marketCap') or g(sd, 'marketCap')
        chg = round(price - prev_close, 4) if price and prev_close else None
        chg_pct = round(chg / prev_close, 6) if chg and prev_close else None

        # Analyst recs
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

        # 1-year chart history
        chart = []
        try:
            hist_data = fetch_chart(sym, session, crumb, '1y')
            chart = parse_chart_history(hist_data)
        except Exception:
            pass

        result = {
            'symbol': sym,
            'companyName': g(pr, 'longName') or g(pr, 'shortName') or sym,
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
            '_debug': {'crumb': bool(crumb), 'v10_err': v10_err},
        }

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb}), 500


if __name__ == '__main__':
    app.run(debug=True)
