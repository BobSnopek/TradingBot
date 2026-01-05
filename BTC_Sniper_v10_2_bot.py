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
# KONFIGURACE (BTC BOT)
# ==========================================
SYMBOL_YF = 'BTC-USD'
FIX_SYMBOL_ID = "324"   # ID pro BTC v cTraderu (zkontroluj v platformě)

# Risk Management
SL_PCT = 0.015  # 1.5%
TP_PCT = 0.030  # 3.0%
RISK_PCT = 0.25
VOLUME_TO_TRADE = 1  # 1 unit (bezpečné)

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

def odeslat_fix_prikaz(side, volume, is_entry=True, price_for_sl=0):
    """
    Odesílá příkaz přes FIX.
    side: "BUY" nebo "SELL"
    is_entry: True = Otevíráme (nastavíme SL/TP), False = Zavíráme (jen Market)
    """
    loguj_aktivitu(f"FIX: {side} {volume} (Entry: {is_entry})")
    
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
            11: order_id, 55: FIX_SYMBOL_ID, 54: side_code, 38: str(int(volume)), 40: "1", 59: "0", 60: get_utc_timestamp()
        }
        ssock.sendall(create_fix_msg("D", order_tags))
        time.sleep(1.0)
        response_order = ssock.recv(4096).decode('ascii', errors='ignore')
        
        execution_price = 0.0
        if "35=8" in response_order and "39=8" not in response_order:
            execution_price = parse_price_from_response(response_order)
            if not execution_price and is_entry: execution_price = price_for_sl
            loguj_aktivitu(f"✅ OK. Cena: {execution_price}")
            
            # Ochrana (SL/TP) - jen pro vstup
            if is_entry and execution_price > 0:
                sl_mult = (1 - SL_PCT) if side.upper() == "BUY" else (1 + SL_PCT)
                tp_mult = (1 + TP_PCT) if side.upper() == "BUY" else (1 - TP_PCT)
                sl_price = round(execution_price * sl_mult, 2)
                tp_price = round(execution_price * tp_mult, 2)
                sl_side = "2" if side.upper() == "BUY" else "1"
                
                # SL
                sl_tags = {49: SENDER_COMP_ID, 56: TARGET_COMP_ID, 50: "TRADE", 57: "TRADE", 34: 3, 52: get_utc_timestamp(), 11: f"SL_{int(time.time())}", 55: FIX_SYMBOL_ID, 54: sl_side, 38: str(int(volume)), 40: "3", 99: f"{sl_price:.2f}", 59: "0", 60: get_utc_timestamp()}
                ssock.sendall(create_fix_msg("D", sl_tags))
                time.sleep(0.5)
                # TP
                tp_tags = {49: SENDER_COMP_ID, 56: TARGET_COMP_ID, 50: "TRADE", 57: "TRADE", 34: 4, 52: get_utc_timestamp(), 11: f"TP_{int(time.time())}", 55: FIX_SYMBOL_ID, 54: sl_side, 38: str(int(volume)), 40: "2", 44: f"{tp_price:.2f}", 59: "0", 60: get_utc_timestamp()}
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
# HLAVNÍ LOGIKA (AI + EXECUTION)
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
    train_df = df[(df.index >= start_date) & (df.index < end_date)].copy()
    
    # Targets
    train_df['Target_L'] = np.where(train_df['Close'].shift(-2) > train_df['Close'] * 1.003, 1, 0)
    train_df['Target_S'] = np.where(train_df['Close'].shift(-2) < train_df['Close'] * 0.997, 1, 0)
    
    features = ['RSI', 'MACD_H']
    
    model_l = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42).fit(train_df[features], train_df['Target_L'])
    model_s = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42).fit(train_df[features], train_df['Target_S'])

    # 4. Predikce (Aktuální svíčka)
    last_row = df.iloc[[-1]].copy() # Jako DataFrame pro predict
    prob_l = model_l.predict_proba(last_row[features])[:, 1][0]
    prob_s = model_s.predict_proba(last_row[features])[:, 1][0]
    
    current_price = last_row['Close'].iloc[0]
    
    # Signál
    signal_now = 0
    if prob_l > 0.51: signal_now = 1
    elif prob_s > 0.51: signal_now = -1
    
    loguj_aktivitu(f"AI Predikce: L={prob_l:.2f}, S={prob_s:.2f} -> Signál: {signal_now}")

    # 5. ŘÍZENÍ POZIC (STATE MACHINE)
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
        # Volitelně: Můžeš přidat logiku pro zavření, pokud je signál slabý
        # if aktualni_pozice != "NONE":
        #    ... zavřít ...
        #    save_position("NONE")

if __name__ == "__main__":
    run_btc_logic()
def save_last_signal(sig):
    """Uloží aktuální signál."""
    with open(STATE_FILE, 'w') as f:
        f.write(str(sig))

def create_fix_msg(msg_type, tags_dict):
    """Sestaví FIX zprávu."""
    s = "\x01"
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

def parse_price_from_response(response):
    """Vytáhne cenu (Tag 6) z odpovědi."""
    try:
        if "6=" in response:
            parts = response.split("\x01")
            for p in parts:
                if p.startswith("6="):
                    return float(p.split("=")[1])
    except:
        return None
    return None

def parse_error_reason(response):
    """Vytáhne chybovou hlášku (Tag 58)."""
    try:
        if "58=" in response:
            parts = response.split("\x01")
            for p in parts:
                if p.startswith("58="):
                    return p.split("=")[1]
    except:
        return "Neznámá chyba"
    return ""

def get_utc_timestamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

def proved_obchod_a_zajisti(symbol, side, current_market_price):
    # --- PŘIHLAŠOVACÍ ÚDAJE ---
    host = "live-uk-eqx-01.p.c-trader.com"
    port = 5212
    sender_comp_id = "live.ftmo.17032147"
    target_comp_id = "cServer"
    password = "CTrader2026"
    username = "17032147"
    
    fix_symbol_id = "324" # BTC
    
    print(f"--- FIX SOCKET: Odesílám {side} pro ID {fix_symbol_id} (Vol: {VOLUME_TO_TRADE}) ---")
    
    try:
        context = ssl.create_default_context()
        sock = socket.create_connection((host, port))
        ssock = context.wrap_socket(sock, server_hostname=host)
        
        # 1. LOGON
        logon_tags = {
            49: sender_comp_id, 56: target_comp_id, 50: "TRADE", 57: "TRADE",
            34: 1, 52: get_utc_timestamp(),
            98: "0", 108: "30", 553: username, 554: password, 141: "Y"
        }
        ssock.sendall(create_fix_msg("A", logon_tags))
        response = ssock.recv(4096).decode('ascii', errors='ignore')
        
        if "35=A" not in response:
            print(f"CHYBA: Logon selhal. Odpověď: {response}")
            return False

        # 2. MARKET ORDER
        order_id = f"BOB_{int(time.time())}"
        side_code = "1" if side.lower() == "buy" else "2"
        
        order_tags = {
            49: sender_comp_id, 56: target_comp_id, 50: "TRADE", 57: "TRADE",
            34: 2, 52: get_utc_timestamp(),
            11: order_id,
            55: fix_symbol_id,
            54: side_code,
            38: str(int(VOLUME_TO_TRADE)),
            40: "1", 59: "0", 60: get_utc_timestamp()
        }
        
        ssock.sendall(create_fix_msg("D", order_tags))
        time.sleep(1.0)
        response_order = ssock.recv(4096).decode('ascii', errors='ignore')
        
        if "35=8" in response_order and "39=8" not in response_order:
            filled_price = parse_price_from_response(response_order)
            final_price = filled_price if filled_price else current_market_price
            
            source_msg = "(ze serveru)" if filled_price else "(z grafu - záloha)"
            msg = f"ÚSPĚCH: BTC Otevřeno za {final_price:.2f} {source_msg}"
            print(msg)
            loguj_aktivitu(msg)
            
            # 3. ZAJIŠTĚNÍ
            if side.lower() == "buy":
                sl_price = round(final_price * (1 - SL_PCT), 2)
                tp_price = round(final_price * (1 + TP_PCT), 2)
                sl_side = "2"
                tp_side = "2"
            else: 
                sl_price = round(final_price * (1 + SL_PCT), 2)
                tp_price = round(final_price * (1 - TP_PCT), 2)
                sl_side = "1"
                tp_side = "1"

            print(f"--- OCHRANA: SL={sl_price}, TP={tp_price} ---")
            
            # SL
            sl_tags = {
                49: sender_comp_id, 56: target_comp_id, 50: "TRADE", 57: "TRADE",
                34: 3, 52: get_utc_timestamp(),
                11: f"SL_{int(time.time())}", 55: fix_symbol_id, 54: sl_side,
                38: str(int(VOLUME_TO_TRADE)), 40: "3", 99: f"{sl_price:.2f}",
                59: "0", 60: get_utc_timestamp()
            }
            ssock.sendall(create_fix_msg("D", sl_tags))
            time.sleep(1.0)
            response_sl = ssock.recv(4096).decode('ascii', errors='ignore')
            
            if "35=8" in response_sl and "39=8" not in response_sl:
                print("-> SL nastaven OK")
            else:
                print(f"-> CHYBA SL: {parse_error_reason(response_sl)}")
            
            # TP
            tp_tags = {
                49: sender_comp_id, 56: target_comp_id, 50: "TRADE", 57: "TRADE",
                34: 4, 52: get_utc_timestamp(),
                11: f"TP_{int(time.time())}", 55: fix_symbol_id, 54: tp_side,
                38: str(int(VOLUME_TO_TRADE)), 40: "2", 44: f"{tp_price:.2f}",
                59: "0", 60: get_utc_timestamp()
            }
            ssock.sendall(create_fix_msg("D", tp_tags))
            time.sleep(1.0)
            response_tp = ssock.recv(4096).decode('ascii', errors='ignore')

            if "35=8" in response_tp and "39=8" not in response_tp:
                print("-> TP nastaven OK")
            else:
                print(f"-> CHYBA TP: {parse_error_reason(response_tp)}")

            loguj_aktivitu(f"Zajištěno SL: {sl_price}, TP: {tp_price}")
            return True
        else:
            err = parse_error_reason(response_order)
            print(f"CHYBA PŘÍKAZU (Vstup): {err}")
            return False

        ssock.close()

    except Exception as e:
        print(f"CHYBA SOCKETU: {e}")
        return False

# 1. DATA (LIVE ANALÝZA)
symbol = 'BTC-USD'
# Stahujeme 720 dní pro výpočet indikátorů
df_raw = yf.download(symbol, period='720d', interval='1h', auto_adjust=True)
if isinstance(df_raw.columns, pd.MultiIndex):
    df_raw.columns = df_raw.columns.get_level_values(0)
df = df_raw.copy()

# Indikátory
df['RSI'] = ta.rsi(df['Close'], length=7)
macd = ta.macd(df['Close'], fast=8, slow=21, signal=5)
macd_h_col = [c for c in macd.columns if 'h' in c.lower()][0]
df['MACD_H'] = macd[macd_h_col]

# 2. TRÉNINK (AI Model - KLOUZAVÉ OKNO 9 MĚSÍCŮ)
# Dynamický výpočet trénovacího období
end_date = datetime.datetime.now()
start_date = end_date - datetime.timedelta(days=270) # 9 měsíců zpětně

print(f"Trénuji model na datech od: {start_date.strftime('%Y-%m-%d')} do: {end_date.strftime('%Y-%m-%d')}")

train_data = yf.download(symbol, start=start_date, end=end_date, interval='1h', auto_adjust=True)
if isinstance(train_data.columns, pd.MultiIndex):
    train_data.columns = train_data.columns.get_level_values(0)
td = train_data.copy()

td['RSI'] = ta.rsi(td['Close'], length=7)
td_macd = ta.macd(td['Close'], fast=8, slow=21, signal=5)
td_macd_h_col = [c for c in td_macd.columns if 'h' in c.lower()][0]
td['MACD_H'] = td_macd[td_macd_h_col]

# Cíle (Targets)
td['Target_L'] = np.where(td['Close'].shift(-2) > td['Close'] * 1.003, 1, 0)
td['Target_S'] = np.where(td['Close'].shift(-2) < td['Close'] * 0.997, 1, 0)
td = td.dropna()

features = ['RSI', 'MACD_H']

# Trénink
model_l = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42).fit(td[features], td['Target_L'])
model_s = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42).fit(td[features], td['Target_S'])

# 3. PREDIKCE
df['Prob_L'] = model_l.predict_proba(df[features])[:, 1]
df['Prob_S'] = model_s.predict_proba(df[features])[:, 1]
df['Signal'] = 0
df.loc[df['Prob_L'] > 0.51, 'Signal'] = 1
df.loc[df['Prob_S'] > 0.51, 'Signal'] = -1

# 4. SIMULACE (Jen pro zápis do souboru)
def run_trailing_sim(data):
    balance = 1000.0
    MAX_HOLD = 8
    TRAIL_PCT = 0.008
    with open('vypis_obchodu_BTC_TSL.txt', 'w', encoding='utf-8') as f:
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
signal_dnes = int(posledni_radek['Signal'])
aktualni_cena = posledni_radek['Close']
last_signal = get_last_signal()

print(f"--- Analýza {symbol} ---")
print(f"Signál dnes: {signal_dnes} | Poslední uložený signál: {last_signal}")
print(f"AI Model -> Pravděpodobnost L: {posledni_radek['Prob_L']:.2f}, S: {posledni_radek['Prob_S']:.2f}")

if signal_dnes != 0:
    if signal_dnes != last_signal:
        smer = "BUY" if signal_dnes == 1 else "SELL"
        loguj_aktivitu(f"NOVÝ SIGNÁL: {smer} (L:{posledni_radek['Prob_L']:.2f}, S:{posledni_radek['Prob_S']:.2f})")
        
        uspech = proved_obchod_a_zajisti(symbol, smer, aktualni_cena)
        
        if uspech:
            save_last_signal(signal_dnes)
            print("Stav signálu aktualizován v souboru.")
    else:
        print("IGNORUJI: Tento signál už jsme zobchodovali (Anti-Stacking).")
        loguj_aktivitu(f"IGNOROVÁNO: Signál {signal_dnes} se nezměnil.")
else:
    if last_signal != 0:
        save_last_signal(0)
        print("Signál zmizel -> Resetuji stav na 0.")
    
    loguj_aktivitu("NEČINNOST")
    print("Žádný signál.")

print(f"Simulace hotova.")
