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

# ==========================================
# KONFIGURACE (BTC BOT - CLEAN VERSION)
# ==========================================
SYMBOL_YF = 'BTC-USD'
FIX_SYMBOL_ID = "324"   # ID pro BTC (FTMO/cTrader)

# Risk Management
SL_PCT = 0.015      # 1.5% Stop Loss
TP_PCT = 0.030      # 3.0% Take Profit
LEVERAGE = 5        # PÁKA
RISK_PCT = 0.01     # Risk na obchod (1%)

# Objem obchodu (String pro FIX protokol)
# "1000" units = 0.01 BTC (obvykle u cTraderu)
VOLUME_TO_TRADE = "1000" 

# Soubory pro stav
POS_FILE = 'BTC_position.txt'        # "BUY", "SELL", "NONE"
ACTIVITY_FILE = 'BTC_bot_activity.txt'

# Přihlašovací údaje (FTMO / cTrader)
FIX_HOST = "live-uk-eqx-01.p.c-trader.com"
FIX_PORT = 5212
SENDER_COMP_ID = "live.ftmo.17032147"
TARGET_COMP_ID = "cServer"
USERNAME = "17032147"
PASSWORD = "CTrader2026"

# ==========================================
# POMOCNÉ FUNKCE
# ==========================================

def loguj_aktivitu(zprava):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{timestamp}] {zprava}"
    print(full_msg)
    with open(ACTIVITY_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{full_msg}\n")

def get_saved_position():
    """Přečte aktuální drženou pozici."""
    if os.path.exists(POS_FILE):
        with open(POS_FILE, 'r') as f:
            return f.read().strip()
    return "NONE"

def save_position(side):
    """Uloží novou pozici."""
    with open(POS_FILE, 'w') as f:
        f.write(side)

def get_utc_timestamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

# --- FIX PROTOKOL ---
def create_fix_msg(msg_type, tags_dict):
    s = "\x01"
    head_tags = ['35', '49', '56', '50', '57', '34', '52']
    head_str = ""
    head_data = {k: tags_dict.get(k) for k in [35, 49, 56, 50, 57, 34, 52]}
    head_data[35] = msg_type
    
    for tag_str in head_tags:
        tag = int(tag_str) if tag_str != '35' else 35
        val = tags_dict.get(tag)
        if val: head_str += f"{tag}={val}{s}"
        elif tag == 35: head_str += f"35={msg_type}{s}"

    body_str = ""
    for tag, val in tags_dict.items():
        if str(tag) not in head_tags and tag != 35: body_str += f"{tag}={val}{s}"
            
    full_content = head_str + body_str
    length = len(full_content)
    msg_str = f"8=FIX.4.4{s}9={length}{s}{full_content}"
    checksum = sum(msg_str.encode('ascii')) % 256
    return f"{msg_str}10={checksum:03d}{s}".encode('ascii')

def parse_price_from_response(response):
    try:
        if "6=" in response:
            return float([p.split("=")[1] for p in response.split("\x01") if p.startswith("6=")][0])
    except: return None

def parse_error_reason(response):
    try:
        if "58=" in response:
            return [p.split("=")[1] for p in response.split("\x01") if p.startswith("58=")][0]
    except: return "Neznámá chyba"

def odeslat_fix_prikaz(side, volume_str, is_entry=True, price_for_sl=0):
    """
    side: "BUY" nebo "SELL"
    volume_str: "1000", "1", atd.
    is_entry: True (Otevřít + SL/TP), False (Zavřít = Market)
    """
    loguj_aktivitu(f"FIX: {side} {volume_str} (Entry: {is_entry})")
    
    try:
        context = ssl.create_default_context()
        sock = socket.create_connection((FIX_HOST, FIX_PORT))
        ssock = context.wrap_socket(sock, server_hostname=FIX_HOST)
        
        # Logon
        logon_tags = {49: SENDER_COMP_ID, 56: TARGET_COMP_ID, 50: "TRADE", 57: "TRADE", 34: 1, 52: get_utc_timestamp(), 98: "0", 108: "30", 553: USERNAME, 554: PASSWORD, 141: "Y"}
        ssock.sendall(create_fix_msg("A", logon_tags))
        response = ssock.recv(4096).decode('ascii', errors='ignore')
        if "35=A" not in response:
            loguj_aktivitu(f"Logon selhal: {response}")
            return False

        # Order
        order_id = f"BTC_{int(time.time())}"
        side_code = "1" if side.upper() == "BUY" else "2"
        
        order_tags = {
            49: SENDER_COMP_ID, 56: TARGET_COMP_ID, 50: "TRADE", 57: "TRADE", 34: 2, 52: get_utc_timestamp(),
            11: order_id, 55: FIX_SYMBOL_ID, 54: side_code, 38: volume_str, 40: "1", 59: "0", 60: get_utc_timestamp()
        }
        ssock.sendall(create_fix_msg("D", order_tags))
        time.sleep(1.0)
        response_order = ssock.recv(4096).decode('ascii', errors='ignore')
        
        execution_price = 0.0
        if "35=8" in response_order and "39=8" not in response_order:
            execution_price = parse_price_from_response(response_order)
            if not execution_price and is_entry: execution_price = price_for_sl
            loguj_aktivitu(f"✅ OK. Cena: {execution_price}")
            
            # SL/TP (jen pro Entry)
            if is_entry and execution_price > 0:
                sl_mult = (1 - SL_PCT) if side.upper() == "BUY" else (1 + SL_PCT)
                tp_mult = (1 + TP_PCT) if side.upper() == "BUY" else (1 - TP_PCT)
                sl_price = round(execution_price * sl_mult, 2)
                tp_price = round(execution_price * tp_mult, 2)
                sl_side = "2" if side.upper() == "BUY" else "1"
                
                # SL
                sl_tags = {49: SENDER_COMP_ID, 56: TARGET_COMP_ID, 50: "TRADE", 57: "TRADE", 34: 3, 52: get_utc_timestamp(), 11: f"SL_{int(time.time())}", 55: FIX_SYMBOL_ID, 54: sl_side, 38: volume_str, 40: "3", 99: f"{sl_price:.2f}", 59: "0", 60: get_utc_timestamp()}
                ssock.sendall(create_fix_msg("D", sl_tags))
                time.sleep(0.5)
                # TP
                tp_tags = {49: SENDER_COMP_ID, 56: TARGET_COMP_ID, 50: "TRADE", 57: "TRADE", 34: 4, 52: get_utc_timestamp(), 11: f"TP_{int(time.time())}", 55: FIX_SYMBOL_ID, 54: sl_side, 38: volume_str, 40: "2", 44: f"{tp_price:.2f}", 59: "0", 60: get_utc_timestamp()}
                ssock.sendall(create_fix_msg("D", tp_tags))
                loguj_aktivitu(f"-> SL: {sl_price}, TP: {tp_price}")
            
            ssock.close()
            return True
        else:
            loguj_aktivitu(f"❌ CHYBA: {parse_error_reason(response_order)}")
            ssock.close()
            return False
            
    except Exception as e:
        loguj_aktivitu(f"❌ SOCKET CHYBA: {e}")
        return False

# ==========================================
# HLAVNÍ LOGIKA
# ==========================================

def run_btc_logic():
    loguj_aktivitu("--- START BTC ANALÝZY ---")
    
    # 1. Data
    try:
        df_raw = yf.download(SYMBOL_YF, period='720d', interval='1h', auto_adjust=True, progress=False)
        if isinstance(df_raw.columns, pd.MultiIndex):
            df_raw.columns = df_raw.columns.get_level_values(0)
        df = df_raw.copy()
        df.dropna(inplace=True)
    except Exception as e:
        loguj_aktivitu(f"Chyba dat: {e}")
        return

    # 2. Indikátory pro AI
    df['RSI'] = ta.rsi(df['Close'], length=7)
    macd = ta.macd(df['Close'], fast=8, slow=21, signal=5)
    macd_h_col = [c for c in macd.columns if 'h' in c.lower()][0]
    df['MACD_H'] = macd[macd_h_col]
    df.dropna(inplace=True)

    # 3. AI Trénink (Dynamický - posledních 9 měsíců)
    end_date = df.index[-1]
    start_date = end_date - pd.Timedelta(days=270)
    print(f"Trénuji model na datech od: {start_date.strftime('%Y-%m-%d')} do: {end_date.strftime('%Y-%m-%d')}")
    
    train_df = df[(df.index >= start_date) & (df.index < end_date)].copy()
    
    # Targets
    train_df['Target_L'] = np.where(train_df['Close'].shift(-2) > train_df['Close'] * 1.003, 1, 0)
    train_df['Target_S'] = np.where(train_df['Close'].shift(-2) < train_df['Close'] * 0.997, 1, 0)
    
    features = ['RSI', 'MACD_H']
    
    model_l = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42).fit(train_df[features], train_df['Target_L'])
    model_s = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42).fit(train_df[features], train_df['Target_S'])

    # 4. Predikce
    last_row = df.iloc[[-1]].copy()
    prob_l = model_l.predict_proba(last_row[features])[:, 1][0]
    prob_s = model_s.predict_proba(last_row[features])[:, 1][0]
    current_price = last_row['Close'].iloc[0]
    
    # Signál
    signal_now = 0
    if prob_l > 0.51: signal_now = 1
    elif prob_s > 0.51: signal_now = -1
    
    loguj_aktivitu(f"AI Predikce: L={prob_l:.2f}, S={prob_s:.2f} -> Signál: {signal_now}")

    # 5. STATE MACHINE
    aktualni_pozice = get_saved_position()
    loguj_aktivitu(f"Držím: {aktualni_pozice}")
    
    if signal_now == 1: # CHCEME LONG
        if aktualni_pozice == "SELL":
            loguj_aktivitu("🔄 OTOČKA: Zavírám SHORT -> LONG")
            odeslat_fix_prikaz("BUY", VOLUME_TO_TRADE, is_entry=False) # Close Short
            time.sleep(2)
            if odeslat_fix_prikaz("BUY", VOLUME_TO_TRADE, is_entry=True, price_for_sl=current_price):
                save_position("BUY")
        elif aktualni_pozice == "NONE":
            loguj_aktivitu("🟢 NOVÝ: Otevírám LONG")
            if odeslat_fix_prikaz("BUY", VOLUME_TO_TRADE, is_entry=True, price_for_sl=current_price):
                save_position("BUY")
        elif aktualni_pozice == "BUY":
            loguj_aktivitu("☕ Ponechávám LONG.")

    elif signal_now == -1: # CHCEME SHORT
        if aktualni_pozice == "BUY":
            loguj_aktivitu("🔄 OTOČKA: Zavírám LONG -> SHORT")
            odeslat_fix_prikaz("SELL", VOLUME_TO_TRADE, is_entry=False) # Close Long
            time.sleep(2)
            if odeslat_fix_prikaz("SELL", VOLUME_TO_TRADE, is_entry=True, price_for_sl=current_price):
                save_position("SELL")
        elif aktualni_pozice == "NONE":
            loguj_aktivitu("🔴 NOVÝ: Otevírám SHORT")
            if odeslat_fix_prikaz("SELL", VOLUME_TO_TRADE, is_entry=True, price_for_sl=current_price):
                save_position("SELL")
        elif aktualni_pozice == "SELL":
            loguj_aktivitu("☕ Ponechávám SHORT.")
            
    else: # SIGNÁL 0 (NEUTRÁL)
        loguj_aktivitu("⚪ Žádný silný signál.")

if __name__ == "__main__":
    run_btc_logic()
                
