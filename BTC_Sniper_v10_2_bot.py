import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import os
import datetime
import time
from sklearn.ensemble import RandomForestClassifier
from ctrader_fix import *

# --- KONFIGURACE ---
TRAIL_PCT = 0.008  # 0.8% Trailing Stop-Loss
MAX_HOLD = 8       # Maximální doba držení v hodinách
RISK_PCT = 0.25    # 25% marže z virtuálního zůstatku pro simulaci
LEVERAGE = 5       # Páka pro simulaci

def loguj_aktivitu(zprava):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open('BTC_bot_activity.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {zprava}\n")

# --- POMOCNÁ FUNKCE: RUČNÍ VÝROBA FIX ZPRÁVY (MACGYVER STYLE) ---
def create_fix_message(msg_type, pairs):
    """
    Sestaví RAW FIX zprávu bez potřeby externí knihovny.
    Počítá správně BodyLength (tag 9) a Checksum (tag 10).
    """
    s = "\x01" # SOH oddělovač
    
    # Sestavení těla zprávy
    body = ""
    for tag, value in pairs.items():
        body += f"{tag}={value}{s}"
        
    # Výpočet délky (tag 9)
    # BodyLength = délka od tagu 35 (včetně) do tagu 10 (nevčetně)
    temp_body_for_len = f"35={msg_type}{s}{body}"
    length = len(temp_body_for_len)
    
    # Sestavení zprávy před checksumem: 8=...|9=...|35=...|...body...|
    pre_checksum_msg = f"8=FIX.4.4{s}9={length}{s}{temp_body_for_len}"
    
    # Výpočet Checksum (tag 10)
    checksum = sum(pre_checksum_msg.encode('ascii')) % 256
    checksum_str = f"{checksum:03d}" 
    
    final_msg = f"{pre_checksum_msg}10={checksum_str}{s}"
    return final_msg.encode('ascii') # Vracíme jako BYTES

# --- OPRAVENÁ FUNKCE PRO FIX API ---
def proved_obchod_fix(symbol, side):
    symbol_clean = symbol.replace("-", "").replace("/", "")
    host = os.getenv('FIX_HOST')
    port = int(os.getenv('FIX_PORT'))
    sender_id = os.getenv('FIX_SENDER_ID')
    target_id = os.getenv('FIX_TARGET_ID')
    password = os.getenv('FIX_PASSWORD')
    
    # 2.0 loty pro BTC na tvém 200k účtu
    volume = 2.0 

    print(f"--- FIX API: Odesílám {side} {symbol_clean} ({volume}) přes MANUAL RAW ---")

    try:
        client = Client(host, port, sender_id, target_id, password)
        
        # PŘÍPRAVA DAT
        order_id = f"BOB_BTC_{int(time.time())}"
        transact_time = datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3]
        side_code = "1" if side.lower() == "buy" else "2"
        
        # Vytvoříme slovník tagů pro NewOrderSingle (D)
        tags = {
            11: order_id,       # ClOrdID
            55: symbol_clean,   # Symbol
            54: side_code,      # Side
            38: str(volume),    # OrderQty
            40: "1",            # OrdType = Market
            60: transact_time,  # TransactTime
            59: "0"             # TimeInForce = Day
        }
        
        # ODESLÁNÍ RAW BYTES
        raw_msg = create_fix_message("D", tags)
        print(f"DEBUG: Odesílám raw bytes: {raw_msg}")
        
        client.send(raw_msg)
        
        msg = f"ÚSPĚCH: RAW FIX (bytes) odesláno. ID: {order_id}"
        print(msg)
        loguj_aktivitu(msg)
        return True

    except Exception as e:
        msg = f"CHYBA: Odeslání RAW selhalo. Důvod: {str(e)}"
        print(msg)
        loguj_aktivitu(msg)
        return False

# 1. DATA A INDIKÁTORY
symbol = 'BTC-USD'
df_raw = yf.download(symbol, period='720d', interval='1h', auto_adjust=True)
if isinstance(df_raw.columns, pd.MultiIndex):
    df_raw.columns = df_raw.columns.get_level_values(0)
df = df_raw.copy()

df['RSI'] = ta.rsi(df['Close'], length=7)
macd = ta.macd(df['Close'], fast=8, slow=21, signal=5)
macd_h_col = [c for c in macd.columns if 'h' in c.lower()][0]
df['MACD_H'] = macd[macd_h_col]

# 2. TRÉNOVÁNÍ AI MODELU (Data z konce roku 2024)
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

# 3. PREDIKCE A SIGNÁL
df['Prob_L'] = model_l.predict_proba(df[features])[:, 1]
df['Prob_S'] = model_s.predict_proba(df[features])[:, 1]
df['Signal'] = 0
df.loc[df['Prob_L'] > 0.51, 'Signal'] = 1
df.loc[df['Prob_S'] > 0.51, 'Signal'] = -1

# 4. SIMULACE HISTORIE (Trailing Stop-Loss logika)
def run_trailing_sim(data):
    balance = 1000.0
    with open('vypis_obchodu_TSL.txt', 'w') as f:
        f.write("DATUM | TYP | VSTUP | VYSTUP (TSL) | ZISK USD | BALANCE\n")
        f.write("-" * 75 + "\n")
        for i in range(1, len(data) - MAX_HOLD):
            sig = data['Signal'].iloc[i]
            if sig != 0:
                entry = data['Close'].iloc[i]
                res = 0
                if sig == 1: # LONG
                    peak = entry
                    for h in range(1, MAX_HOLD + 1):
                        curr_p = data['Close'].iloc[i+h]
                        peak = max(peak, data['High'].iloc[i+h])
                        sl = peak * (1 - TRAIL_PCT)
                        if curr_p < sl:
                            res = (sl - entry) / entry
                            break
                        res = (curr_p - entry) / entry
                else: # SHORT
                    bottom = entry
                    for h in range(1, MAX_HOLD + 1):
                        curr_p = data['Close'].iloc[i+h]
                        bottom = min(bottom, data['Low'].iloc[i+h])
                        sl = bottom * (1 + TRAIL_PCT)
                        if curr_p > sl:
                            res = (entry - sl) / entry
                            break
                        res = (entry - curr_p) / entry
                pnl_usd = (balance * RISK_PCT) * (res * LEVERAGE - 0.0012)
                balance += pnl_usd
                typ = "LONG " if sig == 1 else "SHORT"
                f.write(f"{data.index[i]} | {typ} | {entry:8.2f} | {entry*(1+res):8.2f} | {pnl_usd:8.2f} | {balance:8.2f}\n")
    return balance

final_bal = run_trailing_sim(df)

# 5. REÁLNÁ KONTROLA A AKCE
posledni_radek = df.iloc[-1]
prob_l = posledni_radek['Prob_L'] * 100
prob_s = posledni_radek['Prob_S'] * 100
cena = posledni_radek['Close']

status_rozbor = f"Analýza ceny {cena:.2f} | AI Predikce: Long {prob_l:.1f}%, Short {prob_s:.1f}%"
print(status_rozbor)

signal_dnes = posledni_radek['Signal']
if signal_dnes != 0:
    smer = "BUY" if signal_dnes == 1 else "SELL"
    loguj_aktivitu(f"{status_rozbor} -> AKCE: {smer}")
    proved_obchod_fix(symbol, smer)
else:
    loguj_aktivitu(f"{status_rozbor} -> NEČINNOST: Signál pod hranicí 51%")
    print("Aktuálně žádný signál k reálnému obchodu.")

print(f"Simulace hotova. Teoretický zůstatek: {final_bal:.2f} USD")
