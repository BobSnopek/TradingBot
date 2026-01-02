import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import os
import datetime
import time
import ssl
import socket
from sklearn.ensemble import RandomForestClassifier

# --- KONFIGURACE ---
TRAIL_PCT = 0.008
MAX_HOLD = 8
RISK_PCT = 0.25
LEVERAGE = 5

def loguj_aktivitu(zprava):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open('BTC_bot_activity.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {zprava}\n")

def create_fix_msg(msg_type, tags_dict):
    s = "\x01"
    body = ""
    for tag, val in tags_dict.items():
        body += f"{tag}={val}{s}"
    temp_head = f"35={msg_type}{s}{body}"
    length = len(temp_head)
    msg_str = f"8=FIX.4.4{s}9={length}{s}{temp_head}"
    checksum = sum(msg_str.encode('ascii')) % 256
    msg_final = f"{msg_str}10={checksum:03d}{s}"
    return msg_final.encode('ascii')

def proved_obchod_fix(symbol, side):
    symbol_clean = symbol.replace("-", "").replace("/", "")
    
    # --- HARDCODED ÚDAJE (FTMO) ---
    host = "live-uk-eqx-01.p.c-trader.com"
    port = 5212
    sender_comp_id = "live.ftmo.17032147"
    username_int = "17032147"
    target_comp_id = "cServer"
    password = "TraderHeslo@2026"
    
    volume = 2
    
    print(f"--- PŘÍMÝ FIX SOCKET: Odesílám {side} {symbol_clean} ---")
    
    try:
        context = ssl.create_default_context()
        sock = socket.create_connection((host, port))
        ssock = context.wrap_socket(sock, server_hostname=host)
        
        # LOGON (MsgType=A)
        # Odstraněn tag 50 (QUOTE)
        logon_tags = {
            49: sender_comp_id,
            56: target_comp_id,
            57: "TRADE",
            34: 1,
            52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            98: "0",
            108: "30",
            553: username_int,
            554: password,
            141: "Y"
        }
        ssock.sendall(create_fix_msg("A", logon_tags))
        
        response = ssock.recv(4096).decode('ascii', errors='ignore')
        if "35=A" in response and "58=" not in response:
            print(f"DEBUG: Logon OK! (Uživatel: {username_int})")
        else:
            err_text = "Neznámá chyba"
            if "58=" in response:
                err_text = response.split("58=")[1].split("\x01")[0]
            print(f"VAROVÁNÍ: Logon selhal: {err_text}")

        # ORDER (MsgType=D)
        order_id = f"BOB_{int(time.time())}"
        side_code = "1" if side.lower() == "buy" else "2"
        
        order_tags = {
            49: sender_comp_id,
            56: target_comp_id,
            57: "TRADE",
            34: 2,
            52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            11: order_id,
            55: symbol_clean,
            54: side_code,
            38: str(int(volume)),
            40: "1",
            59: "0",
            60: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3]
        }
        
        ssock.sendall(create_fix_msg("D", order_tags))
        time.sleep(2)
        response_order = ssock.recv(4096).decode('ascii', errors='ignore')
        ssock.close()
        
        if "35=8" in response_order:
            if "39=8" not in response_order:
                msg = f"ÚSPĚCH: Obchod potvrzen! Detail: {response_order}"
                print(msg)
                loguj_aktivitu(msg)
                return True
            else:
                err = "Zamítnuto"
                if "58=" in response_order:
                    err = response_order.split("58=")[1].split("\x01")[0]
                msg = f"ZAMÍTNUTO: {err}"
                print(msg)
                loguj_aktivitu(msg)
                return False
        else:
            msg = f"VÝSLEDEK NEJASNÝ: {response_order}"
            print(msg)
            loguj_aktivitu(msg)
            return True

    except Exception as e:
        print(f"CHYBA: {e}")
        return False

# 1. DATA
symbol = 'BTC-USD'
df_raw = yf.download(symbol, period='720d', interval='1h', auto_adjust=True)
if isinstance(df_raw.columns, pd.MultiIndex):
    df_raw.columns = df_raw.columns.get_level_values(0)
df = df_raw.copy()
df['RSI'] = ta.rsi(df['Close'], length=7)
macd = ta.macd(df['Close'], fast=8, slow=21, signal=5)
macd_h_col = [c for c in macd.columns if 'h' in c.lower()][0]
df['MACD_H'] = macd[macd_h_col]

# 2. TRÉNINK
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

# 3. PREDIKCE
df['Prob_L'] = model_l.predict_proba(df[features])[:, 1]
df['Prob_S'] = model_s.predict_proba(df[features])[:, 1]
df['Signal'] = 0
df.loc[df['Prob_L'] > 0.51, 'Signal'] = 1
df.loc[df['Prob_S'] > 0.51, 'Signal'] = -1

# 4. SIMULACE
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

# 5. EXECUTION
posledni_radek = df.iloc[-1]
signal_dnes = posledni_radek['Signal']
print(f"--- Analýza {symbol} ---")
if signal_dnes != 0:
    smer = "BUY" if signal_dnes == 1 else "SELL"
    loguj_aktivitu(f"AKCE: {smer}")
    proved_obchod_fix(symbol, smer)
else:
    loguj_aktivitu("NEČINNOST")
    print("Žádný signál.")
print(f"Simulace hotova.")
