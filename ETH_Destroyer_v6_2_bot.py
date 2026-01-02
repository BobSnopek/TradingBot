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
TRAIL_PCT = 0.012   # 1.2% Trailing Stop-Loss
MAX_HOLD = 12       # Max doba držení 12h
RISK_PCT = 0.20     # 20% risk
LEVERAGE = 3        # Páka 3x

def loguj_aktivitu_eth(zprava):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open('ETH_bot_activity.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {zprava}\n")

# --- GENERÁTOR FIX ZPRÁV ---
def create_fix_msg(msg_type, tags_dict):
    """Sestaví validní FIX zprávu včetně hlavičky a checksumu."""
    s = "\x01"
    body = ""
    for tag, val in tags_dict.items():
        body += f"{tag}={val}{s}"
    
    # Hlavička (35=MsgType)
    temp_head = f"35={msg_type}{s}{body}"
    length = len(temp_head)
    
    msg_str = f"8=FIX.4.4{s}9={length}{s}{temp_head}"
    
    # Checksum
    checksum = sum(msg_str.encode('ascii')) % 256
    msg_final = f"{msg_str}10={checksum:03d}{s}"
    return msg_final.encode('ascii')

# --- SYNCHRONNÍ ODESLÁNÍ PŘES SSL ---
def proved_obchod_fix(symbol, side):
    symbol_clean = symbol.replace("-", "").replace("/", "")
    host = os.getenv('FIX_HOST', 'h65.p.ctrader.com')
    port = int(os.getenv('FIX_PORT', 5212))
    sender_id = os.getenv('FIX_SENDER_ID')
    target_id = os.getenv('FIX_TARGET_ID')
    password = os.getenv('FIX_PASSWORD')
    
    # Nastavení objemu: Převedeme na int, aby to bylo "15" a ne "15.0"
    volume = 15
    if "BTC" in symbol_clean: volume = 2
    
    # cTrader často vyžaduje volume v základních jednotkách (units).
    # 1 Lot = 100,000 units (často).
    # Pokud ti to příkaz vezme jako 15 units (mikroskopické množství), musíme to příště vynásobit.
    # Zatím necháme raw 15, uvidíme, co řekne server.
    
    print(f"--- PŘÍMÝ FIX SOCKET: Odesílám {side} {symbol_clean} ---")
    
    try:
        # 1. PŘIPOJENÍ
        context = ssl.create_default_context()
        sock = socket.create_connection((host, port))
        ssock = context.wrap_socket(sock, server_hostname=host)
        print(f"DEBUG: Připojeno k {host}:{port}")

        # 2. LOGON (MsgType=A)
        # PŘIDÁNO: Tag 57 (TargetSubID) = TRADE
        logon_tags = {
            49: sender_id,
            56: target_id,
            57: "TRADE",    # <--- TOTO JE TA KLÍČOVÁ OPRAVA!
            50: "QUOTE",    # SenderSubID (často vyžadováno jako pár k TargetSubID)
            34: 1,
            52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            98: "0",
            108: "30",
            553: sender_id,
            554: password,
            141: "Y"
        }
        logon_msg = create_fix_msg("A", logon_tags)
        ssock.sendall(logon_msg)
        
        # Čekání na Logon
        response = ssock.recv(4096).decode('ascii', errors='ignore')
        if "35=A" in response and "10=" in response:
            print("DEBUG: Logon úspěšný! Server odpověděl 35=A.")
        else:
            print(f"VAROVÁNÍ: Logon odpověď je divná, ale zkouším pokračovat: {response}")
            # Pokud tam je chyba "Logout", nemá smysl pokračovat, ale zkusíme to pro debug.

        # 3. NEW ORDER SINGLE (MsgType=D)
        order_id = f"BOB_{int(time.time())}"
        side_code = "1" if side.lower() == "buy" else "2"
        
        order_tags = {
            49: sender_id,
            56: target_id,
            57: "TRADE",  # Musí být i v hlavičce objednávky
            50: "QUOTE",
            34: 2,
            52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            11: order_id,
            55: symbol_clean,
            54: side_code,
            38: str(int(volume)), # Posíláme jako string bez desetinných míst
            40: "1",         # Market Order
            59: "0",         # Day
            60: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3]
        }
        
        order_msg = create_fix_msg("D", order_tags)
        print(f"DEBUG: Odesílám příkaz...")
        ssock.sendall(order_msg)
        
        # 4. ČEKÁNÍ NA POTVRZENÍ
        time.sleep(2) # Dáme tomu chvilku
        response_order = ssock.recv(4096).decode('ascii', errors='ignore')
        
        ssock.close()
        
        # Hledáme tag 35=8 (Execution Report)
        if "35=8" in response_order: 
            # Ještě zkontrolujeme tag 150 (ExecType) nebo 39 (OrdStatus)
            # 39=8 znamená Rejected. 39=0 znamená New/Accepted.
            if "39=8" in response_order:
                # Najdeme text chyby (Tag 58)
                err_text = ""
                if "58=" in response_order:
                    err_text = response_order.split("58=")[1].split("\x01")[0]
                msg = f"ZAMÍTNUTO: Server příkaz odmítl. Důvod: {err_text}"
            else:
                msg = f"ÚSPĚCH: Obchod potvrzen serverem! (Execution Report přijat)"
            
            print(msg)
            print(f"DETAIL ODPOVĚDI: {response_order}")
            loguj_aktivitu_eth(msg)
            return True
        else:
            msg = f"NEJISTÝ VÝSLEDEK: Data odeslána, odpověď: {response_order}"
            print(msg)
            loguj_aktivitu_eth(msg)
            return True

    except Exception as e:
        msg = f"CHYBA SOCKETU: {str(e)}"
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
