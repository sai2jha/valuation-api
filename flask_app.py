from flask import Flask, jsonify
from flask_cors import CORS
import requests
import traceback
from datetime import datetime

app = Flask(__name__)
CORS(app)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://finance.yahoo.com/',
}

def safe_float(val):
    try:
        v = float(val)
        return None if (v != v or abs(v) == float('inf')) else v
    except (TypeError, ValueError):
        return None

def get_crumb(session):
    try:
        session.get('https://finance.yahoo.com', timeout=10)
        r = session.get('https://query1.finance.yahoo.com/v1/test/getcrumb', timeout=10)
        if r.status_code == 200 and r.text and r.text != 'null':
            return r.text.strip()
    except Exception:
        pass
    return None

def yf_v7_quote(sym, session):
    fields = 'symbol,longName,shortName,regularMarketPrice,regularMarketPreviousClose,regularMarketChange,regularMarketChangePercent,regularMarketVolume,averageDailyVolume3Month,marketCap,trailingPE,forwardPE,priceToBook,priceToSalesTrailing12Months,enterpriseToEbitda,debtToEquity,currentRatio,returnOnEquity,revenueGrowth,earningsGrowth,grossMargins,operatingMargins,dividendYield,beta,trailingEps,targetMeanPrice,fiftyTwoWeekHigh,fiftyTwoWeekLow,sector,industry'
    url = f'https://query1.finance.yahoo.com/v7/finance/quote?symbols={sym}&fields={fields}'
    r = session.get(url, timeout=15)
    r.raise_for_status()
    results = r.json().get('quoteResponse', {}).get('result', [])
    return results[0] if results else {}

def yf_v10_summary(sym, session, crumb):
    if not crumb:
        return {}
    url = f'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}?modules=recommendationTrend,assetProfile&crumb={crumb}'
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        result = r.json().get('quoteSummary', {}).get('result', [None])
        if result and result[0]:
            return result[0]
    except Exception:
        pass
    return {}

def yf_chart(sym, session, period='1y'):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range={period}'
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
        session = requests.Session()
        session.headers.update(HEADERS)

        crumb = get_crumb(session)
        quote = yf_v7_quote(sym, session)
        price = safe_float(quote.get('regularMarketPrice'))

        if not price:
            return jsonify({'error': f'No data found for "{sym}". Check the ticker symbol.'}), 404

        summary = yf_v10_summary(sym, session, crumb)
        rt = summary.get('recommendationTrend', {})
        ap = summary.get('assetProfile', {})

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

        chart = yf_chart(sym, session, '1y')

        prev_close = safe_float(quote.get('regularMarketPreviousClose'))
        chg = safe_float(quote.get('regularMarketChange'))
        chg_pct_raw = safe_float(quote.get('regularMarketChangePercent'))
        chg_pct = round(chg_pct_raw / 100, 6) if chg_pct_raw is not None else None

        result = {
            'symbol': sym,
            'companyName': quote.get('longName') or quote.get('shortName') or sym,
            'sector': quote.get('sector') or ap.get('sector', ''),
            'industry': quote.get('industry') or ap.get('industry', ''),
            'price': price,
            'previousClose': prev_close,
            'change': chg,
            'changePercent': chg_pct,
            'marketCap': safe_float(quote.get('marketCap')),
            'volume': quote.get('regularMarketVolume'),
            'avgVolume': safe_float(quote.get('averageDailyVolume3Month')),
            'fiftyTwoWeekHigh': safe_float(quote.get('fiftyTwoWeekHigh')),
            'fiftyTwoWeekLow': safe_float(quote.get('fiftyTwoWeekLow')),
            'peRatio': safe_float(quote.get('trailingPE')),
            'forwardPE': safe_float(quote.get('forwardPE')),
            'pbRatio': safe_float(quote.get('priceToBook')),
            'psRatio': safe_float(quote.get('priceToSalesTrailing12Months')),
            'evEbitda': safe_float(quote.get('enterpriseToEbitda')),
            'debtEquity': safe_float(quote.get('debtToEquity')),
            'currentRatio': safe_float(quote.get('currentRatio')),
            'roe': safe_float(quote.get('returnOnEquity')),
            'revenueGrowth': safe_float(quote.get('revenueGrowth')),
            'earningsGrowth': safe_float(quote.get('earningsGrowth')),
            'grossMargin': safe_float(quote.get('grossMargins')),
            'operatingMargin': safe_float(quote.get('operatingMargins')),
            'dividendYield': safe_float(quote.get('dividendYield')),
            'beta': safe_float(quote.get('beta')),
            'eps': safe_float(quote.get('trailingEps')),
            'targetPrice': safe_float(quote.get('targetMeanPrice')),
            'chart': chart,
            'analystRecommendations': recs,
        }

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb}), 500

if __name__ == '__main__':
    app.run(debug=True)
