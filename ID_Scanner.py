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

def najdi_raw_id():
    host = "live-uk-eqx-01.p.c-trader.com"
    port = 5211 # QUOTE PORT
    sender_comp_id = "live.ftmo.17032147"
    username_int = "17032147"
    target_comp_id = "cServer"
    password = "CTrader2026"
    
    print(f"--- HLEDÁM ID PRO BTC A ETH ---")
    
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
        ssock.recv(4096) # Přečíst logon odpověď
        print("Logon OK. Stahuji data...")

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

        start_time = time.time()
        while time.time() - start_time < 15:
            chunk = ssock.recv(16384).decode('ascii', errors='ignore')
            if not chunk: break
            
            # Hledáme prostý text
            if "ETHUSD" in chunk or "BTCUSD" in chunk:
                # Rozdělíme chunk na tagy a vypíšeme to čitelně
                print("\n--- NALEZEN SYMBOL V DATECH ---")
                # Nahradíme FIX oddělovač mezerou pro čitelnost
                clean_chunk = chunk.replace("\x01", " | ")
                print(clean_chunk)
                print("-------------------------------")
                
                # Zkusíme najít číslo ID (Tag 55) poblíž názvu
                # Hledáme ručně v tom textu
                
    except Exception as e:
        print(f"Chyba: {e}")

if __name__ == "__main__":
    najdi_raw_id()
