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

# --- KONFIGURACE OCHRANY A RISK MANAGEMENTU ---
SL_PCT = 0.015   # Stop Loss 1.5%
TP_PCT = 0.030   # Take Profit 3.0%
RISK_PCT = 0.25
LEVERAGE = 5     # Páka pro BTC

# --- KONFIGURACE OBCHODOVÁNÍ ---
VOLUME_TO_TRADE = 1          # Bezpečný objem pro testování
STATE_FILE = 'BTC_last_signal.txt' # Soubor pro paměť bota

def loguj_aktivitu(zprava):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open('BTC_bot_activity.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {zprava}\n")

def get_last_signal():
    """Přečte posledně provedený signál ze souboru."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                return int(f.read().strip())
            except:
                return 0
    return 0

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
