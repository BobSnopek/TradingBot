import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.ensemble import RandomForestClassifier
import os
from ctrader_fix import *
from twisted.internet.ssl import CertificateOptions
from twisted.internet import reactor

# Načtení FIX údajů z GitHub Secrets
def proved_obchod_fix(symbol, side):
    symbol = symbol.replace("-", "")
    # FIX parametry
    host = os.getenv('FIX_HOST')
    port = int(os.getenv('FIX_PORT'))
    sender_id = os.getenv('FIX_SENDER_ID')
    target_id = os.getenv('FIX_TARGET_ID')
    password = os.getenv('FIX_PASSWORD')
    
    # Výpočet velikosti pro 200K účet (agresivní 1:100)
    volume = 2.0 if symbol == "BTCUSD" else 15.0 

    print(f"--- FIX API: Odesílám {side} {symbol} ({volume} lotů) ---")
    
    try:
        client = Client(host, port, sender_id, target_id, password)
        # OPRAVA: Použití správné metody sendOrder místo send_order
        client.sendOrder(symbol, side, volume)
        print("Příkaz byl úspěšně předán protokolu.")
    except AttributeError:
        # Záložní metoda pro různé verze knihovny
        print("Zkouším záložní metodu odeslání (send_new_order_single)...")
        client = Client(host, port, sender_id, target_id, password)
        client.send_new_order_single(symbol, side, volume)
    
    return True

# 1. DATA - Rok 2025
symbol = 'BTC-USD'
df_raw = yf.download(symbol, period='720d', interval='1h', auto_adjust=True)
if isinstance(df_raw.columns, pd.MultiIndex):
    df_raw.columns = df_raw.columns.get_level_values(0)
df = df_raw.copy()

# 2. INDIKÁTORY
df['RSI'] = ta.rsi(df['Close'], length=7)
stoch = ta.stochrsi(df['Close'], length=10)
stoch_k_col = [c for c in stoch.columns if 'k' in c.lower()][0]
df['Stoch_K'] = stoch[stoch_k_col]
macd = ta.macd(df['Close'], fast=8, slow=21, signal=5)
macd_h_col = [c for c in macd.columns if 'h' in c.lower()][0]
df['MACD_H'] = macd[macd_h_col]

# 3. TRÉNINK (Konec 2024)
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

print(f"--- ANALÝZA BTC ({symbol}) ---")
print(f"Aktuální AI analýza: Long {df['Prob_L'].iloc[-1]*100:.1f}%, Short {df['Prob_S'].iloc[-1]*100:.1f}%")

# 5. SIMULACE S LOGOVÁNÍM DO SOUBORU
def run_logged_sim(data, leverage=5, risk_pct=0.25):
    balance = 1000.0
    equity = np.full(len(data), balance)
    net_returns = np.zeros(len(data))
    
    with open('vypis_obchodu.txt', 'w') as f:
        f.write("DATUM | TYP | VSTUPNI CENA | VYSTUPNI CENA | ZISK/ZTRATA USD | AKTUALNI BALANCE\n")
        f.write("-" * 85 + "\n")
        
        for i in range(1, len(data) - 2):
            sig = data['Signal'].iloc[i]
            if sig != 0:
                entry = data['Close'].iloc[i]
                exit_p = data['Close'].iloc[i+2]
                
                res = (exit_p - entry) / entry * leverage if sig == 1 else (entry - exit_p) / entry * leverage
                res -= 0.0012 
                res = max(res, -0.03) 
                
                pnl_usd = (balance * risk_pct) * res
                balance += pnl_usd
                
                typ_obchodu = "LONG " if sig == 1 else "SHORT"
                f.write(f"{data.index[i]} | {typ_obchodu} | {entry:8.2f} | {exit_p:8.2f} | {pnl_usd:8.2f} USD | {balance:8.2f} USD\n")
                
                net_returns[i] = pnl_usd / (balance - pnl_usd) if (balance - pnl_usd) != 0 else 0
            equity[i] = balance
            
    equity[-2:] = balance
    return equity, net_returns

df['Equity'], df['Net_Return'] = run_logged_sim(df)

# 6. REÁLNÉ ODESLÁNÍ PŘÍKAZU
posledni_radek = df.iloc[-1]
signal_dnes = posledni_radek['Signal']

print(f"--- KONTROLA SIGNÁLU PRO CTRADER ---")
if signal_dnes != 0:
    smer = "Buy" if signal_dnes == 1 else "Sell"
    print(f"!!! NALEZEN AKTUÁLNÍ SIGNÁL: {smer} !!!")
    proved_obchod_fix(symbol, smer)
else:
    print("Aktuálně žádný signál k reálnému obchodu.")

# 7. ZOBRAZENÍ VÝSLEDKŮ
print(f"HOTOVO! Soubor 'vypis_obchodu.txt' byl vytvořen.")
print(f"Konečný zůstatek simulace: {df['Equity'].iloc[-1]:.2f} USD")
