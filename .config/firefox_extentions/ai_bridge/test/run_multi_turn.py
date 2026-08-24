#!/usr/bin/env python3
import sys
import os
import json
import socket
import base64
import uuid
import struct
import time
import subprocess

HOST = "127.0.0.1"
PORT = 8765

def send_prompt(prompt_text, turn_num):
    print(f"\n==========================================", flush=True)
    print(f"[TURN {turn_num}] Sending: '{prompt_text}'", flush=True)
    print(f"==========================================", flush=True)

    s = socket.socket()
    s.settimeout(25)
    s.connect((HOST, PORT))

    sec_key = base64.b64encode(os.urandom(16)).decode('utf-8')
    handshake = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {HOST}:{PORT}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {sec_key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    s.sendall(handshake.encode('utf-8'))
    resp = s.recv(1024)

    req_id = str(uuid.uuid4())
    payload = json.dumps({"type": "RUN_QUERY", "query": prompt_text, "requestId": req_id}).encode('utf-8')
    mask = os.urandom(4)
    masked = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))
    frame = bytearray([0x81, 0x80 | len(payload)]) + mask + masked
    s.sendall(frame)

    # 1. Focus Firefox window via Hyprland IPC & 2. Fire authentic Return keypress
    def fire_return():
        time.sleep(1.2)
        try:
            res = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                clients = json.loads(res.stdout)
                ff = [c for c in clients if 'firefox' in c.get('class','').lower()]
                if ff:
                    addr = ff[0]['address']
                    ws_info = ff[0].get('workspace', {})
                    ws_id = ws_info.get('id')
                    if ws_id and ws_id > 0:
                        subprocess.run(["hyprctl", "dispatch", f"hl.dsp.focus({{ workspace = '{ws_id}' }})"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                    subprocess.run(["hyprctl", "dispatch", f"hl.dsp.focus({{ window = 'address:{addr}' }})"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        except Exception: pass

        try: subprocess.run(["wtype", "-k", "Return"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception: pass
    
    import threading
    t = threading.Thread(target=fire_return, daemon=True)
    t.start()

    full_text = ""
    start_t = time.time()
    while True:
        try:
            head = s.recv(2)
            if not head: break
            l = head[1] & 0x7F
            if l == 126: l = struct.unpack("!H", s.recv(2))[0]
            elif l == 127: l = struct.unpack("!Q", s.recv(8))[0]

            p = bytearray()
            while len(p) < l:
                c = s.recv(l - len(p))
                if not c: break
                p.extend(c)

            data = json.loads(p.decode('utf-8'))
            if data.get("requestId") != req_id: continue

            mtype = data.get("type")
            if mtype == "STREAM_CHUNK":
                chunk = data.get("text", "")
                sys.stdout.write(chunk)
                sys.stdout.flush()
                full_text = data.get("full", full_text + chunk)
            elif mtype == "FINAL":
                print("\n" + "-"*40, flush=True)
                final_res = data.get("full", full_text)
                print(f"[TURN {turn_num} SUCCESS] Completed in {time.time()-start_t:.1f}s:", flush=True)
                print(f"--> {final_res}", flush=True)
                s.close()
                return True, final_res
            elif mtype == "ERROR":
                print(f"\n[TURN {turn_num} ERROR] {data.get('error')}", flush=True)
                s.close()
                return False, data.get('error')
        except Exception as e:
            print(f"\n[TURN {turn_num} EXCEPTION] {e}", flush=True)
            s.close()
            return False, str(e)

def main():
    prompts = [
        "What is 15 + 25? Answer in one sentence.",
        "Now multiply that result by 3.",
        "Now subtract 20 from that result.",
        "Summarize all three steps in one sentence."
    ]

    history = []
    for idx, p in enumerate(prompts, 1):
        ok, res = send_prompt(p, idx)
        history.append((idx, p, ok, res))
        time.sleep(2.0)

    print("\n==========================================", flush=True)
    print("MULTI-TURN TEST SUMMARY REPORT", flush=True)
    print("==========================================", flush=True)
    all_ok = True
    for idx, p, ok, res in history:
        status = "PASSED" if ok else "FAILED"
        if not ok: all_ok = False
        print(f"Turn {idx}: [{status}] Prompt: '{p}'", flush=True)
        if ok:
            print(f"  Result: {res[:120]}...", flush=True)

    if all_ok:
        print("\nALL MULTI-TURN PROMPTS PASSED 100% SUCCESSFULLY!", flush=True)
    else:
        print("\nSOME TURNS FAILED", flush=True)

if __name__ == "__main__":
    main()
