#!/usr/bin/env python3
import sys, os, json, socket, base64, uuid, time

HOST = "127.0.0.1"
PORT = 8765

benchmark_prompts = [
    ("1. Short Greeting", "Hi, give me a 1-sentence greeting."),
    ("2. Medium Bullets", "List 4 key features of Python in bullet points."),
    ("3. Long Code Gen", "Write a complete Python class for a BankAccount with deposit, withdraw, and get_balance methods with docstrings."),
    ("4. Code Refactor", "Modify that BankAccount class to raise ValueError when withdrawing more than current balance."),
    ("5. 5-Word Summary", "Summarize key takeaway in 5 words.")
]

def main():
    print("="*65)
    print("   VARYING LENGTH & COMPLEXITY MULTI-TURN BENCHMARK   ")
    print("="*65)

    report = []

    for label, text in benchmark_prompts:
        print(f"\n==========================================", flush=True)
        print(f"[{label}] PROMPT: '{text}'", flush=True)
        print(f"==========================================", flush=True)

        sock = socket.socket()
        sock.settimeout(40)
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
                    print(f"\n\n[{label} SUCCESS] Finished in {dur:.2f}s | Chunks: {chunk_count} | Chars: {len(final_txt)}", flush=True)
                    report.append((label, text, True, dur, final_txt))
                    sock.close()
                    break
                elif mtype == "ERROR":
                    dur = time.time() - start_t
                    err = msg.get('error')
                    print(f"\n[{label} ERROR] {err}", flush=True)
                    report.append((label, text, False, dur, err))
                    sock.close()
                    break
            except Exception as e:
                dur = time.time() - start_t
                print(f"\n[{label} TIMEOUT/EXCEPTION] {e}", flush=True)
                report.append((label, text, False, dur, str(e)))
                sock.close()
                break

        time.sleep(1.5)

    print("\n" + "="*65)
    print("            BENCHMARK FINAL SUMMARY REPORT            ")
    print("="*65)
    all_ok = True
    for label, text, ok, dur, out in report:
        status = "PASSED" if ok else "FAILED"
        if not ok: all_ok = False
        print(f"[{status:6s}] {label:20s} | Time: {dur:5.2f}s | Chars: {len(out):4d}")

    if all_ok:
        print("\n>>> 🎉 ALL VARYING-LENGTH PROMPTS PASSED 100% PERFECTLY! <<<")

if __name__ == "__main__":
    main()
