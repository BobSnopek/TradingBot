import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import os
import datetime
import time
import ssl
import socket

# --- KONFIGURACE ---
TRAIL_PCT = 0.012
MAX_HOLD = 12
RISK_PCT = 0.20
LEVERAGE = 3

def loguj_aktivitu_eth(zprava):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open('ETH_bot_activity.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {zprava}\n")

def create_fix_msg(msg_type, tags_dict):
    s = "\x01"
    # Hlavička
    head_tags = ['35', '49', '56', '50', '57', '34', '52']
    head_str = ""
    head_data = {k: tags_dict.get(k) for k in [35, 49, 56, 50, 57, 34, 52]}
    head_data[35] = msg_type
    
    for tag_str in head_tags:
        tag = int(tag_str) if tag_str != '35' else 35
        val = tags_dict.get(tag)
        if val:
            head_str += f"{tag}={val}{s}"
        elif tag == 35:
            head_str += f"35={msg_type}{s}"

    body_str = ""
    for tag, val in tags_dict.items():
        if str(tag) not in head_tags and tag != 35:
            body_str += f"{tag}={val}{s}"
            
    full_content = head_str + body_str
    length = len(full_content)
    msg_str = f"8=FIX.4.4{s}9={length}{s}{full_content}"
    checksum = sum(msg_str.encode('ascii')) % 256
    msg_final = f"{msg_str}10={checksum:03d}{s}"
    return msg_final.encode('ascii')

def proved_obchod_fix(symbol, side):
    symbol_clean = symbol.replace("-", "").replace("/", "")
    
    host = "live-uk-eqx-01.p.c-trader.com"
    
    # --- ZMĚNA STRATEGIE: TESTUJEME DATA PORT 5211 ---
    port = 5211 
    
    # Vracíme se k dlouhému ID (protože krátké bylo zamítnuto)
    sender_comp_id = "live.ftmo.17032147"
    username_int = "17032147"
    target_comp_id = "cServer"
    
    password = "CHeslo2026" 
    
    print(f"--- TEST PŘIPOJENÍ NA DATOVÝ PORT (5211) ---")
    
    try:
        context = ssl.create_default_context()
        sock = socket.create_connection((host, port))
        ssock = context.wrap_socket(sock, server_hostname=host)
        
        # LOGON PRO PORT 5211 (Vyžaduje TargetSubID=QUOTE)
        logon_tags = {
            49: sender_comp_id, 
            56: target_comp_id,
            57: "QUOTE",        # <--- Pro port 5211 musí být QUOTE
            50: "QUOTE",        # SenderSubID často taky QUOTE
            34: 1,
            52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            98: "0",
            108: "30",
            553: username_int,
            554: password,
            141: "Y"
        }
        
        print("Odesílám Logon na port 5211...")
        ssock.sendall(create_fix_msg("A", logon_tags))
        
        response = ssock.recv(4096).decode('ascii', errors='ignore')
        
        if "35=A" in response and "58=" not in response:
            msg = f"!!! SLÁVA !!! LOGON NA 5211 ÚSPĚŠNÝ! HESLO JE SPRÁVNĚ."
            print(msg)
            print(f"Server odpověděl: {response}")
            loguj_aktivitu_eth(msg)
            # Pokud toto projde, víme, že heslo platí. Obchodovat tu sice nejde, ale potvrdíme údaje.
            return True
        else:
            err = "Neznámá chyba"
            if "58=" in response:
                err = response.split("58=")[1].split("\x01")[0]
            msg = f"LOGON SELHAL I NA 5211: {err}"
            print(msg)
            print(f"RAW: {response}")
            return False

    except Exception as e:
        print(f"CHYBA: {e}")
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
df.loc[(df['ADX'] > 30) & (df['DMP'] > df['DMN']) & (df['EMA_FAST'] > df['EMA_SLOW']), 'Signal'] = 1
df.loc[(df['ADX'] > 30) & (df['DMN'] > df['DMP']) & (df['EMA_FAST'] < df['EMA_SLOW']), 'Signal'] = -1
df.loc[(df['ADX'] <= 30) & (df['EMA_FAST'] > df['EMA_SLOW']) & (df['RSI'] > 58), 'Signal'] = -1
df.loc[(df['ADX'] <= 30) & (df['EMA_FAST'] < df['EMA_SLOW']) & (df['RSI'] < 42), 'Signal'] = 1

# 4. SIMULACE
def run_trailing_sim_eth(data):
    balance = 1000.0
    with open('vypis_obchodu_ETH_TSL.txt', 'w', encoding='utf-8') as f:
        f.write("DATUM | TYP | VSTUP | VYSTUP (TSL) | ZISK USD | BALANCE\n")
        f.write("-" * 75 + "\n")
        for i in range(1, len(data) - MAX_HOLD):
            sig = data['Signal'].iloc[i]
            if sig != 0 and sig != data['Signal'].iloc[i-1]:
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
final_bal = run_trailing_sim_eth(df)

# 5. EXECUTION
posledni_radek = df.iloc[-1]
signal_dnes = posledni_radek['Signal']
print(f"--- Analýza {symbol} ---")
if signal_dnes != 0:
    smer = "BUY" if signal_dnes == 1 else "SELL"
    loguj_aktivitu_eth(f"AKCE: {smer}")
    proved_obchod_fix(symbol, smer)
else:
    loguj_aktivitu_eth("NEČINNOST")
    print("Žádný signál.")
print(f"Simulace hotova.")
