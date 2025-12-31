import yfinance as yf
import pandas as pd
import numpy as np
import pandas_ta as ta
import matplotlib.pyplot as plt
import seaborn as sns

# 1. DATA (2024-2025)
symbol = 'ETH-USD'
df = yf.download(symbol, start="2024-01-01", end="2025-12-30", interval='1h', auto_adjust=True)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
df.dropna(inplace=True)

# 2. IDENTICKÉ INDIKÁTORY JAKO v6.1
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

# 4. SIMULACE S TRAILING STOP-LOSSEM
def run_smoother_eth(data, leverage=3, risk_pct=0.20):
    balance = 1000.0
    equity = []
    
    for i in range(1, len(data) - 2):
        sig = data['Signal'].iloc[i]
        prev_sig = data['Signal'].iloc[i-1]
        
        if sig != 0 and sig != prev_sig:
            entry = data['Close'].iloc[i]
            # Sledujeme vývoj ceny během 2 hodin
            window = data.iloc[i:i+3]
            
            # Simulace trailingu: Pokud cena v okně dosáhla zisku, ale pak spadla
            if sig == 1: # LONG
                peak = window['High'].max()
                final = window['Close'].iloc[-1]
                # Pokud zisk dosáhl aspoň 1.2% (před pákou), ale pak klesl, bereme aspoň něco
                if (peak - entry) / entry > 0.012:
                    res = max(0.005, (final - entry) / entry)
                else:
                    res = (final - entry) / entry
            else: # SHORT
                peak = window['Low'].min()
                final = window['Close'].iloc[-1]
                if (entry - peak) / entry > 0.012:
                    res = max(0.005, (entry - final) / entry)
                else:
                    res = (entry - final) / entry
            
            res = (res * leverage) - 0.0012
            res = max(res, -0.035) # Fixní SL pojistka zůstává
            
            balance += (balance * risk_pct) * res
        equity.append(balance)
    
    while len(equity) < len(data): equity.append(balance)
    return equity

df['Equity'] = run_smoother_eth(df)

# 5. HEATMAPA
df['Year'] = df.index.year
df['Month'] = df.index.month
df['Net_Return'] = df['Equity'].pct_change().fillna(0)
monthly = df.groupby(['Year', 'Month'])['Net_Return'].apply(lambda x: (1 + x).prod() - 1) * 100
monthly_table = monthly.unstack(level=0)

plt.figure(figsize=(10, 8))
sns.heatmap(monthly_table, annot=True, fmt=".1f", cmap="RdYlGn", center=0)
plt.title("ETH Sniper v6.2: Destroyer of worlds (2024-2025)")
plt.show()

print(f"Konečný zůstatek v6.2: {df['Equity'].iloc[-1]:.2f} USD")
