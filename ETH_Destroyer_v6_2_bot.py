import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import matplotlib.pyplot as plt
import seaborn as sns
import os
from datetime import datetime
from ctrader_fix import *
from twisted.internet.ssl import CertificateOptions
from twisted.internet import reactor

# Funkce pro logování veškeré aktivity do ETH deníku
def loguj_aktivitu_eth(zprava):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open('ETH_bot_activity.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {zprava}\n")

# Načtení FIX údajů z GitHub Secrets
def proved_obchod_fix(symbol, side):
    symbol = symbol.replace("-", "")
    host = os.getenv('FIX_HOST')
    port = int(os.getenv('FIX_PORT'))
    sender_id = os.getenv('FIX_SENDER_ID')
    target_id = os.getenv('FIX_TARGET_ID')
    password = os.getenv('FIX_PASSWORD')
    
    volume = 15.0 if symbol == "ETHUSD" else 2.0 

    print(f"--- FIX API: Odesílám {side} {symbol} ({volume} lotů) ---")
    
    try:
        client = Client(host, port, sender_id, target_id, password)
        client.sendOrder(symbol, side, volume)
        msg = f"ÚSPĚCH: FIX příkaz {side} {symbol} odeslán (objem: {volume})"
        print(msg)
        loguj_aktivitu_eth(msg)
        return True
    except Exception as e:
        msg = f"CHYBA: FIX příkaz {side} selhal. Důvod: {str(e)}"
        print(msg)
        loguj_aktivitu_eth
        
