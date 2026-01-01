import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.ensemble import RandomForestClassifier
import os
from datetime import datetime
from ctrader_fix import *
from twisted.internet.ssl import CertificateOptions
from twisted.internet import reactor

# Funkce pro logování veškeré aktivity
def loguj_aktivitu(zprava):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open('BTC_bot_activity.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {zprava}\n")

# Načtení FIX údajů z GitHub Secrets
def proved_obchod_fix(symbol, side):
    symbol = symbol.replace("-", "")
    host = os.getenv('FIX_HOST')
    port = int(os.getenv('FIX_PORT'))
    sender_id = os.getenv('FIX_SENDER_ID')
    target_id = os.getenv('FIX_TARGET_ID')
    password = os.getenv('FIX_PASSWORD')
    
    volume = 2.0 if symbol == "BTCUSD" else 15.0 

    try:
        client = Client(host, port, sender_id, target_id, password)
        client.sendOrder(symbol, side, volume)
        msg = f"ÚSPĚCH: FIX příkaz {side} {symbol} odeslán (objem: {volume})"
        print(msg)
        loguj_aktivitu(msg)
        return True
    except Exception as e:
        msg = f"CHYBA: FIX příkaz {side} selhal. Důvod: {str(e)}"
        print(msg)
        loguj_aktivitu(msg)
        return False

# 1. DATA
symbol = 'BTC-USD'
df_raw = yf.download(symbol, period='720d', interval='1h', auto_adjust=True)
if isinstance(df_raw.columns, pd.MultiIndex):
    df_raw.columns = df_raw.columns.get_level_values(0)
df = df_raw.copy()

# 2. INDIKÁTORY
df['RSI'] = ta.rsi(df['Close'], length=7)
macd = ta.macd(df['Close'], fast=8, slow=21, signal=5)
macd_h_col = [c for c in macd.columns if 'h' in c.lower()][0]
df['MACD_H'] = macd[macd_h_col]

# 3. TRÉNINK
train_data = yf.download(symbol, start="2024-10-01", end="2025-01-01", interval='1h', auto_adjust=True)
if isinstance(train_data.columns, pd.MultiIndex):
    train_data.columns = train_data.columns.get_level_values(0)
td = train_data.copy()
td['RSI'] = ta.rsi(td['Close'], length=7)
td_macd = ta.macd(td['Close'], fast=8, slow=21, signal=5)
td_macd_h_col = [c for c in td_macd.columns if 'h' in c.lower()][0]
td['MACD_H'] = td_macd[td_macd_h_col]
td['Target_L'] = np.where(td['Close'].shift(-2) > td['Close'] * 1.003, 1, 0)
td['Target_S'] = np.where(td['Close'].shift(-2) < td['Close'] * 0.997, 1, 0)
td = td.dropna()

features = ['RSI', 'MACD_H']
model_l = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42).fit(td[features], td['Target_L'])
model_s = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42).fit(td[features], td['Target_S'])

# 4. PREDIKCE
df['Prob_L'] = model_l.predict_proba(df[features])[:, 1]
df['Prob_S'] = model_s.predict_proba(df[features])[:, 1]
df['Signal'] = 0
df.loc[df['Prob_L'] > 0.51, 'Signal'] = 1
df.loc[df['Prob_S'] > 0.51, 'Signal'] = -1

# --- LOGOVÁNÍ AKTUÁLNÍHO STAVU ---
posledni_radek = df.iloc[-1]
prob_l = posledni_radek['Prob_L'] * 100
prob_s = posledni_radek['Prob_S'] * 100
cena = posledni_radek['Close']

# Základní záznam o každém běhu
status_rozbor = f"Analýza ceny {cena:.2f} | AI Predikce: Long {prob_l:.1f}%, Short {prob_s:.1f}%"
print(status_rozbor)

# 6. REÁLNÉ ROZHODNUTÍ
signal_dnes = posledni_radek['Signal']

if signal_dnes != 0:
    smer = "BUY" if signal_dnes == 1 else "SELL"
    loguj_aktivitu(f"{status_rozbor} -> AKCE: {smer}")
    proved_obchod_fix(symbol, smer)
else:
    loguj_aktivitu(f"{status_rozbor} -> NEČINNOST: Signál pod hranicí 51%")
    print("Aktuálně žádný signál k reálnému obchodu.")

# (Simulace a heatmapa zůstávají beze změny pod tímto bodem...)
