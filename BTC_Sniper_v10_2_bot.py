!pip install yfinance pandas_ta scikit-learn seaborn

import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.ensemble import RandomForestClassifier

# 1. DATA - Rok 2025
symbol = 'BTC-USD'
df_raw = yf.download(symbol, start="2025-01-01", end="2025-12-31", interval='1h', auto_adjust=True)
if isinstance(df_raw.columns, pd.MultiIndex):
    df_raw.columns = df_raw.columns.get_level_values(0)
df = df_raw.copy()

# 2. INDIKÁTORY (Univerzální metoda)
df['RSI'] = ta.rsi(df['Close'], length=7)
stoch = ta.stochrsi(df['Close'], length=10)
stoch_k_col = [c for c in stoch.columns if 'k' in c.lower()][0]
df['Stoch_K'] = stoch[stoch_k_col]
macd = ta.macd(df['Close'], fast=8, slow=21, signal=5)
macd_h_col = [c for c in macd.columns if 'h' in c.lower()][0]
df['MACD_H'] = macd[macd_h_col]

# 3. TRÉNINK (Konec 2024)
train_data = yf.download(symbol, start="2024-10-01", end="2025-01-01", interval='1h', auto_adjust=True)
if isinstance(train_data.columns, pd.MultiIndex):
    train_data.columns = train_data.columns.get_level_values(0)
td = train_data.copy()
td['RSI'] = ta.rsi(td['Close'], length=7)
td_macd = ta.macd(td['Close'], fast=8, slow=21, signal=5)
td_macd_h_col = [c for c in td_macd.columns if 'h' in c.lower()][0]
td['MACD_H'] = td_macd[td_macd_h_col]
td['Target_L'] = np.where(td['Close'].shift(-2) > td['Close'] * 1.003, 1, 0)
td['Target_S'] = np.where(td['Close'].shift(-2) < td['Close'] * 0.997, 1, 0)
td = td.dropna()

features = ['RSI', 'MACD_H']
model_l = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42).fit(td[features], td['Target_L'])
model_s = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42).fit(td[features], td['Target_S'])

# 4. PREDIKCE
df['Prob_L'] = model_l.predict_proba(df[features])[:, 1]
df['Prob_S'] = model_s.predict_proba(df[features])[:, 1]
df['Signal'] = 0
df.loc[df['Prob_L'] > 0.51, 'Signal'] = 1
df.loc[df['Prob_S'] > 0.51, 'Signal'] = -1

# 5. SIMULACE S LOGOVÁNÍM DO SOUBORU
def run_logged_sim(data, leverage=5, risk_pct=0.25):
    balance = 1000.0
    equity = np.full(len(data), balance)
    net_returns = np.zeros(len(data))
    
    with open('vypis_obchodu.txt', 'w') as f:
        f.write("DATUM | TYP | VSTUPNI CENA | VYSTUPNI CENA | ZISK/ZTRATA USD | AKTUALNI BALANCE\n")
        f.write("-" * 85 + "\n")
        
        for i in range(1, len(data) - 2):
            sig = data['Signal'].iloc[i]
            if sig != 0:
                entry = data['Close'].iloc[i]
                exit_p = data['Close'].iloc[i+2]
                
                # Výpočet procentuálního výsledku (Long vs Short)
                res = (exit_p - entry) / entry * leverage if sig == 1 else (entry - exit_p) / entry * leverage
                res -= 0.0012 # Poplatky
                res = max(res, -0.03) # Stop-loss pojistka
                
                pnl_usd = (balance * risk_pct) * res
                balance += pnl_usd
                
                typ_obchodu = "LONG " if sig == 1 else "SHORT"
                f.write(f"{data.index[i]} | {typ_obchodu} | {entry:8.2f} | {exit_p:8.2f} | {pnl_usd:8.2f} USD | {balance:8.2f} USD\n")
                
                net_returns[i] = pnl_usd / (balance - pnl_usd) if (balance - pnl_usd) != 0 else 0
            equity[i] = balance
            
    equity[-2:] = balance
    return equity, net_returns

df['Equity'], df['Net_Return'] = run_logged_sim(df)

# 6. ZOBRAZENÍ VÝSLEDKŮ
df['Month'] = df.index.month
monthly = df.groupby(df.index.month)['Net_Return'].apply(lambda x: (1 + x).prod() - 1) * 100

plt.figure(figsize=(10, 6))
sns.barplot(x=monthly.index, y=monthly.values, hue=monthly.index, palette="RdYlGn", legend=False)
plt.axhline(0, color='white', linewidth=1)
plt.title("Sniper v10.2: Finální analýza 2025")
plt.show()

print(f"HOTOVO! Soubor 'vypis_obchodu.txt' byl vytvořen.")
print(f"Konečný zůstatek: {df['Equity'].iloc[-1]:.2f} USD")
