from flask import Flask, jsonify
from flask_cors import CORS
import requests
import traceback
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)


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


def get_session():
    """Create authenticated session with Yahoo Finance cookies and crumb."""
    session = requests.Session()
    ua = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    )
    session.headers.update({'User-Agent': ua})
    crumb = None

    # Step 1: Visit fc.yahoo.com to get initial A3 cookie
    try:
        session.get(
            'https://fc.yahoo.com',
            headers={
                'User-Agent': ua,
                'Accept': 'text/html,*/*;q=0.8',
            },
            timeout=8
        )
    except Exception:
        pass

    # Step 2: Try the non-JS cookie endpoint
    for url in [
        'https://finance.yahoo.com/?guccounter=1',
        'https://finance.yahoo.com/quote/AAPL/',
        'https://finance.yahoo.com/',
    ]:
        try:
            r = session.get(
                url,
                headers={
                    'User-Agent': ua,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                },
                timeout=15,
                allow_redirects=True
            )
            if r.status_code == 200 and 'finance.yahoo' in r.url:
                break
        except Exception:
            continue

    # Step 3: Get crumb
    for crumb_url in [
        'https://query1.finance.yahoo.com/v1/test/getcrumb',
        'https://query2.finance.yahoo.com/v1/test/getcrumb',
    ]:
        try:
            r = session.get(
                crumb_url,
                headers={
                    'User-Agent': ua,
                    'Accept': 'application/json, text/plain, */*',
                    'Referer': 'https://finance.yahoo.com/',
                },
                timeout=10
            )
            if r.status_code == 200 and r.text and r.text.strip() not in ('null', ''):
                crumb = r.text.strip().strip('"')
                break
        except Exception:
            pass

    return session, crumb


def yf_chart(sym, session, crumb, period='1d'):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range={period}'
    if crumb:
        url += f'&crumb={crumb}'
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://finance.yahoo.com/',
    }
    r = session.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def yf_summary(sym, session, crumb):
    if not crumb:
        return {}, 'no crumb'
    modules = (
        'summaryDetail,financialData,defaultKeyStatistics,'
        'assetProfile,recommendationTrend,price'
    )
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://finance.yahoo.com/',
    }
    for host in ['query1', 'query2']:
        url = (
            f'https://{host}.finance.yahoo.com/v10/finance/quoteSummary/{sym}'
            f'?modules={modules}&crumb={crumb}&formatted=false'
        )
        try:
            r = session.get(url, headers=headers, timeout=20)
            if r.status_code == 200:
                result = r.json().get('quoteSummary', {}).get('result', [None])
                if result and result[0]:
                    return result[0], None
            elif r.status_code == 401:
                continue
        except Exception as e:
            continue
    return {}, f'401 both hosts (crumb: {bool(crumb)})'


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

        session, crumb = get_session()

        # 1. Basic price from v8 chart (works without crumb)
        try:
            price_data = yf_chart(sym, session, crumb, '1d')
        except Exception as e:
            return jsonify({'error': f'No data for "{sym}": {str(e)}'}), 404

        pr_result = price_data.get('chart', {}).get('result', [None])[0]
        if not pr_result:
            return jsonify({'error': f'No data for "{sym}".'}), 404

        meta = pr_result.get('meta', {})
        price = safe_float(meta.get('regularMarketPrice'))
        if not price:
            return jsonify({'error': f'No price for "{sym}".'}), 404

        prev_close_raw = safe_float(
            meta.get('chartPreviousClose') or meta.get('previousClose')
        )

        # 2. Rich valuation data from v10 (requires crumb)
        summary, v10_err = yf_summary(sym, session, crumb)
        sd = summary.get('summaryDetail', {})
        fd = summary.get('financialData', {})
        ks = summary.get('defaultKeyStatistics', {})
        ap = summary.get('assetProfile', {})
        pr = summary.get('price', {})
        rt = summary.get('recommendationTrend', {})

        prev_close = safe_float(g(sd, 'previousClose')) or prev_close_raw
        mkt_cap = g(pr, 'marketCap') or g(sd, 'marketCap')
        chg = round(price - prev_close, 4) if price and prev_close else None
        chg_pct = round(chg / prev_close, 6) if chg and prev_close else None

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
            hist_data = yf_chart(sym, session, crumb, '1y')
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
            'volume': g(pr, 'regularMarketVolume') or meta.get('regularMarketVolume'),
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
            '_debug': {'crumb': crumb, 'v10': v10_err},
        }

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb}), 500


if __name__ == '__main__':
    app.run(debug=True)
