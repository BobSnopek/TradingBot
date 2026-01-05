import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import os
import datetime
import time
import ssl
import socket

# ==========================================
# KONFIGURACE BOTA
# ==========================================
SYMBOL_YF = 'ETH-USD'   # Symbol pro stažení dat
FIX_SYMBOL_ID = "323"   # ID symbolu v cTraderu (ETH)

# Risk Management
SL_PCT = 0.015  # Stop Loss 1.5%
TP_PCT = 0.030  # Take Profit 3.0%
VOLUME_TO_TRADE = 1  # 1 = 1 jednotka (např. 1 ETH). Pro test bezpečné.

# Soubor pro uložení stavu (ABYCHOM VĚDĚLI, CO DRŽÍME)
POS_FILE = 'ETH_position.txt' 
ACTIVITY_FILE = 'ETH_bot_activity.txt'

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
    """Zapíše zprávu do logu i do konzole."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{timestamp}] {zprava}"
    print(full_msg)
    with open(ACTIVITY_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{full_msg}\n")

def get_saved_position():
    """Přečte, jakou pozici si bot myslí, že drží (BUY, SELL, NONE)."""
    if os.path.exists(POS_FILE):
        with open(POS_FILE, 'r') as f:
            return f.read().strip()
    return "NONE"

def save_position(side):
    """Uloží aktuální pozici do souboru."""
    with open(POS_FILE, 'w') as f:
        f.write(side)

def get_utc_timestamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

# --- FIX PROTOKOL FUNKCE ---

def create_fix_msg(msg_type, tags_dict):
    """Sestaví zprávu ve formátu FIX."""
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
    try:
        if "58=" in response:
            parts = response.split("\x01")
            for p in parts:
                if p.startswith("58="):
                    return p.split("=")[1]
    except:
        return "Neznámá chyba"
    return ""

def odeslat_fix_prikaz(side, volume, is_entry=True, price_for_sl=0):
    """
    Univerzální funkce pro odeslání příkazu.
    side: "BUY" nebo "SELL"
    volume: Množství
    is_entry: True = Otevíráme pozici (nastavíme SL/TP), False = Zavíráme (jen market order)
    """
    loguj_aktivitu(f"Odesílám FIX příkaz: {side} {volume} (Entry: {is_entry})")
    
    try:
        context = ssl.create_default_context()
        sock = socket.create_connection((FIX_HOST, FIX_PORT))
        ssock = context.wrap_socket(sock, server_hostname=FIX_HOST)
        
        # 1. LOGON
        logon_tags = {
            49: SENDER_COMP_ID, 56: TARGET_COMP_ID, 50: "TRADE", 57: "TRADE",
            34: 1, 52: get_utc_timestamp(),
            98: "0", 108: "30", 553: USERNAME, 554: PASSWORD, 141: "Y"
        }
        ssock.sendall(create_fix_msg("A", logon_tags))
        response = ssock.recv(4096).decode('ascii', errors='ignore')
        
        if "35=A" not in response:
            loguj_aktivitu(f"CHYBA: Logon selhal. {response}")
            return False

        # 2. MARKET ORDER (New Order Single)
        order_id = f"ORD_{int(time.time())}"
        side_code = "1" if side.upper() == "BUY" else "2"
        
        order_tags = {
            49: SENDER_COMP_ID, 56: TARGET_COMP_ID, 50: "TRADE", 57: "TRADE",
            34: 2, 52: get_utc_timestamp(),
            11: order_id,
            55: FIX_SYMBOL_ID,
            54: side_code,
            38: str(int(volume)),
            40: "1", # Market
            59: "0", # Day
            60: get_utc_timestamp()
        }
        
        ssock.sendall(create_fix_msg("D", order_tags))
        time.sleep(1.0)
        response_order = ssock.recv(4096).decode('ascii', errors='ignore')
        
        # Kontrola
        execution_price = 0.0
        if "35=8" in response_order and "39=8" not in response_order:
            execution_price = parse_price_from_response(response_order)
            if not execution_price and is_entry: execution_price = price_for_sl # Fallback
            
            loguj_aktivitu(f"✅ PŘÍKAZ SPLNĚN. Cena: {execution_price}")
            
            # 3. NASTAVENÍ OCHRANY (Jen pokud otevíráme novou pozici)
            if is_entry and execution_price > 0:
                if side.upper() == "BUY":
                    sl_price = round(execution_price * (1 - SL_PCT), 2)
                    tp_price = round(execution_price * (1 + TP_PCT), 2)
                    sl_side = "2" # Sell
                else:
                    sl_price = round(execution_price * (1 + SL_PCT), 2)
                    tp_price = round(execution_price * (1 - TP_PCT), 2)
                    sl_side = "1" # Buy

                # Poslat SL
                sl_tags = {
                    49: SENDER_COMP_ID, 56: TARGET_COMP_ID, 50: "TRADE", 57: "TRADE",
                    34: 3, 52: get_utc_timestamp(),
                    11: f"SL_{int(time.time())}", 55: FIX_SYMBOL_ID, 54: sl_side,
                    38: str(int(volume)), 40: "3", 99: f"{sl_price:.2f}",
                    59: "0", 60: get_utc_timestamp()
                }
                ssock.sendall(create_fix_msg("D", sl_tags))
                time.sleep(0.5)
                
                # Poslat TP
                tp_tags = {
                    49: SENDER_COMP_ID, 56: TARGET_COMP_ID, 50: "TRADE", 57: "TRADE",
                    34: 4, 52: get_utc_timestamp(),
                    11: f"TP_{int(time.time())}", 55: FIX_SYMBOL_ID, 54: sl_side, # Stejná strana jako SL
                    38: str(int(volume)), 40: "2", 44: f"{tp_price:.2f}",
                    59: "0", 60: get_utc_timestamp()
                }
                ssock.sendall(create_fix_msg("D", tp_tags))
                loguj_aktivitu(f"-> Ochrana odeslána (SL: {sl_price}, TP: {tp_price})")
            
            ssock.close()
            return True
            
        else:
            err = parse_error_reason(response_order)
            loguj_aktivitu(f"❌ CHYBA PŘÍKAZU: {err}")
            ssock.close()
            return False

    except Exception as e:
        loguj_aktivitu(f"❌ KRITICKÁ CHYBA SOCKETU: {e}")
        return False

# ==========================================
# HLAVNÍ LOGIKA
# ==========================================

def run_analysis_and_trade():
    loguj_aktivitu("--- START ANALÝZY ---")
    
    # 1. STÁHNOUT DATA
    try:
        df = yf.download(SYMBOL_YF, period='60d', interval='1h', auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
    except Exception as e:
        loguj_aktivitu(f"Chyba stahování dat: {e}")
        return

    # 2. VÝPOČET INDIKÁTORŮ
    df['EMA_FAST'] = ta.ema(df['Close'], length=12)
    df['EMA_SLOW'] = ta.ema(df['Close'], length=26)
    df['RSI'] = ta.rsi(df['Close'], length=14)
    adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
    df['ADX'] = adx_df.iloc[:, 0]; df['DMP'] = adx_df.iloc[:, 1]; df['DMN'] = adx.iloc[:, 2]

    # 3. SIGNÁL (Poslední uzavřená svíčka)
    last_idx = -1 
    row = df.iloc[last_idx]
    
    adx = row['ADX']
    dmp = row['DMP']
    dmn = row['DMN']
    e_fast = row['EMA_FAST']
    e_slow = row['EMA_SLOW']
    rsi = row['RSI']
    current_price = row['Close']
    
    # Logika signálu
    signal_now = 0
    # Trend Long
    if (adx > 15) and (dmp > dmn) and (e_fast > e_slow): signal_now = 1
    # Trend Short
    elif (adx > 15) and (dmn > dmp) and (e_fast < e_slow): signal_now = -1
    # Mean Reversion Long (Dip)
    elif (adx <= 30) and (e_fast < e_slow) and (rsi < 40): signal_now = 1
    
    loguj_aktivitu(f"Data: Cena={current_price:.2f}, ADX={adx:.1f}, RSI={rsi:.1f}")
    loguj_aktivitu(f"Vypočtený signál: {signal_now}")

    # 4. ŘÍZENÍ POZIC (STATE MACHINE)
    aktualni_pozice = get_saved_position()
    loguj_aktivitu(f"Aktuálně držím: {aktualni_pozice}")
    
    # --- LOGIKA PŘEPÍNÁNÍ ---
    
    if signal_now == 1: # Chceme LONG
        if aktualni_pozice == "SELL":
            loguj_aktivitu("🔄 OTOČKA: Zavírám SHORT -> Otevírám LONG")
            # 1. Zavřít Short (Koupit)
            odeslat_fix_prikaz("BUY", VOLUME_TO_TRADE, is_entry=False)
            time.sleep(2)
            # 2. Otevřít Long (Koupit)
            ok = odeslat_fix_prikaz("BUY", VOLUME_TO_TRADE, is_entry=True, price_for_sl=current_price)
            if ok: save_position("BUY")
            
        elif aktualni_pozice == "NONE":
            loguj_aktivitu("🟢 NOVÝ: Otevírám LONG")
            ok = odeslat_fix_prikaz("BUY", VOLUME_TO_TRADE, is_entry=True, price_for_sl=current_price)
            if ok: save_position("BUY")
            
        elif aktualni_pozice == "BUY":
            loguj_aktivitu("☕ Ponechávám LONG (nic nedělám).")

    elif signal_now == -1: # Chceme SHORT
        if aktualni_pozice == "BUY":
            loguj_aktivitu("🔄 OTOČKA: Zavírám LONG -> Otevírám SHORT")
            # 1. Zavřít Long (Prodat)
            odeslat_fix_prikaz("SELL", VOLUME_TO_TRADE, is_entry=False)
            time.sleep(2)
            # 2. Otevřít Short (Prodat)
            ok = odeslat_fix_prikaz("SELL", VOLUME_TO_TRADE, is_entry=True, price_for_sl=current_price)
            if ok: save_position("SELL")
            
        elif aktualni_pozice == "NONE":
            loguj_aktivitu("🔴 NOVÝ: Otevírám SHORT")
            ok = odeslat_fix_prikaz("SELL", VOLUME_TO_TRADE, is_entry=True, price_for_sl=current_price)
            if ok: save_position("SELL")
            
        elif aktualni_pozice == "SELL":
            loguj_aktivitu("☕ Ponechávám SHORT (nic nedělám).")
            
    else: # Signál 0 (Neutrál)
        # Volitelně: Pokud chceš zavřít pozici, když zmizí signál:
        # if aktualni_pozice == "BUY": odeslat_fix_prikaz("SELL", ...); save_position("NONE")
        # if aktualni_pozice == "SELL": odeslat_fix_prikaz("BUY", ...); save_position("NONE")
        loguj_aktivitu("⚪ Žádný signál. Držím stávající stav.")

if __name__ == "__main__":
    # Spustí se jednou. Pro smyčku lze dát do while True s sleep(3600)
    run_analysis_and_trade()
                return 0
    return 0

def save_last_signal(sig):
    """Uloží aktuální signál, abychom ho příště neopakovali."""
    with open(STATE_FILE, 'w') as f:
        f.write(str(sig))

def create_fix_msg(msg_type, tags_dict):
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
    # --- ÚDAJE ---
    host = "live-uk-eqx-01.p.c-trader.com"
    port = 5212
    sender_comp_id = "live.ftmo.17032147"
    target_comp_id = "cServer"
    password = "CTrader2026"
    username = "17032147"
    
    fix_symbol_id = "323" # ETH
    
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
        time.sleep(1.0) # Bezpečná pauza
        response_order = ssock.recv(4096).decode('ascii', errors='ignore')
        
        # Kontrola Execution Report (35=8) a zda není Rejected (39=8)
        if "35=8" in response_order and "39=8" not in response_order:
            filled_price = parse_price_from_response(response_order)
            final_price = filled_price if filled_price else current_market_price
            
            source_msg = "(ze serveru)" if filled_price else "(z grafu - záloha)"
            msg = f"ÚSPĚCH: Otevřeno za {final_price:.2f} {source_msg}"
            print(msg)
            loguj_aktivitu_eth(msg)
            
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

            loguj_aktivitu_eth(f"Ochrana odeslána: SL {sl_price}, TP {tp_price}")
            return True
        else:
            err = parse_error_reason(response_order)
            print(f"CHYBA PŘÍKAZU (Vstup): {err}")
            return False

        ssock.close()

    except Exception as e:
        print(f"CHYBA SOCKETU: {e}")
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

# 4. SIMULACE (Volitelná - jen pro info do souboru)
def run_trailing_sim_eth(data):
    balance = 1000.0
    MAX_HOLD = 12
    TRAIL_PCT = 0.012
    with open('vypis_obchodu_ETH_TSL.txt', 'w', encoding='utf-8') as f:
        f.write("DATUM | TYP | VSTUP | VYSTUP (TSL) | ZISK USD | BALANCE\n")
        f.write("-" * 75 + "\n")
        for i in range(1, len(data) - MAX_HOLD):
            sig = data['Signal'].iloc[i]
            if sig != 0 and sig != data['Signal'].iloc[i-1]:
                entry = data['Close'].iloc[i]
                res = 0
                if sig == 1:
                    peak = entry
                    for h in range(1, MAX_HOLD + 1):
                        curr_p = data['Close'].iloc[i+h]
                        peak = max(peak, data['High'].iloc[i+h])
                        sl = peak * (1 - TRAIL_PCT)
                        if curr_p < sl:
                            res = (sl - entry) / entry
                            break
                        res = (curr_p - entry) / entry
                else:
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

# 5. EXECUTION (S KONTROLOU DUPLICIT)
posledni_radek = df.iloc[-1]
signal_dnes = int(posledni_radek['Signal'])
aktualni_cena = posledni_radek['Close']
last_signal = get_last_signal()

print(f"--- Analýza {symbol} ---")
print(f"Signál dnes: {signal_dnes} | Poslední uložený signál: {last_signal}")

if signal_dnes != 0:
    # Obchodujeme pouze pokud se signál změnil oproti minule
    if signal_dnes != last_signal:
        smer = "BUY" if signal_dnes == 1 else "SELL"
        loguj_aktivitu_eth(f"NOVÝ SIGNÁL: {smer} (Změna z {last_signal})")
        
        uspech = proved_obchod_a_zajisti(symbol, smer, aktualni_cena)
        
        if uspech:
            save_last_signal(signal_dnes)
            print("Stav signálu aktualizován v souboru.")
    else:
        print("IGNORUJI: Tento signál už jsme zobchodovali (Anti-Stacking).")
        loguj_aktivitu_eth(f"IGNOROVÁNO: Signál {signal_dnes} se nezměnil.")
else:
    # Pokud je signál 0 (neutrální), uložíme 0, abychom byli připraveni na nový nákup
    if last_signal != 0:
        save_last_signal(0)
        print("Signál zmizel -> Resetuji stav na 0.")
    
    loguj_aktivitu_eth("NEČINNOST")
    print("Žádný signál.")

print(f"Simulace hotova.")
