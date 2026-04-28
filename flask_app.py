from flask import Flask, jsonify
from flask_cors import CORS
import yfinance as yf
import traceback

app = Flask(__name__)
CORS(app)

@app.route('/api/stock/<ticker>')
def get_stock(ticker):
      try:
                t = yf.Ticker(ticker.upper())
                info = t.info
                if not info or (info.get('currentPrice') is None and info.get('regularMarketPrice') is None):
                              return jsonify({'error': f'No data found for {ticker.upper()}'}), 404
                          hist = t.history(period='1y')
                chart = [{'date': d.strftime('%Y-%m-%d'), 'close': round(float(r['Close']), 2), 'volume': int(r['Volume'])} for d, r in hist.iterrows()] if not hist.empty else []
                income = {}
                try:
                              fin = t.financials
                              if fin is not None and not fin.empty:
                                                for col in fin.columns[:4]:
                                                                      yr = str(col.year)
                                                                      income[yr] = {
                                                                          'revenue': int(fin.loc['Total Revenue', col]) if 'Total Revenue' in fin.index else None,
                                                                          'netIncome': int(fin.loc['Net Income', col]) if 'Net Income' in fin.index else None
                                                                      }
                                                          except:
                                            pass
                rec_summary = {'strongBuy': 0, 'buy': 0, 'hold': 0, 'sell': 0, 'strongSell': 0}
        try:
                      recs = t.recommendations
                      if recs is not None and not recs.empty:
                                        latest = recs.iloc[-1]
                                        rec_summary = {
                                            'strongBuy': int(latest.get('strongBuy', 0)),
                                            'buy': int(latest.get('buy', 0)),
                                            'hold': int(latest.get('hold', 0)),
                                            'sell': int(latest.get('sell', 0)),
                                            'strongSell': int(latest.get('strongSell', 0))
                                        }
                                except:
            pass
        return jsonify({
                      'ticker': ticker.upper(),
                      'name': info.get('longName') or info.get('shortName', ticker.upper()),
                      'sector': info.get('sector', 'N/A'),
                      'industry': info.get('industry', 'N/A'),
                      'currency': info.get('currency', 'USD'),
                      'price': info.get('currentPrice') or info.get('regularMarketPrice'),
                      'previousClose': info.get('previousClose'),
                      'open': info.get('open'),
                      'dayLow': info.get('dayLow'),
                      'dayHigh': info.get('dayHigh'),
                      'fiftyTwoWeekLow': info.get('fiftyTwoWeekLow'),
                      'fiftyTwoWeekHigh': info.get('fiftyTwoWeekHigh'),
                      'volume': info.get('volume'),
                      'avgVolume': info.get('averageVolume'),
                      'marketCap': info.get('marketCap'),
                      'enterpriseValue': info.get('enterpriseValue'),
                      'peRatio': info.get('trailingPE'),
                      'forwardPE': info.get('forwardPE'),
                      'pegRatio': info.get('pegRatio'),
                      'priceToBook': info.get('priceToBook'),
                      'priceToSales': info.get('priceToSalesTrailing12Months'),
                      'evToEbitda': info.get('enterpriseToEbitda'),
                      'evToRevenue': info.get('enterpriseToRevenue'),
                      'eps': info.get('trailingEps'),
                      'forwardEps': info.get('forwardEps'),
                      'dividendYield': info.get('dividendYield'),
                      'beta': info.get('beta'),
                      'returnOnEquity': info.get('returnOnEquity'),
                      'returnOnAssets': info.get('returnOnAssets'),
                      'profitMargins': info.get('profitMargins'),
                      'grossMargins': info.get('grossMargins'),
                      'operatingMargins': info.get('operatingMargins'),
                      'revenueGrowth': info.get('revenueGrowth'),
                      'earningsGrowth': info.get('earningsGrowth'),
                      'totalCash': info.get('totalCash'),
                      'totalDebt': info.get('totalDebt'),
                      'debtToEquity': info.get('debtToEquity'),
                      'currentRatio': info.get('currentRatio'),
                      'freeCashflow': info.get('freeCashflow'),
                      'targetMeanPrice': info.get('targetMeanPrice'),
                      'targetHighPrice': info.get('targetHighPrice'),
                      'targetLowPrice': info.get('targetLowPrice'),
                      'recommendationKey': info.get('recommendationKey'),
                      'numberOfAnalystOpinions': info.get('numberOfAnalystOpinions'),
                      'analystRecommendations': rec_summary,
                      'chart': chart,
                      'financials': income,
                      'description': info.get('longBusinessSummary', '')
        })
except Exception as e:
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

if __name__ == '__main__':
      app.run(debug=True)
