import ssl
import socket
import datetime
import time

def create_fix_msg(msg_type, tags_dict):
    s = "\x01"
    # Hlavička pro port 5211 (Quote)
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

def najdi_id_symbolu():
    host = "live-uk-eqx-01.p.c-trader.com"
    port = 5211 # QUOTE PORT (Data)
    sender_comp_id = "live.ftmo.17032147"
    username_int = "17032147"
    target_comp_id = "cServer"
    password = "CHeslo2026"
    
    print(f"--- PŘIPOJUJI SE NA DATOVÝ SERVER PRO ZÍSKÁNÍ ID ---")
    
    try:
        context = ssl.create_default_context()
        sock = socket.create_connection((host, port))
        ssock = context.wrap_socket(sock, server_hostname=host)
        
        # 1. LOGON (Quote Session)
        logon_tags = {
            49: sender_comp_id, 
            56: target_comp_id,
            50: "QUOTE",        # SenderSubID = QUOTE
            57: "QUOTE",        # TargetSubID = QUOTE
            34: 1,
            52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            98: "0",
            108: "30",
            553: username_int,
            554: password,
            141: "Y"
        }
        ssock.sendall(create_fix_msg("A", logon_tags))
        
        # Přečteme odpověď na Logon
        login_response = ssock.recv(4096).decode('ascii', errors='ignore')
        if "35=A" not in login_response:
            print(f"Chyba přihlášení: {login_response}")
            return

        print("Logon OK. Odesílám žádost o seznam symbolů...")

        # 2. SECURITY LIST REQUEST (MsgType=x)
        # Chceme seznam všech symbolů
        list_req_tags = {
            49: sender_comp_id,
            56: target_comp_id,
            50: "QUOTE",
            57: "QUOTE",
            34: 2,
            52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            320: "ReqID_123",     # SecurityReqID
            263: "0"              # SubscriptionRequestType (0=Snapshot)
        }
        ssock.sendall(create_fix_msg("x", list_req_tags))

        # 3. ČTENÍ ODPOVĚDÍ
        # Server nám pošle spoustu zpráv. Budeme hledat ETHUSD a BTCUSD.
        print("Skenuji symboly (to může chvilku trvat)...")
        
        buffer = ""
        found_eth = False
        found_btc = False
        
        start_time = time.time()
        while time.time() - start_time < 10: # Čekáme max 10 sekund
            chunk = ssock.recv(8192).decode('ascii', errors='ignore')
            if not chunk: break
            buffer += chunk
            
            # Rozdělíme na jednotlivé zprávy
            messages = buffer.split("8=FIX.4.4")
            for msg in messages:
                if not msg: continue
                # Hledáme název symbolu (Tag 107 nebo 58) a ID (Tag 55)
                # Obvykle: 55=ID ... 107=Name
                
                # Hledání ETH
                if "ETHUSD" in msg or "ETH-USD" in msg:
                    try:
                        # Vytáhneme ID (Tag 55)
                        parts = msg.split("\x01")
                        sym_id = [p.split("=")[1] for p in parts if p.startswith("55=")][0]
                        sym_name = "ETHUSD"
                        print(f"!!! NALEZENO: {sym_name} má ID: {sym_id}")
                        found_eth = True
                    except: pass

                # Hledání BTC
                if "BTCUSD" in msg or "BTC-USD" in msg:
                    try:
                        parts = msg.split("\x01")
                        sym_id = [p.split("=")[1] for p in parts if p.startswith("55=")][0]
                        sym_name = "BTCUSD"
                        print(f"!!! NALEZENO: {sym_name} má ID: {sym_id}")
                        found_btc = True
                    except: pass
            
            if found_eth and found_btc:
                print("Máme obojí! Končím.")
                break
                
            # Udržujeme buffer rozumně velký
            if len(buffer) > 100000: buffer = buffer[-1000:]

        ssock.close()

    except Exception as e:
        print(f"Chyba: {e}")

if __name__ == "__main__":
    najdi_id_symbolu()
