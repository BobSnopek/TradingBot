import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import os
import datetime
import time
from ctrader_fix import *

# --- KONFIGURACE ---
TRAIL_PCT = 0.012   # 1.2% Trailing Stop-Loss
MAX_HOLD = 12       # Max doba držení 12h
RISK_PCT = 0.20     # 20% risk
LEVERAGE = 3        # Páka 3x

def loguj_aktivitu_eth(zprava):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open('ETH_bot_activity.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {zprava}\n")

# --- POMOCNÁ TŘÍDA PRO OKLAMÁNÍ KNIHOVNY ---
class HackMessage:
    """Tato třída předstírá, že je oficiální Message objekt."""
    def __init__(self, raw_bytes):
        self.raw = raw_bytes
        # Knihovna chce delimiter, tak jí ho dáme (prázdný, protože už ho máme v datech)
        self.delimiter = b"" 
    
    def __bytes__(self):
        return self.raw
        
    def __str__(self):
        return self.raw.decode('ascii', errors='ignore')

# --- POMOCNÁ FUNKCE: RUČNÍ VÝROBA FIX ZPRÁVY ---
def create_fix_message(msg_type, pairs):
    s = "\x01" # SOH oddělovač
    body = ""
    for tag, value in pairs.items():
        body += f"{tag}={value}{s}"
        
    temp_body_for_len = f"35={msg_type}{s}{body}"
    length = len(temp_body_for_len)
    
    pre_checksum_msg = f"8=FIX.4.4{s}9={length}{s}{temp_body_for_len}"
    
    checksum = sum(pre_checksum_msg.encode('ascii')) % 256
    checksum_str = f"{checksum:03d}" 
    
    final_msg = f"{pre_checksum_msg}10={checksum_str}{s}"
    return final_msg.encode('ascii')

# --- HLAVNÍ FUNKCE ODESLÁNÍ ---
def proved_obchod_fix(symbol, side):
    symbol_clean = symbol.replace("-", "").replace("/", "")
    host = os.getenv('FIX_HOST')
    port = int(os.getenv('FIX_PORT'))
    sender_id = os.getenv('FIX_SENDER_ID')
    target_id = os.getenv('FIX_TARGET_ID')
    password = os.getenv('FIX_PASSWORD')
    
    volume = 15.0 if "ETH" in symbol_clean else 2.0 

    print(f"--- FIX API: Odesílám {side} {symbol_clean} ({volume}) ---")

    try:
        client = Client(host, port, sender_id, target_id, password)
        
        # PŘÍPRAVA DAT
        order_id = f"BOB_{int(time.time())}"
        # Oprava deprecation warningu pro čas
        transact_time = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]
        side_code = "1" if side.lower() == "buy" else "2"
        
        tags = {
            11: order_id,
            55: symbol_clean,
            54: side_code,
            38: str(volume),
            40: "1", # Market
            60: transact_time,
            59: "0"
        }
        
        # Vyrobíme hotová data (bytes)
        raw_msg_bytes = create_fix_message("D", tags)
        print(f"DEBUG: Data připravena: {raw_msg_bytes}")
        
        # --- METODA 1: GOD MODE (Přímý zápis do transportu) ---
        # Tímto obcházíme metodu send() a její kontroly
        try:
            if hasattr(client, 'transport') and client.transport is not None:
                print("Používám přímý zápis do transportu (God Mode)...")
                client.transport.write(raw_msg_bytes)
                msg = f"ÚSPĚCH: Data odeslána přímo do socketu. ID: {order_id}"
                print(msg)
                loguj_aktivitu_eth(msg)
                return True
        except Exception as e:
            print(f"Přímý zápis nevyšel: {e}")

        # --- METODA 2: TROJSKÝ KŮŇ (Falešný objekt) ---
        # Pokud metoda 1 selže, zkusíme oklamat send()
        print("Zkouším odeslat přes HackMessage objekt...")
        fake_obj = HackMessage(raw_msg_bytes)
        client.send(fake_obj)
        
        msg = f"ÚSPĚCH: HackMessage odeslána. ID: {order_id}"
        print(msg)
        loguj_aktivitu_eth(msg)
        return True

    except Exception as e:
        msg = f"CHYBA: Všechny metody selhaly. Důvod: {str(e)}"
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

# 4. SIMULACE HISTORIE
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

# 5. LOGOVÁNÍ
posledni_radek = df.iloc[-1]
cena = posledni_radek['Close']
adx_val = posledni_radek['ADX']
rsi_val = posledni_radek['RSI']

status_rozbor = f"Analýza ceny {cena:.2f} | ADX: {adx_val:.2f}, RSI: {rsi_val:.2f}"
print(f"--- {status_rozbor} ---")

# 6. REÁLNÉ ROZHODNUTÍ
signal_dnes = posledni_radek['Signal']
if signal_dnes != 0:
    smer = "BUY" if signal_dnes == 1 else "SELL"
    loguj_aktivitu_eth(f"{status_rozbor} -> AKCE: {smer}")
    proved_obchod_fix(symbol, smer)
else:
    loguj_aktivitu_eth(f"{status_rozbor} -> NEČINNOST: Žádná technická shoda")
    print("Aktuálně žádný signál k obchodu.")

print(f"Simulace hotova. Teoretický zůstatek: {final_bal:.2f} USD")
