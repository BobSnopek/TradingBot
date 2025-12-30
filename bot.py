import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import os
import requests
from sklearn.ensemble import RandomForestClassifier

# --- KONFIGURACE (Načtení z GitHub Secrets) ---
ASSETS = ['BTC-USD', 'ETH-USD', 'SOL-USD']
FEE = 0.001

def build_and_test(symbol):
    # Stažení dat
    data = yf.download(symbol, period='730d', interval='1h', auto_adjust=True, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    
    df = data.copy()

    # Indikátory
    df['RSI'] = ta.rsi(df['Close'], length=14)
    df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
    df['ADX'] = adx_df['ADX_14']
    
    bb = ta.bbands(df['Close'], length=20, std=2)
    up_col = [c for c in bb.columns if c.startswith('BBU')][0]
    lo_col = [c for c in bb.columns if c.startswith('BBL')][0]
    df['BB_Dist'] = (df['Close'] - bb[lo_col]) / (bb[up_col] - bb[lo_col])
    df['Hour'] = df.index.hour

    # Cíl: nárůst ceny za 3 hodiny
    df['Target'] = np.where(df['Close'].shift(-3) > df['Close'] * 1.005, 1, 0)
    features = ['RSI', 'ADX', 'BB_Dist', 'Hour']
    df_ml = df[features + ['Target', 'Close']].dropna()

    # Trénink modelu (zmenšeno pro rychlost v cloudu)
    X = df_ml[features]
    y = df_ml['Target']
    split = int(len(df_ml) * 0.8)
    model = RandomForestClassifier(n_estimators=100, max_depth=5, class_weight='balanced', random_state=42)
    model.fit(X[:split], y[:split])

    # Predikce pro poslední řádek
    posledni_radek = X.tail(1)
    pravdepodobnost = model.predict_proba(posledni_radek)[0, 1]
    aktualni_adx = df_ml['ADX'].iloc[-1]
    aktualni_cena = df_ml['Close'].iloc[-1]
    
    # Rozhodnutí
    signal = "KOUPIT" if (pravdepodobnost > 0.62) and (aktualni_adx > 20) else "ČEKAT"
    
    return signal, pravdepodobnost, aktualni_cena

if __name__ == "__main__":
    log_file = "obchodni_denik.txt"
    cas = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')
    
    # Otevřeme soubor pro přidávání (append)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n--- Analýza ze dne {cas} ---\n")
        
        for asset in ASSETS:
            try:
                signal, prob, cena = build_and_test(asset)
                vystup = f"{asset}: {signal} (Jistota: {prob*100:.1f}%, Cena: {cena:.2f} USD)\n"
                print(vystup) # Uvidíš v logu GitHubu
                f.write(vystup)
            except Exception as e:
                f.write(f"Chyba u {asset}: {e}\n")
    
    print("Analýza dokončena.")
