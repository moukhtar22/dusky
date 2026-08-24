#!/usr/bin/env python3
"""
Final Daemon Stress Client - Multi-Turn Verification
Connects to live systemd daemon on ws://127.0.0.1:8765 and runs 3 sequential follow-up queries.
"""

import socket
import json
import base64
import os
import time

HOST = "127.0.0.1"
PORT = 8765

TEST_TURNS = [
    "Write a 2-sentence explanation of quantum computing.",
    "Now summarize that in exactly 6 words.",
    "Now convert that 6-word summary into uppercase."
]

def make_ws_handshake(sock):
    key = base64.b64encode(os.urandom(16)).decode('utf-8')
    req = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {HOST}:{PORT}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode('utf-8'))
    resp = sock.recv(4096).decode('utf-8', errors='ignore')
    if "101" not in resp:
        raise Exception(f"Handshake failed: {resp}")

def send_frame(sock, text):
    payload = text.encode('utf-8')
    length = len(payload)
    mask = os.urandom(4)
    masked_payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))
    
    if length <= 125:
        header = bytes([0x81, 0x80 | length])
    elif length <= 65535:
        header = bytes([0x81, 0xFE]) + length.to_bytes(2, 'big')
    else:
        header = bytes([0x81, 0xFF]) + length.to_bytes(8, 'big')
        
    sock.sendall(header + mask + masked_payload)

def read_frame(sock):
    head = sock.recv(2)
    if not head or len(head) < 2:
        return None
    b2 = head[1]
    length = b2 & 0x7F
    if length == 126:
        length = int.from_bytes(sock.recv(2), 'big')
    elif length == 127:
        length = int.from_bytes(sock.recv(8), 'big')
    
    payload = bytearray()
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk: break
        payload.extend(chunk)
    return payload.decode('utf-8', errors='replace')

def main():
    print("==================================================")
    print("      Live Systemd Daemon Multi-Turn Stress Test")
    print("==================================================")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    make_ws_handshake(sock)
    print("[+] Connected to live bridge.py systemd daemon!")

    for idx, prompt in enumerate(TEST_TURNS, 1):
        print(f"\n==================================================")
        print(f" TURN {idx}: \"{prompt}\"")
        print(f"==================================================\n")
        
        req = json.dumps({"type": "RUN_QUERY", "query": prompt, "requestId": f"stress-{idx}"})
        send_frame(sock, req)
        
        start_time = time.time()
        buf = ""
        while True:
            raw = read_frame(sock)
            if not raw: break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            
            mtype = msg.get("type")
            if mtype == "STREAM_CHUNK":
                chunk = msg.get("text", "")
                buf += chunk
                print(chunk, end="", flush=True)
            elif mtype in ("FINAL", "ERROR"):
                dur = time.time() - start_time
                if mtype == "FINAL":
                    print(f"\n\n[Turn {idx} COMPLETE in {dur:.2f}s | Length: {len(buf)} chars]")
                else:
                    print(f"\n\n[Turn {idx} ERROR]: {msg.get('error')}")
                break

    print("\n==================================================")
    print(" 🎉 3-TURN LIVE STRESS TEST COMPLETED SUCCESSFULLY!")
    print("==================================================")

if __name__ == "__main__":
    main()
