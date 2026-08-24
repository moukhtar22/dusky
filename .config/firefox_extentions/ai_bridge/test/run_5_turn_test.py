#!/usr/bin/env python3
import sys, os, json, socket, base64, uuid, time

HOST = "127.0.0.1"
PORT = 8765

prompts = [
    ("Turn 1", "Give me a 1-sentence greeting."),
    ("Turn 2", "List 3 primary colors in bullet points."),
    ("Turn 3", "Which color is formed when mixing the first two?"),
    ("Turn 4", "Write a Python line to store those 3 colors in a list."),
    ("Turn 5", "Summarize our conversation in 5 words.")
]

def main():
    print("="*60)
    print("      5-TURN SEQUENTIAL FOLLOW-UP STRESS TEST      ")
    print("="*60)
    
    report = []
    
    for label, text in prompts:
        print(f"\n>>> [{label}] SENDING: '{text}'", flush=True)
        sock = socket.socket()
        sock.settimeout(35)
        sock.connect((HOST, PORT))

        sec_key = base64.b64encode(os.urandom(16)).decode('utf-8')
        handshake = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {HOST}:{PORT}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {sec_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode('utf-8'))
        resp = sock.recv(1024)

        req_id = str(uuid.uuid4())
        payload = json.dumps({"type": "RUN_QUERY", "query": text, "requestId": req_id}).encode('utf-8')
        mask = os.urandom(4)
        masked = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))
        frame = bytearray([0x81, 0x80 | len(payload)]) + mask + masked
        sock.sendall(frame)

        start_t = time.time()
        full_resp = ""
        chunk_count = 0

        while True:
            try:
                head = sock.recv(2)
                if not head: break
                l = head[1] & 0x7F
                if l == 126: l = int.from_bytes(sock.recv(2), 'big')
                elif l == 127: l = int.from_bytes(sock.recv(8), 'big')
                p = bytearray()
                while len(p) < l:
                    c = sock.recv(l - len(p))
                    if not c: break
                    p.extend(c)

                msg = json.loads(p.decode('utf-8'))
                if msg.get("requestId") != req_id: continue

                mtype = msg.get("type")
                if mtype == "STREAM_CHUNK":
                    chunk = msg.get("text", "")
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    chunk_count += 1
                    full_resp = msg.get("full", full_resp + chunk)
                elif mtype == "FINAL":
                    dur = time.time() - start_t
                    final_txt = msg.get("full", full_resp)
                    print(f"\n--- [{label} SUCCESS in {dur:.2f}s | Chunks: {chunk_count} | Chars: {len(final_txt)}] ---", flush=True)
                    report.append((label, text, True, dur, final_txt))
                    sock.close()
                    break
                elif mtype == "ERROR":
                    dur = time.time() - start_t
                    err = msg.get('error')
                    print(f"\n--- [{label} ERROR in {dur:.2f}s]: {err} ---", flush=True)
                    report.append((label, text, False, dur, err))
                    sock.close()
                    break
            except Exception as e:
                dur = time.time() - start_t
                print(f"\n--- [{label} TIMEOUT/EXCEPTION in {dur:.2f}s]: {e} ---", flush=True)
                report.append((label, text, False, dur, str(e)))
                sock.close()
                break

        time.sleep(1.5)

    print("\n" + "="*60)
    print("             5-TURN BENCHMARK FINAL SUMMARY             ")
    print("="*60)
    all_ok = True
    for label, text, ok, dur, out in report:
        status = "PASSED" if ok else "FAILED"
        if not ok: all_ok = False
        print(f"[{status:6s}] {label:8s} | Time: {dur:5.2f}s | Response: {out[:50]}...")

    if all_ok:
        print("\n>>> 🎉 ALL 5 BACK-TO-BACK FOLLOW-UP PROMPTS PASSED 100%! <<<")

if __name__ == "__main__":
    main()
