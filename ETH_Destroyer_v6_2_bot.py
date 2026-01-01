import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import matplotlib.pyplot as plt
import seaborn as sns
import os
import datetime  # OPRAVA: Import celého modulu pro stabilitu
from ctrader_fix import *
from twisted.internet.ssl import CertificateOptions
from twisted.internet import reactor

# Funkce pro logování veškeré aktivity do ETH deníku
def loguj_aktivitu_eth(zprava):
    # Bezpečné volání času
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open('ETH_bot_activity.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {zprava}\n")

# Načtení FIX údajů z GitHub Secrets
def proved_obchod_fix(symbol, side):
    symbol = symbol.replace("-", "")
    host = os.getenv('FIX_HOST')
    port = int(os.getenv('FIX_PORT'))
    sender_id = os.getenv('FIX_SENDER_ID')
    target_id = os.getenv('FIX_TARGET_ID')
    password = os.getenv('FIX_PASSWORD')
    
    volume = 15.0 if symbol == "ETHUSD" else 2.0 

    print(f"--- FIX API: Odesílám {side} {symbol} ({volume} lotů) ---")
    
    try:
        client = Client(host, port, sender_id, target_id, password)
        client.sendOrder(symbol, side, volume)
        msg = f"ÚSPĚCH: FIX příkaz {side} {symbol} odeslán (objem: {volume})"
        print(msg)
        loguj_aktivitu_eth(msg)
        return True
    except Exception as e:
        msg = f"CHYBA: FIX příkaz {side} selhal. Důvod: {str(e)}"
        print(msg)
        loguj_aktivitu_eth(msg)
        return False

# 1. DATA
symbol = 'ETH-USD'
df = yf.download(symbol, period='720d', interval='1h', auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
df.dropna(inplace=True)

# 2. INDIKÁTORY
df['EMA_FAST'] = ta.ema(df['Close'], length=12)
df['EMA_SLOW'] = ta.ema(df['Close'], length=26)
df['RSI'] = ta.rsi(df['Close'], length=14)
adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
df['ADX'] = adx_df.iloc[:, 0]
df['DMP'] = adx_df.iloc[:, 1]
df['DMN'] = adx_df.iloc[:, 2]

# 3. SIGNÁLY
df['Signal'] = 0
# Režim Trend (ADX > 30)
df.loc[(df['ADX'] > 30) & (df['DMP'] > df['DMN']) & (df['EMA_FAST'] > df['EMA_SLOW']), 'Signal'] = 1
df.loc[(df['ADX'] > 30) & (df['DMN'] > df['DMP']) & (df['EMA_FAST'] < df['EMA_SLOW']), 'Signal'] = -1
# Režim Contrarian (ADX <= 30)
df.loc[(df['ADX'] <= 30) & (df['EMA_FAST'] > df['EMA_SLOW']) & (df['RSI'] > 58), 'Signal'] = -1
df.loc[(df['ADX'] <= 30) & (df['EMA_FAST'] < df['EMA_SLOW']) & (df['RSI'] < 42), 'Signal'] = 1

# --- LOGOVÁNÍ AKTUÁLNÍHO STAVU ---
posledni_radek = df.iloc[-1]
cena = posledni_radek['Close']
adx_val = posledni_radek['ADX']
rsi_val = posledni_radek['RSI']

status_rozbor = f"Analýza ceny {cena:.2f} | ADX: {adx_val:.2f}, RSI: {rsi_val:.2f}"
print(f"--- {status_rozbor} ---")

# 6. REÁLNÉ ROZHODNUTÍ A ODESLÁNÍ
if posledni_radek['Signal'] != 0:
    smer = "BUY" if posledni_radek['Signal'] == 1 else "SELL"
    loguj_aktivitu_eth(f"{status_rozbor} -> AKCE: {smer}")
    proved_obchod_fix(symbol, smer)
else:
    loguj_aktivitu_eth(f"{status_rozbor} -> NEČINNOST: Žádná technická shoda")
    print("Aktuálně žádný signál k obchodu.")

# 4. SIMULACE (Volitelné, pro logy v Actions)
print(f"Konečný stav ETH simulace: {df.index[-1]}")
