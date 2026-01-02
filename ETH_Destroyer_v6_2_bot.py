import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import os
import datetime
import time
import ssl
import socket

# --- KONFIGURACE OCHRANY ---
SL_PCT = 0.015  # Stop Loss 1.5% (Pokud cena klesne o 1.5%, prodáváme)
TP_PCT = 0.030  # Take Profit 3.0% (Pokud cena stoupne o 3%, vybíráme zisk)
RISK_PCT = 0.20
LEVERAGE = 3

def loguj_aktivitu_eth(zprava):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open('ETH_bot_activity.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {zprava}\n")

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
    """Vytáhne cenu (Tag 6 - AvgPx) z odpovědi serveru."""
    try:
        if "6=" in response:
            parts = response.split("\x01")
            for p in parts:
                if p.startswith("6="):
                    return float(p.split("=")[1])
    except:
        return None
    return None

def proved_obchod_a_zajisti(symbol, side):
    # --- ÚDAJE ---
    host = "live-uk-eqx-01.p.c-trader.com"
    port = 5212
    sender_comp_id = "live.ftmo.17032147"
    target_comp_id = "cServer"
    password = "CTrader2026"
    username = "17032147"
    
    # ID instrumentu (ETH = 323)
    fix_symbol_id = "323"
    volume = 15
    
    print(f"--- FIX SOCKET: Odesílám {side} pro ID {fix_symbol_id} ---")
    
    try:
        context = ssl.create_default_context()
        sock = socket.create_connection((host, port))
        ssock = context.wrap_socket(sock, server_hostname=host)
        
        # 1. LOGON
        logon_tags = {
            49: sender_comp_id, 56: target_comp_id, 50: "TRADE", 57: "TRADE",
            34: 1, 52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            98: "0", 108: "30", 553: username, 554: password, 141: "Y"
        }
        ssock.sendall(create_fix_msg("A", logon_tags))
        response = ssock.recv(4096).decode('ascii', errors='ignore')
        
        if "35=A" not in response:
            print("CHYBA: Logon selhal.")
            return False

        # 2. MARKET ORDER (Vstup do pozice)
        order_id = f"BOB_{int(time.time())}"
        side_code = "1" if side.lower() == "buy" else "2" # 1=Buy, 2=Sell
        
        order_tags = {
            49: sender_comp_id, 56: target_comp_id, 50: "TRADE", 57: "TRADE",
            34: 2, 52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            11: order_id,
            55: fix_symbol_id,
            54: side_code,
            38: str(int(volume)),
            40: "1",     # Market
            59: "0",     # Day
            60: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3]
        }
        
        ssock.sendall(create_fix_msg("D", order_tags))
        time.sleep(1)
        response_order = ssock.recv(4096).decode('ascii', errors='ignore')
        
        # Kontrola úspěchu
        if "35=8" in response_order and "39=8" not in response_order:
            entry_price = parse_price_from_response(response_order)
            msg = f"ÚSPĚCH: Nakoupeno za {entry_price if entry_price else 'Neznámo'}"
            print(msg)
            loguj_aktivitu_eth(msg)
            
            # 3. ZAJIŠTĚNÍ (STOP LOSS a TAKE PROFIT)
            if entry_price:
                # Výpočet cen
                if side.lower() == "buy":
                    sl_price = round(entry_price * (1 - SL_PCT), 2)
                    tp_price = round(entry_price * (1 + TP_PCT), 2)
                    sl_side = "2" # Sell Stop
                    tp_side = "2" # Sell Limit
                else: # Sell
                    sl_price = round(entry_price * (1 + SL_PCT), 2)
                    tp_price = round(entry_price * (1 - TP_PCT), 2)
                    sl_side = "1" # Buy Stop
                    tp_side = "1" # Buy Limit

                print(f"--- NASTAVUJI OCHRANU: SL={sl_price}, TP={tp_price} ---")
                
                # A) STOP LOSS ORDER (Type 3 = Stop Order)
                sl_tags = {
                    49: sender_comp_id, 56: target_comp_id, 50: "TRADE", 57: "TRADE",
                    34: 3, 52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
                    11: f"SL_{int(time.time())}",
                    55: fix_symbol_id,
                    54: sl_side,       # Opačný směr
                    38: str(int(volume)),
                    40: "3",           # STOP ORDER
                    99: str(sl_price), # Stop Price
                    59: "0", 60: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3]
                }
                ssock.sendall(create_fix_msg("D", sl_tags))
                time.sleep(0.5)
                
                # B) TAKE PROFIT ORDER (Type 2 = Limit Order)
                tp_tags = {
                    49: sender_comp_id, 56: target_comp_id, 50: "TRADE", 57: "TRADE",
                    34: 4, 52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
                    11: f"TP_{int(time.time())}",
                    55: fix_symbol_id,
                    54: tp_side,       # Opačný směr
                    38: str(int(volume)),
                    40: "2",           # LIMIT ORDER
                    44: str(tp_price), # Limit Price
                    59: "0", 60: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3]
                }
                ssock.sendall(create_fix_msg("D", tp_tags))
                print("Ochranné příkazy odeslány.")
                loguj_aktivitu_eth(f"Zajištěno SL: {sl_price}, TP: {tp_price}")

            return True
        else:
            print(f"CHYBA PŘÍKAZU: {response_order}")
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
    proved_obchod_a_zajisti(symbol, smer)
else:
    loguj_aktivitu_eth("NEČINNOST")
    print("Žádný signál.")
print(f"Simulace hotova.")
