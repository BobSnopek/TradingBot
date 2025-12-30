!pip install yfinance pandas_ta scikit-learn

import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier

# --- KONFIGURACE ---
assets = ['BTC-USD', 'ETH-USD', 'SOL-USD']
period = '730d'
interval = '1h'
fee = 0.001

def build_and_test(symbol):
    # Stažení dat
    data = yf.download(symbol, period=period, interval=interval, auto_adjust=True, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    df = data.copy()

    # Indikátory
    df['RSI'] = ta.rsi(df['Close'], length=14)
    df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
    df['ADX'] = adx_df['ADX_14']
    bb = ta.bbands(df['Close'], length=20, std=2)
    up_col = [c for c in bb.columns if c.startswith('BBU')][0]
    lo_col = [c for c in bb.columns if c.startswith('BBL')][0]
    df['BB_Dist'] = (df['Close'] - bb[lo_col]) / (bb[up_col] - bb[lo_col])
    df['Hour'] = df.index.hour

    # Target a ML příprava
    df['Target'] = np.where(df['Close'].shift(-3) > df['Close'] * 1.005, 1, 0)
    features = ['RSI', 'ADX', 'BB_Dist', 'Hour']
    df_ml = df[features + ['Target', 'Close', 'ATR']].dropna()

    # ML Model
    X = df_ml[features]
    y = df_ml['Target']
    split = int(len(df_ml) * 0.8)
    model = RandomForestClassifier(n_estimators=100, max_depth=5, class_weight='balanced', random_state=42)
    model.fit(X[:split], y[:split])

    # Predikce
    df_ml['AI_Prob'] = model.predict_proba(X)[:, 1]
    df_ml['Signal'] = np.where((df_ml['AI_Prob'] > 0.62) & (df_ml['ADX'] > 20), 1, 0)

    # Výpočet výnosů
    df_ml['Net_Return'] = (df_ml['Close'].pct_change() * df_ml['Signal'].shift(1)) - (df_ml['Signal'].diff().abs() * fee)
    df_ml['Equity'] = (1 + df_ml['Net_Return'].fillna(0)).cumprod()

    # Aktuální stav (Live Signal)
    posledni_stav = df_ml.iloc[-1]
    aktualni_signal = "KOUPIT" if posledni_stav['Signal'] == 1 else "ČEKAT"

    return df_ml['Equity'], aktualni_signal, posledni_stav['AI_Prob']

# --- SPUŠTĚNÍ PRO VŠECHNY MĚNY ---
results = {}
live_signals = []

plt.figure(figsize=(12,6))
plt.style.use('dark_background')

for asset in assets:
    print(f"Analyzuji {asset}...")
    equity, signal, prob = build_and_test(asset)
    results[asset] = equity
    live_signals.append({'Asset': asset, 'Signál': signal, 'Jistota AI': f"{prob*100:.1f}%"})
    plt.plot(equity, label=f"{asset} ({equity.iloc[-1]:.2f}x)")

plt.title("Srovnání portfolia (BTC vs ETH vs SOL)")
plt.legend()
plt.show()

# Výpis tabulky pro tebe
print("\n--- AKTUÁLNÍ SIGNÁLY PRO TUTO HODINU ---")
print(pd.DataFrame(live_signals))

import requests

def posli_telegram_zpravu(zprava):
    # Tady doplníš svoje údaje od BotFathera a UserInfoBota
    token = 'TVUJ_API_TOKEN_TADY'
    chat_id = 'TVOJE_CHAT_ID_TADY'
    
    url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={zprava}"
    
    try:
        requests.get(url)
        print("Zpráva na Telegram odeslána!")
    except Exception as e:
        print(f"Chyba při odesílání na Telegram: {e}")

# Příklad použití v naší tabulce signálů:
for radek in live_signals:
    if radek['Signál'] == "KOUPIT":
        text = f"🚀 SIGNÁL: {radek['Asset']} \nJistota: {radek['Jistota AI']}"
        posli_telegram_zpravu(text)
        
import os

# Načtení tajných údajů z prostředí GitHubu
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def posli_telegram_zpravu(zprava):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage?chat_id={CHAT_ID}&text={zprava}"
    requests.get(url)

if __name__ == "__main__":
    # Spustí analýzu pro všechny měny
    for asset in ['BTC-USD', 'ETH-USD', 'SOL-USD']:
        equity, signal, prob = build_and_test(asset)
        if signal == "KOUPIT":
            text = f"🚀 SIGNÁL: {asset} \nJistota AI: {prob*100:.1f}%"
            posli_telegram_zpravu(text)
    print("Analýza hotova, zprávy odeslány.")
