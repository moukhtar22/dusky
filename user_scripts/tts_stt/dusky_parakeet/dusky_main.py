#!/usr/bin/env python3
"""Dusky STT CPU daemon (hardware-agnostic).

Owns capture, stateful Silero VAD, append-only typing, file transcription,
and the control plane. ASR runs in an on-demand worker (.venv-worker) whose
EP matches config hardware: CUDA / CPU (+opportunistic MIGraphX/ROCM on
AMD). Audio crosses via sealed memfds over SOCK_SEQPACKET.

Transcripts are pure Parakeet output: the model already emits punctuated,
capitalized text at ~6% WER, so there is deliberately no LLM cleanup stage
(no Ollama server, no extra VRAM/RAM, no rewrite risk, no added latency).
"""

import argparse
import collections
import fcntl
import importlib.metadata
import json
import logging
import mmap
import os
import queue
import selectors
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

MIN_PYTHON = (3, 14, 6)
SAMPLE_RATE = 16000
VAD_FRAME_SAMPLES = 512
VAD_CONTEXT_SAMPLES = 64
BYTES_PER_SAMPLE = 2
MAX_PACKET = 65536
MAX_INLINE = 57344

if sys.version_info < MIN_PYTHON:
    raise SystemExit("Dusky STT requires CPython 3.14.6+")
_gil = getattr(sys, "_is_gil_enabled", None)
if _gil is None or not _gil():
    raise SystemExit("Dusky STT requires GIL-enabled CPython")

# Kernel ABI: Python 3.14 does not expose these on all builds.
if not hasattr(os, "MFD_NOEXEC_SEAL"):
    os.MFD_NOEXEC_SEAL = 0x0008  # type: ignore[attr-defined]
F_SEAL_EXEC = 0x0020

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import numpy as np
import onnxruntime as ort
import sounddevice as sd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s dusky[%(process)d]: %(message)s")
LOG = logging.getLogger("dusky")

APP_DIR = Path(os.environ.get("DUSKY_APP_DIR", Path(__file__).resolve().parent))
CONFIG_PATH = Path(os.environ.get("DUSKY_CONFIG", APP_DIR / "config.json"))

type JsonObject = dict[str, Any]

REQUIRED_SEALS = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
CUDA_TOKENS = ("libcuda.so", "libcudart.so", "libcublas", "libcudnn", "libnvrtc", "onnxruntime_providers_cuda")
PUNCT = ".,?!:;\"'()[]{}"
UNIT_NAME = "dusky_stt.service"
NO_IDLE_EXIT_ENV = "DUSKY_WORKER_NO_IDLE_EXIT"


def unit_is_enabled() -> bool | None:
    """True if the user unit is enabled (warm-resident mode), False if
    disabled (on-demand mode), None when the answer is unknowable."""
    try:
        r = subprocess.run(["systemctl", "--user", "is-enabled", UNIT_NAME],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() == "enabled"


def assert_cpu_ort_namespace() -> None:
    owners = sorted(set(importlib.metadata.packages_distributions().get("onnxruntime", [])))
    if owners != ["onnxruntime"]:
        raise RuntimeError(f"CPU ORT namespace not exclusive: {owners}")
    maps = Path("/proc/self/maps").read_text(encoding="utf-8", errors="replace").casefold()
    for tok in CUDA_TOKENS:
        if tok in maps:
            raise RuntimeError(f"CUDA leaked into CPU daemon: {tok}")


def cuda_maps() -> list[str]:
    try:
        text = Path("/proc/self/maps").read_text(encoding="utf-8", errors="replace").casefold()
    except OSError:
        return []
    return sorted({tok for tok in CUDA_TOKENS if tok in text})


def systemd_notify(state: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.sendto(state.encode(), addr)
    except OSError:
        pass


def watchdog_interval() -> float:
    raw = os.environ.get("WATCHDOG_USEC")
    if not raw:
        return 0.0
    pid = os.environ.get("WATCHDOG_PID")
    if pid and pid.strip() and int(pid) != os.getpid():
        return 0.0
    try:
        return max(0.25, int(raw) / 2_000_000.0)
    except ValueError:
        return 0.0


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as h:
            h.write(content)
            h.flush()
            os.fsync(h.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        tmp.unlink(missing_ok=True)


def create_sealed_audio(pcm: np.ndarray) -> int:
    payload = pcm.astype("<i2", copy=False).tobytes()
    fd = os.memfd_create("dusky-audio", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING | os.MFD_NOEXEC_SEAL)
    try:
        os.ftruncate(fd, len(payload))
        view = memoryview(payload)
        off = 0
        while off < len(payload):
            off += os.pwrite(fd, view[off:], off)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, REQUIRED_SEALS)
    except BaseException:
        os.close(fd)
        raise
    return fd


class RingBuffer:
    def __init__(self, capacity_samples: int) -> None:
        self._buf = np.zeros(capacity_samples, dtype="<i2")
        self._cap = capacity_samples
        self._start = 0
        self._len = 0
        self.dropped_samples = 0

    def __len__(self) -> int:
        return self._len

    def reset(self) -> None:
        self._start = 0
        self._len = 0

    def append(self, frame: np.ndarray) -> None:
        count = int(frame.size)
        if count >= self._cap:
            self.dropped_samples += count - self._cap
            self._buf[:] = frame[-self._cap:]
            self._start = 0
            self._len = self._cap
            return
        end = (self._start + self._len) % self._cap
        first = min(count, self._cap - end)
        self._buf[end:end + first] = frame[:first]
        if first < count:
            self._buf[:count - first] = frame[first:]
        ovf = max(0, self._len + count - self._cap)
        if ovf:
            self.dropped_samples += ovf
            self._start = (self._start + ovf) % self._cap
            self._len = self._cap
        else:
            self._len += count

    def read(self, max_samples: int | None = None) -> np.ndarray:
        if self._len == 0:
            return np.empty(0, dtype="<i2")
        first = min(self._len, self._cap - self._start)
        chunks = [self._buf[self._start:self._start + first]]
        if first < self._len:
            chunks.append(self._buf[:self._len - first])
        data = np.concatenate(chunks) if len(chunks) > 1 else chunks[0].copy()
        return data[-max_samples:] if max_samples and data.size > max_samples else data


class StatefulSileroVad:
    def __init__(self, model_path: Path) -> None:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, VAD_CONTEXT_SAMPLES), dtype=np.float32)

    def probability(self, pcm: np.ndarray) -> float:
        current = (pcm.astype(np.float32) * (1.0 / 32768.0)).reshape(1, -1)
        model_input = np.concatenate((self._context, current), axis=1)
        out, nxt = self._session.run(None, {
            "input": model_input, "state": self._state,
            "sr": np.array(SAMPLE_RATE, dtype=np.int64)})
        self._context = current[:, -VAD_CONTEXT_SAMPLES:].copy()
        self._state = np.asarray(nxt, dtype=np.float32)
        return float(np.asarray(out).reshape(-1)[0])


class WorkerManager:
    def __init__(self, config: JsonObject) -> None:
        self.config = config
        # Warm mode (service unit enabled): the worker is pre-spawned at
        # boot and never released after sessions, so dictation is instant.
        # The daemon sets this from unit_is_enabled(); the flag reaches the
        # worker process as DUSKY_WORKER_NO_IDLE_EXIT (no idle exit there).
        self.warm = False
        self._cv = threading.Condition(threading.RLock())
        self._proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._gen = 0
        self._spawns = 0
        self._inflight: dict[str, int] = {}
        self._results: dict[str, JsonObject] = {}
        self._discarded: set[str] = set()

    @property
    def pid(self) -> int | None:
        with self._cv:
            return self._proc.pid if self._proc and self._proc.poll() is None else None

    def _spawn_locked(self) -> None:
        if self._proc and self._proc.poll() is None and self._sock:
            return
        # Reap a dead predecessor before overwriting (else zombie Popen +
        # leaked socketpair fd); the old reader thread already closes its sock.
        if self._proc is not None and self._proc.poll() is not None:
            try:
                self._proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._proc.kill()
                except OSError:
                    pass
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._proc = None
            self._sock = None
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
        for s in (parent, child):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            except OSError:
                pass
        child.set_inheritable(True)
        env = dict(os.environ)
        if str(self.config.get("hardware", "cpu")) == "nvidia":
            env["CUDA_VISIBLE_DEVICES"] = str(self.config.get("gpu_device", 0))
            env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
            env["CUDA_MODULE_LOADING"] = "LAZY"
        else:
            env["CUDA_VISIBLE_DEVICES"] = "-1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["HF_HUB_OFFLINE"] = "1"
        if self.warm:
            # Warm-resident service mode: the worker must not idle-exit.
            env[NO_IDLE_EXIT_ENV] = "1"
        worker_py = APP_DIR / str(self.config.get("worker_python", ".venv-worker/bin/python"))
        worker_script = APP_DIR / str(self.config.get("worker_script", "dusky_worker.py"))
        cfg = APP_DIR / "config.json"
        proc = subprocess.Popen([str(worker_py), str(worker_script), "--config", str(cfg),
                                 "--fd", str(child.fileno())],
                                cwd=APP_DIR, env=env, close_fds=True, pass_fds=(child.fileno(),))
        child.close()
        self._gen += 1
        self._spawns += 1
        self._proc = proc
        self._sock = parent
        threading.Thread(target=self._reader_loop, args=(self._gen, proc, parent),
                         name=f"dusky-worker-{self._gen}", daemon=True).start()
        LOG.info("Spawned worker PID=%d gen=%d hw=%s", proc.pid, self._gen, self.config.get("hardware"))

    def _fail_generation(self, gen: int, reason: str) -> None:
        with self._cv:
            for req_id, g in list(self._inflight.items()):
                if g == gen and req_id not in self._results:
                    self._results[req_id] = {"ok": False, "request_id": req_id, "error": reason}
                    # Free the slot: without this two worker crashes pin
                    # len(_inflight) == limit forever and the next
                    # submit(force=True) spins forever (extended-session deadlock).
                    self._inflight.pop(req_id, None)
            self._cv.notify_all()

    def _reader_loop(self, gen: int, proc: subprocess.Popen[bytes], sock: socket.socket) -> None:
        try:
            while True:
                fds: list[int] = []
                try:
                    payload, ancdata, flags, _ = sock.recvmsg(MAX_PACKET, socket.CMSG_SPACE(4 * 8))
                except OSError as exc:
                    LOG.debug("Worker recv failed: %s", exc)
                    break
                for level, ctype, data in ancdata:
                    if level == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
                        n = len(data) // struct.calcsize("i")
                        fds.extend(struct.unpack(f"{n}i", data[:n * struct.calcsize("i")]))
                if flags & getattr(socket, "MSG_CTRUNC", 0x20) or flags & getattr(socket, "MSG_TRUNC", 0x20):
                    for fd in fds:
                        os.close(fd)
                    LOG.warning("Worker packet truncated; discarding generation %d", gen)
                    break
                if len(fds) > 1:
                    for fd in fds:
                        os.close(fd)
                    LOG.warning("Worker sent >1 fd; discarding")
                    continue
                if not payload:
                    for fd in fds:
                        os.close(fd)
                    break
                try:
                    resp = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    for fd in fds:
                        os.close(fd)
                    continue
                if resp.get("payload") == "memfd" and fds:
                    fd = fds[0]
                    try:
                        sz = os.fstat(fd).st_size
                        with mmap.mmap(fd, sz, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ) as m:
                            resp.update(json.loads(m.read().decode("utf-8")))
                    except (OSError, ValueError, json.JSONDecodeError) as exc:
                        LOG.warning("Bad worker memfd reply: %s", exc)
                    finally:
                        for fd in fds:
                            os.close(fd)
                else:
                    for fd in fds:
                        os.close(fd)
                    if resp.get("payload") == "memfd":
                        resp = {"ok": False, "request_id": resp.get("request_id"),
                                "error": "worker memfd reply arrived without fd"}
                req_id = resp.get("request_id")
                with self._cv:
                    self._inflight.pop(req_id, None)
                    if req_id in self._discarded:
                        self._discarded.discard(req_id)
                    elif req_id:
                        self._results[req_id] = resp
                    self._cv.notify_all()
        except Exception as exc:
            LOG.debug("Worker channel closed: %s", exc)
        finally:
            self._fail_generation(gen, "worker exited")
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            with self._cv:
                if gen == self._gen:
                    self._proc = None
                    self._sock = None
                self._cv.notify_all()
            sock.close()

    def submit(self, pcm: np.ndarray, meta: JsonObject, *, force: bool) -> str | None:
        with self._cv:
            limit = int(self.config.get("max_inflight_requests", 2))
            while len(self._inflight) >= limit:
                if not force:
                    return None
                self._cv.wait(0.1)
            self._spawn_locked()
            assert self._sock is not None
            req_id = uuid.uuid4().hex
            fd = create_sealed_audio(pcm)
            try:
                self._inflight[req_id] = self._gen
                self._sock.sendmsg([json.dumps({"op": "recognize", "request_id": req_id,
                                                "samples": int(pcm.size), "encoding": "s16le", **meta}).encode()],
                                   [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))])
            except OSError:
                self._inflight.pop(req_id, None)
                # Dead-socket race (worker idle-exited before the reader
                # reaped it): drop the stale handles so the retry respawns.
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._proc = None
                self._sock = None
                raise
            finally:
                os.close(fd)
            return req_id

    def wait_result(self, req_id: str, timeout: float, stop: threading.Event | None = None) -> JsonObject | None:
        deadline = time.monotonic() + timeout
        with self._cv:
            while req_id not in self._results:
                if stop is not None and stop.is_set():
                    self._discarded.add(req_id)
                    if len(self._discarded) > 128:
                        self._discarded.pop()
                    self._inflight.pop(req_id, None)
                    return None
                rem = deadline - time.monotonic()
                if rem <= 0:
                    self._discarded.add(req_id)
                    if len(self._discarded) > 128:
                        self._discarded.pop()
                    self._inflight.pop(req_id, None)
                    return None
                self._cv.wait(min(rem, 0.2))
            self._inflight.pop(req_id, None)
            return self._results.pop(req_id)

    def poll(self, req_id: str) -> JsonObject | None:
        """Non-blocking collect: return the result if it has arrived, else
        None. Never discards, never waits: the capture loop calls this once
        per 32 ms audio frame so the microphone stalls for exactly 0 s."""
        with self._cv:
            if req_id not in self._results:
                return None
            self._inflight.pop(req_id, None)
            return self._results.pop(req_id)

    def cancel(self, req_id: str) -> None:
        """Abandon a superseded interim: drop it if already answered, else
        mark it discarded so the late reply is dropped on arrival instead of
        leaking in _results forever."""
        with self._cv:
            if req_id in self._results:
                self._results.pop(req_id, None)
                self._inflight.pop(req_id, None)
            else:
                self._discarded.add(req_id)
                if len(self._discarded) > 128:
                    self._discarded.pop()
                self._inflight.pop(req_id, None)
            self._cv.notify_all()

    def prewarm(self) -> None:
        """Spawn the worker now (warm mode): model loads at boot so the
        first dictation pays no cold-start. Best effort; the next submit
        respawns transparently if this fails."""
        with self._cv:
            self._spawn_locked()

    def stop(self) -> None:
        with self._cv:
            sock = self._sock
        if sock:
            try:
                sock.sendmsg([b'{"op":"shutdown"}'])
            except OSError:
                pass
        proc = self._proc
        if proc:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    pass


class StableSuffixTyper:
    def __init__(self, holdback_words: int) -> None:
        self.holdback = max(0, holdback_words)
        self.emitted: list[str] = []
        self.diverged = False
        self.disabled = False

    def reset(self) -> None:
        self.emitted = []
        self.diverged = False

    def update(self, text: str, *, final: bool) -> None:
        if self.diverged or self.disabled:
            return
        words = text.strip().split()
        e_norm = [w.strip(PUNCT).casefold() for w in self.emitted]
        w_norm = [w.strip(PUNCT).casefold() for w in words]
        overlap = 0
        for a, b in zip(e_norm, w_norm):
            if a != b:
                break
            overlap += 1
        if overlap < len(e_norm):
            self.diverged = True
            LOG.warning("Hypothesis diverged; live typing suspended for phrase.")
            return
        target = len(words) if final else max(0, len(words) - self.holdback)
        if target > len(self.emitted):
            chunk = (" " if self.emitted else "") + " ".join(words[len(self.emitted):target])
            try:
                subprocess.run(["wtype", "-"], input=chunk.encode(), check=False, timeout=5)
            except (OSError, subprocess.SubprocessError):
                self.disabled = True
                LOG.warning("wtype failed; live typing disabled for session.")
                return
            self.emitted.extend(words[len(self.emitted):target])


def decode_file_to_pcm(path: Path, chunk_seconds: float) -> "collections.abc.Iterator[np.ndarray]":
    """Stream-decode any ffmpeg-readable file to 16k mono s16 chunks.

    Generator: yields one chunk at a time so a 2-hour podcast (~230 MB PCM)
    never materializes fully in the daemon (constant ~1 MB RSS instead of
    ~460 MB transient). Raises on ffmpeg failure.
    """
    per_samples = max(1, int(chunk_seconds * SAMPLE_RATE))
    per_bytes = per_samples * BYTES_PER_SAMPLE
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
           "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000",
           "-f", "s16le", "-c:a", "pcm_s16le", "-"]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout and proc.stderr
    errs: list[bytes] = []
    def drain() -> None:
        try:
            errs.append(proc.stderr.read() or b"")
        except OSError:
            pass
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    try:
        carry = b""
        while True:
            buf = proc.stdout.read(max(per_bytes - len(carry), 4096))
            if not buf:
                break
            carry += buf
            while len(carry) >= per_bytes:
                piece, carry = carry[:per_bytes], carry[per_bytes:]
                yield np.frombuffer(piece, dtype="<i2").copy()
        if carry:
            # Odd trailing byte cannot form a sample; drop it.
            carry = carry[:len(carry) & ~1]
            if carry:
                yield np.frombuffer(carry, dtype="<i2").copy()
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except OSError:
            pass
        t.join(timeout=10)
        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            rc = proc.wait(timeout=30)
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed ({rc}): {b''.join(errs)[-1000:].decode(errors='replace')}")


class RecordingSession:
    def __init__(self, daemon: "DuskyDaemon", realtime: bool) -> None:
        self.daemon = daemon
        self.config = daemon.config
        self.realtime = realtime
        self.session_id = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.vad = StatefulSileroVad(APP_DIR / str(self.config.get("vad_model_path", "models/silero_vad.onnx")))
        cap = int((float(self.config.get("max_phrase_seconds", 15.0)) + 2.0) * SAMPLE_RATE)
        self.ring = RingBuffer(cap)
        self.pre_roll: collections.deque[np.ndarray] = collections.deque(
            maxlen=max(1, round(float(self.config.get("pre_roll_seconds", 0.32)) * SAMPLE_RATE / VAD_FRAME_SAMPLES)))
        self.typer = StableSuffixTyper(int(self.config.get("stable_holdback_words", 2))) if realtime else None
        self.phrases: list[str] = []
        self.phrase_id = 0
        # Set by the "pause" control command (indicator pause button):
        # while set, mic frames are read-and-discarded so the stream never
        # overflows, and VAD restarts fresh on resume.
        self.paused = threading.Event()
        # Recording pill process (set by _run_session when spawned). Killed
        # the moment a stop is requested so the UI feels instant even though
        # the GPU drain continues headless until the final toast.
        self._indicator: subprocess.Popen | None = None
        # Live-typing state is touched from the capture thread (interim) and
        # the finalizer thread (final): always hold this around typer calls.
        self._typer_lock = threading.Lock()
        # Phrase finals are transcribed off the capture thread so hours-long
        # continuous speech never stalls the microphone (see run()).
        self._final_q: queue.Queue[tuple[int, np.ndarray] | None] = queue.Queue(maxsize=8)
        self._final_thread: threading.Thread | None = None

    # Typing 20k words (~120 KB) via wtype would flood the focused window
    # for tens of minutes and wedge the session thread; file transcripts
    # always land on disk + clipboard, typing is only for short captures.
    MAX_TYPE_CHARS = 2000

    def _publish(self, final_text: str) -> str:
        if not final_text:
            # A tap with no detected speech previously ended in total
            # silence, which reads as "the keybind is broken". Say so.
            if self.config.get("notifications", True):
                try:
                    subprocess.run(["notify-send", "-a", "Dusky STT", "-t", "2500",
                                    "Nothing transcribed", "No speech detected — try again, speaking clearly."],
                                   check=False, timeout=5)
                except (OSError, subprocess.SubprocessError) as exc:
                    LOG.warning("notify-send failed: %s", exc)
            return ""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path(str(self.config.get("state_dir", "~/.local/state/dusky-stt"))).expanduser() / "transcripts"
        out_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out_dir / f"capture-{stamp}-{self.session_id[:8]}.txt", final_text + "\n")
        try:
            if not self.realtime and self.config.get("push_type_at_end", True) and len(final_text) <= self.MAX_TYPE_CHARS:
                subprocess.run(["wtype", "-"], input=final_text.encode(), check=False, timeout=30)
            elif not self.realtime and len(final_text) > self.MAX_TYPE_CHARS:
                LOG.info("Transcript too long for typing (%d chars); kept file+clipboard.", len(final_text))
            subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8"], input=final_text.encode(),
                           check=False, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            LOG.warning("Publish helper failed: %s", exc)
        if self.config.get("notifications", True):
            try:
                subprocess.run(["notify-send", "-a", "Dusky STT", "-t", "3500",
                                "Transcription complete", final_text[:220]], check=False, timeout=5)
            except (OSError, subprocess.SubprocessError) as exc:
                LOG.warning("notify-send failed: %s", exc)
        return final_text

    def _finalizer_loop(self) -> None:
        """Transcribe phrase finals off the capture thread, in order.

        The capture loop must never block on inference: a 15 s phrase costs
        ~3-5 s of GPU time, and stalling stream.read() that long overflows
        PortAudio and deletes the start of the next phrase. So finals go
        through this FIFO while capture keeps reading the mic. Runs until a
        None sentinel; plain (non-stop-aware) waits are correct here because
        nothing time-critical shares this thread, and draining the backlog
        on --stop preserves the last words instead of dropping them.
        """
        per_request = float(self.config.get("finalize_timeout_seconds", 120.0))
        while True:
            item = self._final_q.get()
            if item is None:
                return
            phrase_id, pcm = item
            if pcm.size == 0:
                continue
            res: JsonObject | None = None
            for attempt in (1, 2):
                try:
                    req = self.daemon.worker.submit(pcm, {"session_id": self.session_id,
                        "phrase_id": phrase_id, "final": True}, force=True)
                except OSError as exc:
                    LOG.warning("Phrase %d submit failed (attempt %d): %s", phrase_id, attempt, exc)
                    req = None
                    continue
                if req:
                    res = self.daemon.worker.wait_result(req, per_request)
                if res and res.get("text") and res.get("ok", True):
                    break
                if res and not res.get("ok", True):
                    LOG.warning("Phrase %d failed (attempt %d): %s", phrase_id, attempt, res.get("error"))
                res = None
            if res and res.get("text") and res.get("ok", True):
                txt = res["text"].strip()
                if self.typer:
                    with self._typer_lock:
                        self.typer.update(txt, final=True)
                self.phrases.append(txt)
            elif not self.stop_event.is_set():
                LOG.error("Phrase %d skipped after retries; continuing session.", phrase_id)

    def _offer_final(self, phrase_id: int, pcm: np.ndarray) -> None:
        """Hand a snapshot to the finalizer without stalling the mic.

        Bounded blocking put (never endless): dropping a phrase after 10 s
        against a wedged worker is better than wedging capture forever.
        Deliberately stop-agnostic so the trailing-phrase flush on --stop
        still lands in the queue for the drain.
        """
        try:
            self._final_q.put((phrase_id, pcm), timeout=10.0)
        except queue.Full:
            LOG.warning("Phrase %d dropped: final queue full (worker wedged?).", phrase_id)

    def run(self) -> str:
        self._final_thread = threading.Thread(target=self._finalizer_loop,
                                              name=f"dusky-final-{self.session_id[:8]}", daemon=True)
        self._final_thread.start()
        try:
            self._capture_loop()
        finally:
            # Drain finals (preserves trailing speech on --stop), then publish.
            while True:
                try:
                    self._final_q.put(None, timeout=0.2)
                    break
                except queue.Full:
                    if self._final_thread is not None and not self._final_thread.is_alive():
                        break
                    continue
            if self._final_thread is not None:
                self._final_thread.join(timeout=float(self.config.get("finalize_timeout_seconds", 120.0)) + 30.0)
                if self._final_thread.is_alive():
                    LOG.warning("Finalizer did not drain; publishing partial transcript.")
        return self._publish(" ".join(self.phrases).strip())

    def _capture_loop(self) -> None:
        dev = self.config.get("input_device")
        pending_interim: str | None = None
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=VAD_FRAME_SAMPLES, channels=1,
                               dtype="int16", latency="low", device=dev) as stream:
            active = False
            onset = silence = 0
            onset_target = max(1, round(float(self.config.get("vad_onset_seconds", 0.096)) * SAMPLE_RATE / VAD_FRAME_SAMPLES))
            silence_target = max(1, round(float(self.config.get("phrase_silence_seconds", 0.80)) * SAMPLE_RATE / VAD_FRAME_SAMPLES))
            min_speech = int(float(self.config.get("vad_min_speech_seconds", 0.25)) * SAMPLE_RATE)
            last_interim = time.monotonic()
            while not self.stop_event.is_set():
                raw, overflowed = stream.read(VAD_FRAME_SAMPLES)
                if overflowed:
                    LOG.warning("PortAudio input overflow: audio lost before VAD (system under load?).")
                frame = np.frombuffer(raw, dtype="<i2").copy()
                if self.paused.is_set():
                    # Keep the stream flowing (no overflow) but drop everything.
                    if active or pending_interim is not None:
                        if pending_interim is not None:
                            self.daemon.worker.cancel(pending_interim)
                            pending_interim = None
                        if active and len(self.ring) >= min_speech:
                            self._offer_final(self.phrase_id, self.ring.read())
                        active = False
                        onset = silence = 0
                        self.pre_roll.clear()
                        self.vad.reset()
                        if self.typer:
                            with self._typer_lock:
                                self.typer.reset()
                    continue
                prob = self.vad.probability(frame)
                # Collect any finished interim result without blocking: the
                # mic stalls for exactly 0 s waiting on inference now.
                if pending_interim is not None:
                    res = self.daemon.worker.poll(pending_interim)
                    if res is not None:
                        pending_interim = None
                        if res.get("text") and res.get("ok", True) and self.typer:
                            with self._typer_lock:
                                self.typer.update(res["text"], final=False)
                if not active:
                    self.pre_roll.append(frame)
                    onset = onset + 1 if prob >= float(self.config.get("vad_start_threshold", 0.50)) else 0
                    if onset >= onset_target:
                        active = True
                        self.phrase_id += 1
                        self.ring.reset()
                        for p in self.pre_roll:
                            self.ring.append(p)
                        if self.typer:
                            with self._typer_lock:
                                self.typer.reset()
                else:
                    self.ring.append(frame)
                    silence = silence + 1 if prob < float(self.config.get("vad_end_threshold", 0.35)) else 0
                    now = time.monotonic()
                    if self.realtime and pending_interim is None and (now - last_interim) >= float(self.config.get("realtime_interval_seconds", 1.2)):
                        last_interim = now
                        if len(self.ring) >= min_speech:
                            # Fire-and-forget: force=False drops (rather than
                            # queues) when the worker is saturated, and the
                            # result is polled above on later frames.
                            pending_interim = self.daemon.worker.submit(self.ring.read(), {"session_id": self.session_id,
                                "phrase_id": self.phrase_id, "final": False}, force=False)
                    max_samples = int(float(self.config.get("max_phrase_seconds", 15.0)) * SAMPLE_RATE)
                    if silence >= silence_target or len(self.ring) >= max_samples:
                        active = False
                        onset = silence = 0
                        if pending_interim is not None:
                            # A late interim for the closing phrase is stale;
                            # the final carries the authoritative hypothesis.
                            self.daemon.worker.cancel(pending_interim)
                            pending_interim = None
                        if len(self.ring) >= min_speech:
                            self._offer_final(self.phrase_id, self.ring.read())
                        self.vad.reset()
                        if self.ring.dropped_samples:
                            LOG.warning("Ring overflow dropped %d samples", self.ring.dropped_samples)
            # Trailing speech: stopping mid-utterance (before 0.8 s of
            # silence elapse) must not delete the last sentence. Flush
            # whatever is still in the ring; the finalizer drains it before
            # publish, so --stop preserves the final words.
            if active:
                if pending_interim is not None:
                    self.daemon.worker.cancel(pending_interim)
                if len(self.ring) >= min_speech:
                    self._offer_final(self.phrase_id, self.ring.read())

    def run_file(self, path: Path) -> str:
        chunk_seconds = float(self.config.get("file_chunk_seconds", 20.0))
        per_request = float(self.config.get("finalize_timeout_seconds", 120.0))
        texts: list[str] = []
        for i, ch in enumerate(decode_file_to_pcm(path, chunk_seconds)):
            if self.stop_event.is_set():
                break
            if ch.size == 0:
                continue
            # Per-chunk retry: one transient worker crash must cost one
            # retry, never a 20 s hole and never the remaining ~359 chunks.
            res: JsonObject | None = None
            for attempt in (1, 2):
                if self.stop_event.is_set():
                    res = None
                    break
                try:
                    req = self.daemon.worker.submit(
                        ch, {"session_id": self.session_id, "phrase_id": i + 1, "final": True}, force=True)
                except OSError as exc:
                    LOG.warning("Chunk %d submit failed (attempt %d): %s", i + 1, attempt, exc)
                    req = None
                if req:
                    # Stop-aware: --stop aborts within ~0.2 s instead of
                    # one uninterruptible 120 s block.
                    res = self._wait_interruptible(req, per_request)
                if res and res.get("text") and res.get("ok", True):
                    break
                if res and not res.get("ok", True):
                    LOG.warning("Chunk %d failed (attempt %d): %s", i + 1, attempt, res.get("error"))
                res = None
            if res and res.get("text") and res.get("ok", True):
                texts.append(res["text"].strip())
            elif not self.stop_event.is_set():
                LOG.error("Chunk %d skipped after retries; continuing file.", i + 1)
        return self._publish(" ".join(texts).strip())

    def _wait_interruptible(self, req_id: str, total: float) -> JsonObject | None:
        # Stop-aware single wait: --stop aborts within ~0.2 s instead of one
        # uninterruptible 120 s block. No slicing (an intermediate timeout
        # would discard the request and orphan the late reply).
        return self.daemon.worker.wait_result(req_id, total, stop=self.stop_event)


def _kill_indicator(sess: RecordingSession) -> None:
    """Drop the on-screen pill without waiting (fire-and-forget). Used the
    instant a stop is requested; the session drain continues headless."""
    proc = sess._indicator
    sess._indicator = None
    if proc is not None:
        try:
            proc.terminate()
        except OSError:
            pass


class DuskyDaemon:
    def __init__(self, config_path: Path) -> None:
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        if self.config.get("schema_version") != 2:
            raise RuntimeError("config schema_version must be 2")
        self.worker = WorkerManager(self.config)
        # Chained take: a toggle received mid-drain stores (mode, time)
        # here; _run_session picks it up instead of going idle. `stop` and a
        # fresh `start` clear it. Entries older than 120 s are dropped so a
        # wedged drain can never surprise-restart minutes later.
        self._pending_restart: tuple[str, float] | None = None
        en = unit_is_enabled()
        # Service ON (unit enabled)   -> warm-resident: model preloaded,
        #                              instant dictation, VRAM held.
        # Service OFF (unit disabled) -> on-demand: VRAM only mid-job, full
        #                              release after, then self-stop.
        # Unknown                     -> on-demand (battery-safe default).
        self.warm = en is True
        self.worker.warm = self.warm
        LOG.info("Service mode: %s (unit %s)",
                 "warm-resident" if self.warm else "on-demand",
                 "enabled" if en is True else ("disabled" if en is False else "unknown, assuming on-demand"))
        self.state = "idle"
        self._lock = threading.RLock()
        self._session: RecordingSession | None = None
        self._file_session: RecordingSession | None = None
        self._stop = threading.Event()
        self._start_time = time.monotonic()
        rt = os.environ.get("XDG_RUNTIME_DIR")
        if not rt:
            raise RuntimeError("XDG_RUNTIME_DIR unset")
        self.control_path = Path(rt) / "dusky-stt" / "control.sock"
        self._listener = self._bind_socket()

    def _bind_socket(self) -> socket.socket:
        self.control_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.control_path.parent, 0o700)
        self.control_path.unlink(missing_ok=True)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
        old = os.umask(0o177)
        try:
            s.bind(str(self.control_path))
        finally:
            os.umask(old)
        os.chmod(self.control_path, 0o600)
        s.listen(16)
        s.setblocking(False)
        return s

    def status(self) -> JsonObject:
        with self._lock:
            sess = self._session
            state = self.state
            if sess is not None and sess.stop_event.is_set() and state in ("recording", "transcribing"):
                # Draining the GPU backlog: visibly distinct from capturing so
                # the pill and clients never present a dead-feeling "still
                # recording" state, and rapid re-taps are understood.
                state = "finalizing"
            return {"ok": True, "state": state, "pid": os.getpid(), "worker_pid": self.worker.pid,
                    "hardware": self.config.get("hardware", "cpu"),
                    "warm": self.warm,
                    "session": sess.session_id[:8] if sess is not None else None,
                    "paused": bool(sess.paused.is_set()) if sess is not None else False,
                    "uptime_seconds": round(time.monotonic() - self._start_time, 1),
                    "rss_kib": self._rss(), "cuda_maps": cuda_maps(),
                    "dropped_samples": sess.ring.dropped_samples if sess else 0}

    @staticmethod
    def _rss() -> int:
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
        except OSError:
            pass
        return 0

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())
        sel = selectors.DefaultSelector()
        sel.register(self._listener, selectors.EVENT_READ)
        interval = watchdog_interval()
        systemd_notify("READY=1\nSTATUS=Dusky STT: idle")
        nxt = time.monotonic() + interval if interval else 0.0
        if self.warm:
            # Boot-time preload off the notify path: the model (~622 MB
            # int8) loads in the background so the first keypress is instant.
            threading.Thread(target=self._prewarm_worker, daemon=True,
                             name="dusky-prewarm").start()
        try:
            while not self._stop.is_set():
                timeout = max(0.05, min(0.5, nxt - time.monotonic())) if interval else 0.5
                for key, _ in sel.select(timeout=timeout):
                    if key.fileobj is self._listener:
                        try:
                            conn, _ = self._listener.accept()
                        except (BlockingIOError, OSError):
                            continue
                        threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()
                if interval and time.monotonic() >= nxt:
                    systemd_notify("WATCHDOG=1")
                    nxt = time.monotonic() + interval
        finally:
            systemd_notify("STOPPING=1")
            sel.close()
            self._listener.close()
            self.worker.stop()
            self.control_path.unlink(missing_ok=True)
        return 0

    def _handle_conn(self, conn: socket.socket) -> None:
        with conn:
            try:
                cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                _, uid, _ = struct.unpack("3i", cred)
                if uid != os.getuid():
                    return
                # recvmsg (not recv): SEQPACKET truncation is only visible
                # via MSG_TRUNC, otherwise a crafted oversize datagram could
                # truncate to still-valid JSON and mis-execute.
                data, _, flags, _ = conn.recvmsg(MAX_PACKET)
                if flags & getattr(socket, "MSG_TRUNC", 0x20):
                    return
                if not data:
                    return
                req = json.loads(data.decode("utf-8"))
            except (OSError, ValueError):
                return
            cmd = req.get("command")
            resp: JsonObject = {"ok": False, "error": f"unknown command {cmd!r}"}
            # No mid-session toasts here by design: the on-screen recording
            # pill already covers the session lifetime (recording/paused
            # states included). Notifications fire only for outcomes
            # (transcription complete / nothing detected / capture failed).
            with self._lock:
                if cmd == "status":
                    resp = self.status()
                elif cmd in ("start", "toggle"):
                    if self.state == "idle":
                        realtime = req.get("mode", "realtime") != "push"
                        self._pending_restart = None
                        self._session = RecordingSession(self, realtime)
                        # Publish state under the lock so --status never
                        # reports stale idle after start was acked recording.
                        self.state = "recording"
                        threading.Thread(target=self._run_session, args=(self._session, False, None), daemon=True).start()
                        resp = {"ok": True, "state": "recording"}
                    elif cmd == "toggle" and self._session:
                        if self._session.stop_event.is_set():
                            # Already draining: this tap chains a fresh take
                            # after the drain (every press does something
                            # visible; the pill shows the drain meanwhile).
                            # Deliberately NOT set on the stop tap itself, or
                            # every stop would phantom-restart (pill reopen).
                            if self.state == "recording":
                                self._pending_restart = (req.get("mode", "realtime"), time.monotonic())
                                resp = {"ok": True, "state": "finalizing", "restart": "queued"}
                            else:
                                resp = {"ok": True, "state": "finalizing"}
                        else:
                            self._session.stop_event.set()
                            _kill_indicator(self._session)
                            resp = {"ok": True, "state": "finalizing"}
                    else:
                        resp = {"ok": False, "error": "already recording", "state": self.state}
                elif cmd == "stop":
                    self._pending_restart = None
                    if self._session:
                        self._session.stop_event.set()
                        _kill_indicator(self._session)
                        resp = {"ok": True, "state": "finalizing"}
                    else:
                        resp = {"ok": False, "error": "not recording", "state": self.state}
                elif cmd == "pause":
                    sess = self._session
                    if sess is not None and self.state == "recording":
                        if sess.paused.is_set():
                            sess.paused.clear()
                            resp = {"ok": True, "event": "resumed", "state": self.state}
                        else:
                            sess.paused.set()
                            resp = {"ok": True, "event": "paused", "state": self.state}
                    else:
                        resp = {"ok": False, "error": "not recording", "state": self.state}
                elif cmd == "unload":
                    # Free VRAM/RAM now (worker process exit is the only
                    # guaranteed CUDA teardown, letting the dGPU reach
                    # D3cold). Next request respawns on demand. Refused while
                    # busy so an in-flight transcription is never robbed.
                    if self.state == "idle":
                        self.worker.stop()
                        resp = {"ok": True, "event": "unloaded", "worker_pid": None}
                    else:
                        resp = {"ok": False, "error": "busy", "state": self.state}
                elif cmd == "file":
                    if self.state == "idle":
                        try:
                            p = Path(str(req.get("path", ""))).expanduser()
                            if not p.is_file():
                                # PrivateTmp=yes gives the daemon a private /tmp:
                                # host /tmp files are invisible by design.
                                resp = {"ok": False, "error": f"file not found (sandbox: place files under $HOME, not /tmp): {p}"}
                            else:
                                self._session = RecordingSession(self, False)
                                self.state = "transcribing"
                                threading.Thread(target=self._run_session, args=(self._session, True, p), daemon=True).start()
                                resp = {"ok": True, "state": "transcribing"}
                        except (OSError, ValueError) as exc:
                            resp = {"ok": False, "error": str(exc)}
                    else:
                        resp = {"ok": False, "error": "busy", "state": self.state}
            try:
                # Single datagram: sendall could split an oversize reply into
                # N datagrams of which the client reads only the first.
                raw = json.dumps(resp).encode()
                if len(raw) > MAX_PACKET:
                    raw = json.dumps({"ok": False, "error": "response too large"}).encode()
                conn.sendmsg([raw])
            except OSError:
                pass

    def _prewarm_worker(self) -> None:
        try:
            self.worker.prewarm()
        except Exception as exc:
            LOG.warning("Worker prewarm failed (on-demand respawn still works): %s", exc)

    def _maybe_self_stop(self) -> None:
        """On-demand mode only: after a session, stop the whole service so
        zero footprint remains (daemon RAM included). The next hotkey starts
        it again via the trigger's ensure_service. Delayed so --wait clients
        observe idle + transcript first; aborted if new work arrives or the
        unit got enabled meanwhile."""
        time.sleep(3.0)
        with self._lock:
            if self.state != "idle" or self._session is not None:
                return
        if self.warm or unit_is_enabled() is not False:
            return
        LOG.info("On-demand session complete and unit disabled; stopping service.")
        try:
            subprocess.run(["systemctl", "--user", "stop", UNIT_NAME],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            LOG.debug("Self-stop failed: %s", exc)

    def _run_session(self, sess: RecordingSession, is_file: bool, path: Path | None) -> None:
        self.state = "transcribing" if is_file else "recording"
        systemd_notify(f"STATUS=Dusky STT: {self.state}")
        indicator: subprocess.Popen | None = None
        if not is_file:
            indicator = self._spawn_indicator(sess)
            sess._indicator = indicator
        try:
            sess.run_file(path) if (is_file and path) else sess.run()
        except Exception as exc:
            LOG.error("Session failed: %s", exc)
            if self.config.get("notifications", True):
                try:
                    subprocess.run(["notify-send", "-a", "Dusky STT", "-t", "5000",
                                    "Capture failed", str(exc)[:220]], check=False, timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
        finally:
            # Pill is usually already gone (killed the instant stop was
            # requested for instant UI feedback); this covers session end
            # without an explicit stop (e.g. file jobs, errors).
            _kill_indicator(sess)
            if indicator is not None:
                try:
                    indicator.wait(timeout=2.0)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        indicator.kill()
                    except OSError:
                        pass
            # On-demand mode: deterministic VRAM offload after every session
            # (mic and file) so the dGPU can reach D3cold instead of burning
            # battery until idle_timeout_seconds expires. Worker process exit
            # is the only guaranteed CUDA teardown. Warm mode skips this:
            # the model stays resident for instant dictation by design.
            if not self.warm:
                try:
                    self.worker.stop()
                except Exception as exc:
                    LOG.debug("Worker release after session failed: %s", exc)
            pending: tuple[str, float] | None = None
            new_sess: RecordingSession | None = None
            with self._lock:
                self.state = "idle"
                self._session = None
                pending, self._pending_restart = self._pending_restart, None
                if pending is not None:
                    mode, queued_at = pending
                    if time.monotonic() - queued_at > 120.0:
                        LOG.warning("Dropping stale chained take (%.0fs old).", time.monotonic() - queued_at)
                    else:
                        # Spawn under the SAME lock hold: no interleaving
                        # toggle/start can slip in and double-start a session.
                        self._session = RecordingSession(self, mode != "push")
                        self.state = "recording"
                        new_sess = self._session
            systemd_notify("STATUS=Dusky STT: idle")
            if new_sess is not None:
                # Chained take: the user tapped again mid-drain. New pill
                # spawns with the new session.
                systemd_notify("STATUS=Dusky STT: recording")
                threading.Thread(target=self._run_session, args=(new_sess, False, None), daemon=True).start()
            elif not self.warm:
                threading.Thread(target=self._maybe_self_stop, daemon=True,
                                 name="dusky-self-stop").start()

    @staticmethod
    def _spawn_indicator(sess: RecordingSession) -> "subprocess.Popen[bytes] | None":
        """Show the on-screen recording pill (best effort, never fatal)."""
        try:
            script = APP_DIR / "dusky_rec_indicator.py"
            if not script.is_file():
                return None
            return subprocess.Popen(["/usr/bin/python3", str(script), "--session", sess.session_id],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            LOG.warning("Recording indicator unavailable: %s", exc)
            return None


def main() -> int:
    global APP_DIR, CONFIG_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=CONFIG_PATH)
    ap.add_argument("--check-cpu-isolation", action="store_true")
    args = ap.parse_args()
    CONFIG_PATH = args.config
    APP_DIR = Path(os.environ.get("DUSKY_APP_DIR", args.config.parent if args.config.name == "config.json" else APP_DIR))
    assert_cpu_ort_namespace()
    if args.check_cpu_isolation:
        print(json.dumps({"ok": True, "isolation": "clean", "cuda_maps": cuda_maps()}))
        return 0
    return DuskyDaemon(CONFIG_PATH).run()


if __name__ == "__main__":
    sys.exit(main())
