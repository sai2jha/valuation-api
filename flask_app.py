from flask import Flask, jsonify
from flask_cors import CORS
import yfinance as yf
import requests
import traceback
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)


def safe_float(val):
    try:
        v = float(val)
        return None if (v != v or abs(v) == float('inf')) else v
    except (TypeError, ValueError):
        return None


def yf_chart_fallback(sym, period='1d'):
    """Direct Yahoo Finance v8 chart as fallback (works without auth)."""
    try:
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://finance.yahoo.com/',
        }
        url = (
            f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'
            f'?interval=1d&range={period}'
        )
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        result = data.get('chart', {}).get('result', [None])[0]
        if not result:
            return None, []
        meta = result.get('meta', {})
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
        return meta, chart
    except Exception:
        return None, []


@app.route('/api/stock/<ticker>')
def get_stock(ticker):
    try:
        sym = ticker.upper()

        # Try yfinance first (handles auth internally)
        try:
            ticker_obj = yf.Ticker(sym)
            info = ticker_obj.info

            price = safe_float(info.get('currentPrice') or info.get('regularMarketPrice'))
            if not price:
                raise ValueError('No price from yfinance')

            # Get history for chart
            hist = ticker_obj.history(period='1y')
            chart = []
            for idx, row in hist.iterrows():
                chart.append({
                    'date': idx.strftime('%Y-%m-%d'),
                    'close': round(float(row['Close']), 2),
                    'volume': int(row['Volume']),
                })

            # Analyst recommendations
            recs = []
            try:
                rec_df = ticker_obj.recommendations
                if rec_df is not None and not rec_df.empty:
                    # Get last few periods
                    for _, row in rec_df.tail(4).iterrows():
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

            prev_close = safe_float(info.get('previousClose'))
            chg = safe_float(info.get('regularMarketChange'))
            if not chg and price and prev_close:
                chg = round(price - prev_close, 4)
            chg_pct_raw = safe_float(info.get('regularMarketChangePercent'))
            if chg_pct_raw:
                chg_pct = round(chg_pct_raw / 100, 6)
            elif chg and prev_close:
                chg_pct = round(chg / prev_close, 6)
            else:
                chg_pct = None

            result = {
                'symbol': sym,
                'companyName': info.get('longName') or info.get('shortName') or sym,
                'sector': info.get('sector', ''),
                'industry': info.get('industry', ''),
                'price': price,
                'previousClose': prev_close,
                'change': chg,
                'changePercent': chg_pct,
                'marketCap': safe_float(info.get('marketCap')),
                'volume': info.get('volume') or info.get('regularMarketVolume'),
                'avgVolume': safe_float(info.get('averageVolume')),
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
            }
            return jsonify(result)

        except Exception as yf_err:
            # Fallback: use v8 chart for basic price + chart data
            meta, chart = yf_chart_fallback(sym, '1y')
            if not meta:
                return jsonify(
                    {'error': f'No data for "{sym}". Check the ticker.'}
                ), 404
            price = safe_float(meta.get('regularMarketPrice'))
            if not price:
                return jsonify(
                    {'error': f'No price data for "{sym}".'}
                ), 404
            prev_close = safe_float(
                meta.get('chartPreviousClose') or meta.get('previousClose')
            )
            chg = round(price - prev_close, 4) if price and prev_close else None
            chg_pct = (
                round(chg / prev_close, 6) if chg and prev_close else None
            )
            return jsonify({
                'symbol': sym,
                'companyName': sym,
                'sector': '',
                'industry': '',
                'price': price,
                'previousClose': prev_close,
                'change': chg,
                'changePercent': chg_pct,
                'marketCap': None,
                'volume': meta.get('regularMarketVolume'),
                'avgVolume': None,
                'fiftyTwoWeekHigh': safe_float(meta.get('fiftyTwoWeekHigh')),
                'fiftyTwoWeekLow': safe_float(meta.get('fiftyTwoWeekLow')),
                'peRatio': None, 'forwardPE': None, 'pbRatio': None,
                'psRatio': None, 'evEbitda': None, 'debtEquity': None,
                'currentRatio': None, 'roe': None, 'revenueGrowth': None,
                'earningsGrowth': None, 'grossMargin': None,
                'operatingMargin': None, 'dividendYield': None,
                'beta': None, 'eps': None, 'targetPrice': None,
                'chart': chart,
                'analystRecommendations': [],
                '_note': f'yfinance failed: {str(yf_err)[:100]}',
            })

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb}), 500


if __name__ == '__main__':
    app.run(debug=True)
