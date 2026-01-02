import ssl
import socket
import datetime
import time

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

def najdi_vsechna_id():
    host = "live-uk-eqx-01.p.c-trader.com"
    port = 5211 # QUOTE PORT
    sender_comp_id = "live.ftmo.17032147"
    username_int = "17032147"
    target_comp_id = "cServer"
    password = "CHeslo2026"
    
    print(f"--- PŘIPOJUJI SE NA DATOVÝ SERVER (5211) ---")
    
    try:
        context = ssl.create_default_context()
        sock = socket.create_connection((host, port))
        ssock = context.wrap_socket(sock, server_hostname=host)
        
        # LOGON
        logon_tags = {
            49: sender_comp_id, 
            56: target_comp_id,
            50: "QUOTE",
            57: "QUOTE",
            34: 1,
            52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            98: "0",
            108: "30",
            553: username_int,
            554: password,
            141: "Y"
        }
        ssock.sendall(create_fix_msg("A", logon_tags))
        login_response = ssock.recv(4096).decode('ascii', errors='ignore')
        
        if "35=A" in login_response:
            print("Logon OK. Stahuji seznam instrumentů...")
        else:
            print(f"Logon chyba: {login_response}")
            return

        # Request na všechny symboly
        list_req_tags = {
            49: sender_comp_id,
            56: target_comp_id,
            50: "QUOTE",
            57: "QUOTE",
            34: 2,
            52: datetime.datetime.utcnow().strftime("%Y%m%d-%H:%M:%S.%f")[:-3],
            320: "ReqID_ALL",
            263: "0" 
        }
        ssock.sendall(create_fix_msg("x", list_req_tags))

        # Čtení proudu dat
        print("Vypisuji nalezené páry (ID -> Název):")
        print("-" * 40)
        
        buffer = ""
        found_count = 0
        start_time = time.time()
        
        while time.time() - start_time < 15:
            chunk = ssock.recv(8192).decode('ascii', errors='ignore')
            if not chunk: break
            buffer += chunk
            
            # cTrader posílá zprávy oddělené \x01 (SOH)
            # Hledáme tag 55 (ID) a tag 107 (Popis) nebo 58 (Text)
            
            while "8=FIX.4.4" in buffer:
                # Najdeme konec jedné zprávy (podle checksumu 10=...)
                end_idx = buffer.find("\x0110=")
                if end_idx == -1: break
                
                # Vytáhneme celou zprávu
                msg = buffer[:end_idx+7] # +7 pro checksum
                buffer = buffer[end_idx+7:]
                
                if "35=y" in msg: # Security List message
                    # Rozparsujeme ID a Název
                    try:
                        parts = msg.split("\x01")
                        symbol_id = ""
                        symbol_name = ""
                        
                        for p in parts:
                            if p.startswith("55="): symbol_id = p.split("=")[1]
                            if p.startswith("107="): symbol_name = p.split("=")[1]
                            # Někdy je název v tagu 58
                            if p.startswith("58=") and not symbol_name: symbol_name = p.split("=")[1]

                        if symbol_id:
                            print(f"ID: {symbol_id} \t| Název: {symbol_name}")
                            found_count += 1
                            
                            # Hledáme naše favority
                            if "BTC" in symbol_name.upper() or "ETH" in symbol_name.upper():
                                print(f"🔥 NALEZENO: {symbol_name} má ID {symbol_id} 🔥")
                                
                    except:
                        pass

        ssock.close()
        print("-" * 40)
        print("Skenování dokončeno.")

    except Exception as e:
        print(f"Chyba: {e}")

if __name__ == "__main__":
    najdi_vsechna_id()
