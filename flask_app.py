from flask import Flask, jsonify
from flask_cors import CORS
import requests
import traceback
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

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


def av_income_statement(sym):
    if not AV_KEY:
        return []
    try:
        url = (
            f'https://www.alphavantage.co/query'
            f'?function=INCOME_STATEMENT&symbol={sym}&apikey={AV_KEY}'
        )
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            reports = data.get('annualReports', [])
            history = []
            for rep in reports[:4]:
                year = rep.get('fiscalDateEnding', '')[:4]
                revenue = safe_float(rep.get('totalRevenue'))
                gross = safe_float(rep.get('grossProfit'))
                ebit = safe_float(rep.get('ebit'))
                net = safe_float(rep.get('netIncome'))
                shares = safe_float(rep.get('commonStockSharesOutstanding'))
                eps = round(net / shares, 4) if net and shares else None
                history.append({
                    'year': year,
                    'revenue': revenue,
                    'grossProfit': gross,
                    'ebit': ebit,
                    'netIncome': net,
                    'dilutedEps': eps,
                })
            return history
    except Exception:
        pass
    return []


def av_earnings(sym):
    if not AV_KEY:
        return [], []
    try:
        url = (
            f'https://www.alphavantage.co/query'
            f'?function=EARNINGS&symbol={sym}&apikey={AV_KEY}'
        )
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            quarterly = data.get('quarterlyEarnings', [])
            history = []
            for q in quarterly[:8]:
                reported = safe_float(q.get('reportedEPS'))
                estimated = safe_float(q.get('estimatedEPS'))
                surprise = safe_float(q.get('surprisePercentage'))
                history.append({
                    'date': q.get('reportedDate', ''),
                    'quarter': q.get('fiscalDateEnding', ''),
                    'reportedEps': reported,
                    'estimatedEps': estimated,
                    'surprisePct': surprise,
                })
            annual = data.get('annualEarnings', [])
            estimates = []
            for a in annual[:4]:
                estimates.append({
                    'year': a.get('fiscalDateEnding', '')[:4],
                    'eps': safe_float(a.get('reportedEPS')),
                })
            return history, estimates
    except Exception:
        pass
    return [], []


def av_balance_sheet(sym):
    if not AV_KEY:
        return {}
    try:
        url = (
            f'https://www.alphavantage.co/query'
            f'?function=BALANCE_SHEET&symbol={sym}&apikey={AV_KEY}'
        )
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            reports = data.get('annualReports', [])
            if reports:
                rep = reports[0]
                return {
                    'totalDebt': safe_float(
                        rep.get('shortLongTermDebtTotal') or rep.get('longTermDebt')
                    ),
                    'totalCash': safe_float(
                        rep.get('cashAndCashEquivalentsAtCarryingValue')
                    ),
                    'currentAssets': safe_float(rep.get('totalCurrentAssets')),
                    'currentLiabilities': safe_float(rep.get('totalCurrentLiabilities')),
                }
    except Exception:
        pass
    return {}


def av_cash_flow(sym):
    if not AV_KEY:
        return {}
    try:
        url = (
            f'https://www.alphavantage.co/query'
            f'?function=CASH_FLOW&symbol={sym}&apikey={AV_KEY}'
        )
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            reports = data.get('annualReports', [])
            if reports:
                rep = reports[0]
                op_cf = safe_float(rep.get('operatingCashflow'))
                capex = safe_float(rep.get('capitalExpenditures'))
                if capex is not None:
                    capex = abs(capex)
                fcf = (
                    (op_cf - capex)
                    if (op_cf is not None and capex is not None)
                    else op_cf
                )
                return {'freeCashflow': fcf}
    except Exception:
        pass
    return {}


@app.route('/api/stock/<ticker>')
def get_stock(ticker):
    try:
        sym = ticker.upper()

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

        chart = []
        try:
            hist_data = yf_chart(sym, '1y')
            chart = parse_chart_history(hist_data)
        except Exception:
            pass

        overview = av_overview(sym)
        aq = av_quote(sym)
        income_history = av_income_statement(sym)
        earnings_history, eps_estimates = av_earnings(sym)
        balance = av_balance_sheet(sym)
        cashflow = av_cash_flow(sym)

        if aq.get('05. price'):
            price = safe_float(aq.get('05. price')) or price
            prev_close = safe_float(aq.get('08. previous close')) or prev_close

        chg = safe_float(aq.get('09. change')) if aq else None
        if not chg and price and prev_close:
            chg = round(price - prev_close, 4)

        chg_pct_raw = None
        if aq:
            raw_pct = aq.get('10. change percent', '')
            if isinstance(raw_pct, str):
                raw_pct = raw_pct.replace('%', '')
            chg_pct_raw = safe_float(raw_pct)

        if chg_pct_raw is not None:
            chg_pct = round(chg_pct_raw / 100, 6)
        elif chg and prev_close:
            chg_pct = round(chg / prev_close, 6)
        else:
            chg_pct = None

        def ov(key):
            v = overview.get(key, 'None')
            return safe_float(v) if v not in ('None', '', '-') else None

        ca = balance.get('currentAssets')
        cl = balance.get('currentLiabilities')
        current_ratio = round(ca / cl, 4) if ca and cl else None

        rev_estimates = [
            {'year': r['year'], 'revenue': r['revenue']}
            for r in income_history
            if r.get('revenue')
        ]

        description = overview.get('Description', '') or ''

        result = {
            'symbol': sym,
            'companyName': overview.get('Name') or sym,
            'sector': overview.get('Sector', ''),
            'industry': overview.get('Industry', ''),
            'longBusinessSummary': description,

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
            'pegRatio': ov('PEGRatio'),
            'enterpriseToRevenue': ov('EVToRevenue'),

            'debtEquity': None,
            'currentRatio': current_ratio,
            'quickRatio': None,
            'totalDebt': balance.get('totalDebt'),
            'totalCash': balance.get('totalCash'),
            'freeCashflow': cashflow.get('freeCashflow'),

            'roe': ov('ReturnOnEquityTTM'),
            'returnOnAssets': ov('ReturnOnAssetsTTM'),
            'revenueGrowth': ov('QuarterlyRevenueGrowthYOY'),
            'earningsGrowth': ov('QuarterlyEarningsGrowthYOY'),
            'grossMargin': ov('GrossProfitTTM'),
            'operatingMargin': ov('OperatingMarginTTM'),
            'profitMargins': ov('ProfitMargin'),

            'dividendYield': ov('DividendYield'),
            'beta': ov('Beta'),
            'eps': ov('EPS'),
            'targetPrice': ov('AnalystTargetPrice'),

            'incomeHistory': income_history,
            'epsEstimates': eps_estimates,
            'revEstimates': rev_estimates,
            'earningsHistory': earnings_history,

            'chart': chart,
            'analystRecommendations': [],
            '_source': 'alphavantage' if overview.get('Symbol') else 'yahoo_v8_only',
        }

        return jsonify(result)

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb}), 500



@app.route('/api/debug')
def debug_info():
    import traceback as tb2
    key_present = bool(AV_KEY)
    key_prefix = AV_KEY[:4] + '...' if AV_KEY else 'MISSING'
    av_result = {}
    av_error = None
    try:
        url = (
            f'https://www.alphavantage.co/query'
            f'?function=OVERVIEW&symbol=AAPL&apikey={AV_KEY}'
        )
        r = requests.get(url, timeout=15)
        av_result = {
            'status': r.status_code,
            'has_symbol': 'Symbol' in r.json(),
            'keys': list(r.json().keys())[:5],
        }
    except Exception as e:
        av_error = str(e)
    return jsonify({
        'key_present': key_present,
        'key_prefix': key_prefix,
        'av_test': av_result,
        'av_error': av_error,
    })


if __name__ == '__main__':
    app.run(debug=True)
