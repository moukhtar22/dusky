#!/usr/bin/env python3
"""Dusky STT CPU daemon for Arch Linux, PipeWire, and Wayland.

The daemon is installed in a CPU-only virtual environment. It owns audio
capture, stateful Silero VAD, transcript stability, and Wayland output. ASR is
performed by a fresh interpreter from a separate GPU-only virtual environment.
Audio crosses the process boundary in sealed, non-executable memfd objects.
"""

import argparse
import array
from collections import deque
from dataclasses import dataclass, field
import fcntl
import hashlib
import importlib.metadata
import json
import logging
import os

""" Python 3.14.7 (daemon venv) does not have os.MFD_NOEXEC_SEAL.
The constant only exist in newer CPython """

if not hasattr(os, 'MFD_NOEXEC_SEAL'):
    os.MFD_NOEXEC_SEAL = 0x0008

from pathlib import Path
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from typing import Any
import uuid
import wave


MIN_PYTHON = (3, 14, 6)
SAMPLE_RATE = 16_000
VAD_FRAME_SAMPLES = 512
MAX_PACKET = 64 * 1024
PEERCRED_SIZE = struct.calcsize("3i")
REQUIRED_MEMFD_SEALS = (
    fcntl.F_SEAL_SEAL
    | fcntl.F_SEAL_SHRINK
    | fcntl.F_SEAL_GROW
    | fcntl.F_SEAL_WRITE
)

if sys.version_info < MIN_PYTHON:
    raise SystemExit("Dusky STT requires CPython 3.14.6 or newer")
if sys.implementation.name != "cpython" or not sys._is_gil_enabled():
    raise SystemExit("Dusky STT requires the GIL-enabled CPython 3.14 ABI")

# Every descendant starts GPU-blind. WorkerManager replaces this value only in
# the exec environment of the dedicated GPU interpreter.
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import numpy as np
import onnxruntime as ort
import sounddevice as sd


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s dusky[%(process)d]: %(message)s",
)
LOG = logging.getLogger("dusky")

APP_DIR = Path(os.environ.get("DUSKY_APP_DIR", Path(__file__).resolve().parent))
CONFIG_PATH = APP_DIR / "config.json"
WORKER_PATH = APP_DIR / "dusky_worker.py"
WORKER_PYTHON = APP_DIR / ".venv-worker" / "bin" / "python"

type JsonObject = dict[str, Any]


def normalized_distribution_name(name: str) -> str:
    return name.casefold().replace("_", "-")


def assert_cpu_ort_namespace() -> None:
    owners = {
        normalized_distribution_name(name)
        for name in importlib.metadata.packages_distributions().get("onnxruntime", [])
    }
    if owners != {"onnxruntime"}:
        raise RuntimeError(
            "CPU daemon ORT namespace is not exclusive: "
            f"expected ['onnxruntime'], found {sorted(owners)}"
        )
    if "CPUExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("CPUExecutionProvider is unavailable in the main environment")


def assert_no_cuda_mappings() -> None:
    maps = Path("/proc/self/maps").read_text(encoding="utf-8", errors="replace").casefold()
    forbidden = (
        "libcuda.so",
        "libcudart.so",
        "libcublas.so",
        "libcublaslt.so",
        "libcudnn.so",
        "onnxruntime_providers_cuda",
    )
    loaded = [name for name in forbidden if name in maps]
    if loaded:
        raise RuntimeError(f"CPU daemon loaded forbidden CUDA mappings: {loaded}")


def require_private_directory(path: Path, *, create: bool) -> Path:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"not a real directory: {path}")
    if metadata.st_uid != os.getuid():
        raise RuntimeError(f"directory is not owned by the service user: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RuntimeError(f"directory must have mode 0700: {path}")
    return path


def require_runtime_dir() -> Path:
    raw = os.environ.get("XDG_RUNTIME_DIR")
    if not raw:
        raise RuntimeError("XDG_RUNTIME_DIR is required")
    base = Path(raw)
    base_metadata = os.lstat(base)
    if (
        not stat.S_ISDIR(base_metadata.st_mode)
        or stat.S_ISLNK(base_metadata.st_mode)
        or base_metadata.st_uid != os.getuid()
    ):
        raise RuntimeError("XDG_RUNTIME_DIR has an invalid owner or type")
    return require_private_directory(base / "dusky-stt", create=True)


RUNTIME_DIR = require_runtime_dir()
CONTROL_PATH = RUNTIME_DIR / "control.sock"


def systemd_notify(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    notifier = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
    try:
        notifier.sendto(message.encode("utf-8"), address)
    except OSError:
        LOG.debug("sd_notify datagram failed", exc_info=True)
    finally:
        notifier.close()


def watchdog_interval() -> float:
    raw = os.environ.get("WATCHDOG_USEC")
    watchdog_pid = os.environ.get("WATCHDOG_PID")
    if not raw or (watchdog_pid and int(watchdog_pid) != os.getpid()):
        return 10.0
    return max(0.25, int(raw) / 2_000_000)


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def send_notification(title: str, message: str, *, critical: bool = False) -> None:
    binary = shutil.which("notify-send")
    if binary is None:
        return
    command = [binary, "-a", "Dusky STT", "-t", "3500"]
    if critical:
        command.extend(("-u", "critical"))
    command.extend((title[:80], message[:400]))
    try:
        subprocess.run(command, check=False, timeout=3)
    except (OSError, subprocess.SubprocessError):
        LOG.debug("desktop notification failed", exc_info=True)


def copy_to_clipboard(text: str) -> bool:
    binary = shutil.which("wl-copy")
    if binary is None or not os.environ.get("WAYLAND_DISPLAY"):
        return False
    try:
        completed = subprocess.run(
            [binary, "--type", "text/plain;charset=utf-8"],
            input=text.encode("utf-8"),
            check=False,
            timeout=5,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class RingBuffer:
    """Fixed-size int16 ring used for phrase audio without list growth."""

    def __init__(self, capacity: int) -> None:
        self._data = np.empty(capacity, dtype="<i2")
        self._capacity = capacity
        self._write = 0
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def clear(self) -> None:
        self._write = 0
        self._size = 0

    def append(self, samples: np.ndarray) -> None:
        count = int(samples.size)
        if count >= self._capacity:
            self._data[:] = samples[-self._capacity :]
            self._write = 0
            self._size = self._capacity
            return
        first = min(count, self._capacity - self._write)
        self._data[self._write : self._write + first] = samples[:first]
        remainder = count - first
        if remainder:
            self._data[:remainder] = samples[first:]
        self._write = (self._write + count) % self._capacity
        self._size = min(self._size + count, self._capacity)

    def snapshot(self) -> np.ndarray:
        if self._size == 0:
            return np.empty(0, dtype="<i2")
        start = (self._write - self._size) % self._capacity
        if start + self._size <= self._capacity:
            return self._data[start : start + self._size].copy()
        first = self._capacity - start
        result = np.empty(self._size, dtype="<i2")
        result[:first] = self._data[start:]
        result[first:] = self._data[: self._size - first]
        return result


class StatefulSileroVad:
    """Silero v6.2.1 streaming wrapper with recurrent and context state."""

    def __init__(self, model_path: Path) -> None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_cpu_mem_arena = True
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        input_names = {item.name for item in self._session.get_inputs()}
        if input_names != {"input", "state", "sr"}:
            raise RuntimeError(f"unexpected Silero VAD inputs: {sorted(input_names)}")
        if self._session.get_providers() != ["CPUExecutionProvider"]:
            raise RuntimeError(f"Silero did not bind exclusively to CPU: {self._session.get_providers()}")
        self.reset()
        assert_no_cuda_mappings()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, pcm: np.ndarray) -> float:
        if pcm.shape != (VAD_FRAME_SAMPLES,) or pcm.dtype != np.dtype("<i2"):
            raise ValueError("VAD frame must be 512 little-endian int16 samples")
        current = pcm.astype(np.float32).reshape(1, -1)
        current *= 1.0 / 32768.0
        model_input = np.concatenate((self._context, current), axis=1)
        output, next_state = self._session.run(
            None,
            {
                "input": model_input,
                "state": self._state,
                "sr": np.array(SAMPLE_RATE, dtype=np.int64),
            },
        )
        self._context = current[:, -64:].copy()
        self._state = np.asarray(next_state, dtype=np.float32)
        return float(np.asarray(output).reshape(-1)[0])


def write_all(fd: int, payload: memoryview) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError("short write to audio memfd")
        offset += written


def recv_seqpacket(sock: socket.socket) -> bytes:
    packet, _ancillary, flags, _address = sock.recvmsg(MAX_PACKET)
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        raise RuntimeError("truncated IPC packet")
    return packet


class WorkerManager:
    """Owns one exec-isolated GPU worker and routes results by request ID."""

    def __init__(self, config: JsonObject) -> None:
        self._config = config
        self._condition = threading.Condition(threading.RLock())
        self._process: subprocess.Popen[bytes] | None = None
        self._socket: socket.socket | None = None
        self._generation = 0
        self._inflight: dict[str, int] = {}
        self._results: dict[str, JsonObject] = {}
        self._discarded: set[str] = set()

    @property
    def pid(self) -> int | None:
        with self._condition:
            if self._process is None or self._process.poll() is not None:
                return None
            return self._process.pid

    @property
    def inflight(self) -> int:
        with self._condition:
            return len(self._inflight)

    def _spawn_locked(self) -> None:
        if self._process is not None and self._process.poll() is None and self._socket is not None:
            return
        if not WORKER_PYTHON.is_file() or not os.access(WORKER_PYTHON, os.X_OK):
            raise RuntimeError(f"GPU worker interpreter is unavailable: {WORKER_PYTHON}")

        parent, child = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC,
        )
        child.set_inheritable(True)
        environment = os.environ.copy()
        environment["CUDA_VISIBLE_DEVICES"] = str(self._config["gpu_device"])
        environment["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        environment["CUDA_MODULE_LOADING"] = "LAZY"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            process = subprocess.Popen(
                [
                    str(WORKER_PYTHON),
                    str(WORKER_PATH),
                    "--ipc-fd",
                    str(child.fileno()),
                    "--config",
                    str(CONFIG_PATH),
                ],
                cwd=APP_DIR,
                env=environment,
                close_fds=True,
                pass_fds=(child.fileno(),),
            )
        except Exception:
            parent.close()
            child.close()
            raise
        child.close()
        self._generation += 1
        generation = self._generation
        self._process = process
        self._socket = parent
        threading.Thread(
            target=self._reader_loop,
            args=(generation, process, parent),
            name=f"dusky-worker-reader-{generation}",
            daemon=True,
        ).start()
        LOG.info("spawned GPU worker pid=%d generation=%d", process.pid, generation)

    def _reader_loop(
        self,
        generation: int,
        process: subprocess.Popen[bytes],
        ipc: socket.socket,
    ) -> None:
        try:
            while packet := recv_seqpacket(ipc):
                response = json.loads(packet.decode("utf-8"))
                if not isinstance(response, dict):
                    raise ValueError("worker response is not an object")
                request_id = response.get("request_id")
                if not isinstance(request_id, str) or not request_id:
                    LOG.warning("worker sent response without a request ID: %r", response)
                    continue
                with self._condition:
                    self._inflight.pop(request_id, None)
                    if request_id in self._discarded:
                        self._discarded.remove(request_id)
                    else:
                        self._results[request_id] = response
                    self._condition.notify_all()
        except (OSError, ValueError, json.JSONDecodeError):
            LOG.debug("GPU worker result channel closed", exc_info=True)
        finally:
            try:
                return_code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait(timeout=3)
            with self._condition:
                abandoned = [
                    request_id
                    for request_id, request_generation in self._inflight.items()
                    if request_generation == generation
                ]
                for request_id in abandoned:
                    self._inflight.pop(request_id, None)
                    if request_id in self._discarded:
                        self._discarded.remove(request_id)
                    else:
                        self._results[request_id] = {
                            "type": "result",
                            "request_id": request_id,
                            "text": "",
                            "error": f"GPU worker exited with status {return_code}",
                        }
                if generation == self._generation:
                    self._process = None
                    self._socket = None
                self._condition.notify_all()
            try:
                ipc.close()
            except OSError:
                pass
            LOG.info("GPU worker pid=%d exited status=%d", process.pid, return_code)

    def _invalidate_locked(self) -> None:
        ipc, process = self._socket, self._process
        self._socket = None
        self._process = None
        if ipc is not None:
            try:
                ipc.close()
            except OSError:
                pass
        if process is not None and process.poll() is None:
            process.terminate()

    def submit(
        self,
        pcm: np.ndarray,
        metadata: JsonObject,
        *,
        force: bool,
        wait_seconds: float | None = None,
    ) -> str | None:
        contiguous = np.ascontiguousarray(pcm, dtype="<i2")
        if contiguous.size == 0:
            return None
        timeout = float(wait_seconds or self._config["worker_queue_timeout_seconds"])
        deadline = time.monotonic() + timeout

        for attempt in range(2):
            with self._condition:
                while len(self._inflight) >= int(self._config["max_inflight_requests"]):
                    if not force:
                        return None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("timed out waiting for GPU worker queue capacity")
                    self._condition.wait(min(remaining, 0.1))

                self._spawn_locked()
                assert self._socket is not None
                request_id = uuid.uuid4().hex
                payload: JsonObject = {
                    "type": "transcribe",
                    "request_id": request_id,
                    "samples": int(contiguous.size),
                    "encoding": "s16le",
                    **metadata,
                }
                encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
                if len(encoded) > MAX_PACKET:
                    raise ValueError("ASR request metadata is too large")

                memfd = os.memfd_create(
                    "dusky-audio",
                    os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING | os.MFD_NOEXEC_SEAL,
                )
                try:
                    os.ftruncate(memfd, contiguous.nbytes)
                    write_all(memfd, memoryview(contiguous).cast("B"))
                    fcntl.fcntl(memfd, fcntl.F_ADD_SEALS, REQUIRED_MEMFD_SEALS)
                    if fcntl.fcntl(memfd, fcntl.F_GET_SEALS) & REQUIRED_MEMFD_SEALS != REQUIRED_MEMFD_SEALS:
                        raise RuntimeError("audio memfd did not acquire all required seals")
                    descriptor = array.array("i", [memfd])
                    self._inflight[request_id] = self._generation
                    sent = self._socket.sendmsg(
                        [encoded],
                        [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptor.tobytes())],
                    )
                    if sent != len(encoded):
                        raise OSError(f"short SOCK_SEQPACKET send: {sent}/{len(encoded)}")
                    return request_id
                except OSError:
                    self._inflight.pop(request_id, None)
                    self._invalidate_locked()
                    self._condition.notify_all()
                    if attempt:
                        raise
                finally:
                    os.close(memfd)
        return None

    def pop_result(self, request_id: str) -> JsonObject | None:
        with self._condition:
            return self._results.pop(request_id, None)

    def wait_result(self, request_id: str, timeout: float) -> JsonObject:
        deadline = time.monotonic() + timeout
        with self._condition:
            while request_id not in self._results:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.discard(request_id)
                    raise TimeoutError(f"ASR request {request_id} timed out")
                self._condition.wait(min(remaining, 0.2))
            return self._results.pop(request_id)

    def discard(self, request_id: str) -> None:
        with self._condition:
            self._results.pop(request_id, None)
            if request_id in self._inflight:
                self._discarded.add(request_id)

    def stop(self) -> None:
        with self._condition:
            ipc, process = self._socket, self._process
            if ipc is not None:
                try:
                    ipc.send(json.dumps({"type": "shutdown"}).encode("utf-8"))
                except OSError:
                    pass
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def normalized_words(text: str) -> list[str]:
    return text.strip().split()


def comparison_word(word: str) -> str:
    return word.casefold().strip(".,?!:;\"'()[]{}")


def common_prefix_length(left: list[str], right: list[str]) -> int:
    length = 0
    for left_word, right_word in zip(left, right, strict=False):
        if comparison_word(left_word) != comparison_word(right_word):
            break
        length += 1
    return length


HALLUCINATIONS = frozenset(
    {
        "thank you",
        "thanks for watching",
        "thank you for watching",
        "please subscribe",
        "amara.org",
    }
)


def usable_transcript(text: str) -> bool:
    normalized = " ".join(text.casefold().split()).strip(" .,!?:;")
    return len(normalized) >= 2 and normalized not in HALLUCINATIONS


class WaylandTyper:
    def __init__(self) -> None:
        binary = shutil.which("wtype")
        if binary is None:
            raise RuntimeError("wtype is required for typing output")
        if not os.environ.get("WAYLAND_DISPLAY"):
            raise RuntimeError("WAYLAND_DISPLAY is absent from the user service environment")
        self._binary = binary

    def type_text(self, text: str) -> None:
        if not text:
            return
        completed = subprocess.run(
            [self._binary, "-"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=8,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"wtype failed ({completed.returncode}): {detail}")


@dataclass(slots=True)
class PhraseTypingState:
    previous: list[str] = field(default_factory=list)
    emitted: list[str] = field(default_factory=list)
    diverged: bool = False


class StableSuffixTyper:
    """Types only stable LCP words and never deletes focused-window content."""

    def __init__(self, holdback_words: int) -> None:
        self._wayland = WaylandTyper()
        self._holdback = holdback_words
        self._phrases: dict[int, PhraseTypingState] = {}
        self._has_output = False
        self.typed_text = ""

    def update(self, phrase_id: int, text: str, *, final: bool) -> None:
        words = normalized_words(text)
        if not words:
            return
        state = self._phrases.setdefault(phrase_id, PhraseTypingState())
        if state.diverged:
            state.previous = words
            return

        emitted_count = len(state.emitted)
        if common_prefix_length(state.emitted, words) < emitted_count:
            state.diverged = True
            state.previous = words
            LOG.warning(
                "ASR revision diverged from typed prefix for phrase %d; further live output "
                "for this phrase is suppressed, while the final text remains available in the clipboard",
                phrase_id,
            )
            return

        if final:
            stable_count = len(words)
        elif state.previous:
            stable_count = max(0, common_prefix_length(state.previous, words) - self._holdback)
        else:
            stable_count = 0

        if stable_count > emitted_count:
            suffix_words = words[emitted_count:stable_count]
            suffix = " ".join(suffix_words)
            if self._has_output:
                suffix = " " + suffix
            self._wayland.type_text(suffix)
            self.typed_text += suffix
            self._has_output = True
            state.emitted.extend(suffix_words)
        state.previous = words


@dataclass(slots=True)
class TranscriptRevision:
    revision: int
    text: str
    final: bool


class RecordingSession:
    def __init__(self, config: JsonObject, worker: WorkerManager, realtime: bool) -> None:
        self.config = config
        self.worker = worker
        self.realtime = realtime
        self.session_id = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.vad = StatefulSileroVad(APP_DIR / "models" / "silero_vad.onnx")
        self.pre_roll: deque[np.ndarray] = deque(
            maxlen=max(1, round(float(config["pre_roll_seconds"]) * SAMPLE_RATE / VAD_FRAME_SAMPLES))
        )
        self.phrase_audio = RingBuffer(round(float(config["max_phrase_seconds"]) * SAMPLE_RATE))
        self.phrase_id = 0
        self.revision = 0
        self.phrase_active = False
        self.onset_frames = 0
        self.speech_frames = 0
        self.silence_frames = 0
        self.pending: set[str] = set()
        self.pending_by_phrase: dict[int, set[str]] = {}
        self.request_phrases: dict[str, int] = {}
        self.latest: dict[int, TranscriptRevision] = {}
        self.last_provisional_at = 0.0
        self.overflow_count = 0
        self.typer = StableSuffixTyper(int(config["stable_holdback_words"])) if realtime else None
        self._audio_temporary: Path | None = None
        self._wave: wave.Wave_write | None = None
        if bool(config["keep_audio"]):
            self._audio_temporary = RUNTIME_DIR / f"capture-{self.session_id}.wav"
            self._wave = wave.open(str(self._audio_temporary), "wb")
            self._wave.setnchannels(1)
            self._wave.setsampwidth(2)
            self._wave.setframerate(SAMPLE_RATE)

    def request_stop(self) -> None:
        self.stop_event.set()

    def _submit_phrase(self, *, final: bool) -> None:
        minimum_samples = round(float(self.config["vad_min_speech_seconds"]) * SAMPLE_RATE)
        if self.speech_frames * VAD_FRAME_SAMPLES < minimum_samples:
            return
        if not final and self.pending_by_phrase.get(self.phrase_id):
            return
        self.revision += 1
        request_id = self.worker.submit(
            self.phrase_audio.snapshot(),
            {
                "session_id": self.session_id,
                "phrase_id": self.phrase_id,
                "revision": self.revision,
                "final": final,
            },
            force=final,
        )
        if request_id is not None:
            self.pending.add(request_id)
            self.pending_by_phrase.setdefault(self.phrase_id, set()).add(request_id)
            self.request_phrases[request_id] = self.phrase_id
            self.last_provisional_at = time.monotonic()

    def _finish_phrase(self) -> None:
        if self.phrase_active:
            self._submit_phrase(final=True)
        self.phrase_id += 1
        self.revision = 0
        self.phrase_active = False
        self.onset_frames = 0
        self.speech_frames = 0
        self.silence_frames = 0
        self.phrase_audio.clear()
        self.pre_roll.clear()

    def _process_frame(self, frame: np.ndarray) -> None:
        if self._wave is not None:
            self._wave.writeframesraw(memoryview(frame).cast("B"))
        probability = self.vad.probability(frame)
        start_threshold = float(self.config["vad_start_threshold"])
        end_threshold = float(self.config["vad_end_threshold"])
        end_frames = max(
            1,
            round(float(self.config["phrase_silence_seconds"]) * SAMPLE_RATE / VAD_FRAME_SAMPLES),
        )
        onset_required = max(
            1,
            round(float(self.config["vad_onset_seconds"]) * SAMPLE_RATE / VAD_FRAME_SAMPLES),
        )

        if not self.phrase_active:
            self.pre_roll.append(frame.copy())
            self.onset_frames = self.onset_frames + 1 if probability >= start_threshold else 0
            if self.onset_frames < onset_required:
                return
            self.phrase_active = True
            for buffered in self.pre_roll:
                self.phrase_audio.append(buffered)
            self.speech_frames = self.onset_frames
            self.silence_frames = 0
            self.last_provisional_at = time.monotonic()
            return

        self.phrase_audio.append(frame)
        if probability >= end_threshold:
            self.speech_frames += 1
            self.silence_frames = 0
        else:
            self.silence_frames += 1

        now = time.monotonic()
        if (
            self.realtime
            and len(self.phrase_audio) >= SAMPLE_RATE
            and now - self.last_provisional_at >= float(self.config["realtime_interval_seconds"])
        ):
            self._submit_phrase(final=False)

        max_samples = round(float(self.config["max_phrase_seconds"]) * SAMPLE_RATE)
        if self.silence_frames >= end_frames or len(self.phrase_audio) >= max_samples:
            self._finish_phrase()

    def _handle_result(self, request_id: str, result: JsonObject) -> None:
        self.pending.discard(request_id)
        phrase_value = self.request_phrases.pop(request_id, None)
        if phrase_value is not None:
            phrase_pending = self.pending_by_phrase.get(phrase_value)
            if phrase_pending is not None:
                phrase_pending.discard(request_id)
                if not phrase_pending:
                    self.pending_by_phrase.pop(phrase_value, None)
        if result.get("session_id") != self.session_id:
            return
        if result.get("error"):
            LOG.error("ASR request failed: %s", result["error"])
            return
        text = str(result.get("text", "")).strip()
        if not usable_transcript(text):
            return
        phrase_id = int(result["phrase_id"])
        revision = int(result["revision"])
        current = self.latest.get(phrase_id)
        if current is not None and revision < current.revision:
            return
        final = bool(result.get("final"))
        self.latest[phrase_id] = TranscriptRevision(revision, text, final)
        if self.typer is not None:
            try:
                self.typer.update(phrase_id, text, final=final)
            except Exception as exc:
                LOG.error("Wayland typing failed: %s", exc)

    def _drain_results(self) -> None:
        for request_id in tuple(self.pending):
            result = self.worker.pop_result(request_id)
            if result is not None:
                self._handle_result(request_id, result)

    def _close_wave(self) -> None:
        if self._wave is not None:
            self._wave.close()
            self._wave = None

    def _save_outputs(self) -> str:
        transcript = " ".join(self.latest[key].text for key in sorted(self.latest)).strip()
        state_dir = require_private_directory(Path(self.config["state_dir"]), create=True)
        transcript_dir = require_private_directory(state_dir / "transcripts", create=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")

        if transcript:
            output = transcript_dir / f"capture-{stamp}-{self.session_id[:8]}.txt"
            atomic_write_text(output, transcript + "\n")
            if not self.realtime and bool(self.config["push_type_at_end"]):
                try:
                    WaylandTyper().type_text(transcript)
                except Exception as exc:
                    LOG.error("push-mode Wayland typing failed: %s", exc)
            if self.config["output_mode"] in {"clipboard", "both", "realtime-both"}:
                if not copy_to_clipboard(transcript):
                    LOG.warning("could not copy transcript to the Wayland clipboard")
            send_notification("Transcription complete", transcript[:220])
            LOG.info("saved transcript to %s", output)
        else:
            send_notification("No speech detected", "No transcript was produced")

        if self._audio_temporary is not None:
            audio_dir = require_private_directory(state_dir / "audio", create=True)
            destination = audio_dir / f"capture-{stamp}-{self.session_id[:8]}.wav"
            os.replace(self._audio_temporary, destination)
            os.chmod(destination, 0o600)
        return transcript

    def run(self) -> str:
        LOG.info("recording session %s started realtime=%s", self.session_id, self.realtime)
        device = self.config.get("input_device")
        if isinstance(device, str) and device.lstrip("-").isdecimal():
            device = int(device)
        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=VAD_FRAME_SAMPLES,
                device=device if device not in (None, "") else None,
                channels=1,
                dtype="int16",
                latency="low",
            ) as stream:
                while not self.stop_event.is_set():
                    packet, overflowed = stream.read(VAD_FRAME_SAMPLES)
                    if overflowed:
                        self.overflow_count += 1
                    frame = np.frombuffer(packet, dtype="<i2", count=VAD_FRAME_SAMPLES).copy()
                    self._process_frame(frame)
                    self._drain_results()

            self._finish_phrase()
            deadline = time.monotonic() + float(self.config["finalize_timeout_seconds"])
            while self.pending and time.monotonic() < deadline:
                self._drain_results()
                if self.pending:
                    time.sleep(0.05)
            if self.pending:
                LOG.error("finalization timed out with %d ASR requests pending", len(self.pending))
                for request_id in tuple(self.pending):
                    self.worker.discard(request_id)
            self._close_wave()
            return self._save_outputs()
        finally:
            self._close_wave()
            if self._audio_temporary is not None:
                self._audio_temporary.unlink(missing_ok=True)
            if self.overflow_count:
                LOG.warning("PortAudio reported %d input overflows", self.overflow_count)


class FileTranscriber:
    def __init__(self, config: JsonObject, worker: WorkerManager) -> None:
        self.config = config
        self.worker = worker
        self.session_id = uuid.uuid4().hex
        self.vad = StatefulSileroVad(APP_DIR / "models" / "silero_vad.onnx")
        self.parts: list[str] = []

    def _transcribe_segment(self, segment: list[np.ndarray], index: int) -> None:
        if not segment:
            return
        audio = np.concatenate(segment).astype("<i2", copy=False)
        request_id = self.worker.submit(
            audio,
            {
                "session_id": self.session_id,
                "phrase_id": index,
                "revision": 1,
                "final": True,
            },
            force=True,
        )
        if request_id is None:
            raise RuntimeError("failed to queue file segment")
        result = self.worker.wait_result(request_id, float(self.config["finalize_timeout_seconds"]))
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        text = str(result.get("text", "")).strip()
        if usable_transcript(text):
            self.parts.append(text)

    def run(self, source: Path) -> str:
        if not source.is_file():
            raise FileNotFoundError(source)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is required for file transcription")
        process = subprocess.Popen(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-af",
                "aresample=resampler=soxr:precision=28",
                "-f",
                "s16le",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stderr_chunks: deque[bytes] = deque(maxlen=16)

        def drain_stderr() -> None:
            while chunk := process.stderr.read(64 * 1024):
                stderr_chunks.append(chunk)

        stderr_thread = threading.Thread(
            target=drain_stderr,
            name=f"dusky-ffmpeg-stderr-{self.session_id[:8]}",
            daemon=True,
        )
        stderr_thread.start()

        pre_roll: deque[np.ndarray] = deque(
            maxlen=max(1, round(float(self.config["pre_roll_seconds"]) * SAMPLE_RATE / VAD_FRAME_SAMPLES))
        )
        active: list[np.ndarray] = []
        active_samples = 0
        onset_frames = 0
        speech_frames = 0
        silence_frames = 0
        segment_index = 0
        max_samples = round(float(self.config["file_chunk_seconds"]) * SAMPLE_RATE)
        end_frames = max(
            1,
            round(float(self.config["phrase_silence_seconds"]) * SAMPLE_RATE / VAD_FRAME_SAMPLES),
        )
        onset_required = max(
            1,
            round(float(self.config["vad_onset_seconds"]) * SAMPLE_RATE / VAD_FRAME_SAMPLES),
        )
        byte_buffer = bytearray()
        frame_bytes = VAD_FRAME_SAMPLES * 2

        try:
            while block := process.stdout.read(64 * 1024):
                byte_buffer.extend(block)
                offset = 0
                while len(byte_buffer) - offset >= frame_bytes:
                    frame = np.frombuffer(
                        byte_buffer,
                        dtype="<i2",
                        count=VAD_FRAME_SAMPLES,
                        offset=offset,
                    ).copy()
                    offset += frame_bytes
                    probability = self.vad.probability(frame)

                    if not active:
                        pre_roll.append(frame)
                        onset_frames = onset_frames + 1 if probability >= float(
                            self.config["vad_start_threshold"]
                        ) else 0
                        if onset_frames < onset_required:
                            continue
                        active = list(pre_roll)
                        active_samples = sum(item.size for item in active)
                        speech_frames = onset_frames
                        silence_frames = 0
                        continue

                    active.append(frame)
                    active_samples += frame.size
                    if probability >= float(self.config["vad_end_threshold"]):
                        speech_frames += 1
                        silence_frames = 0
                    else:
                        silence_frames += 1

                    if silence_frames >= end_frames or active_samples >= max_samples:
                        minimum_samples = round(float(self.config["vad_min_speech_seconds"]) * SAMPLE_RATE)
                        if speech_frames * VAD_FRAME_SAMPLES >= minimum_samples:
                            self._transcribe_segment(active, segment_index)
                        segment_index += 1
                        active = []
                        active_samples = 0
                        onset_frames = 0
                        speech_frames = 0
                        silence_frames = 0
                        pre_roll.clear()
                if offset:
                    del byte_buffer[:offset]

            if active:
                minimum_samples = round(float(self.config["vad_min_speech_seconds"]) * SAMPLE_RATE)
                if speech_frames * VAD_FRAME_SAMPLES >= minimum_samples:
                    self._transcribe_segment(active, segment_index)
            return_code = process.wait(timeout=10)
            stderr_thread.join(timeout=2)
            stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            if return_code != 0:
                raise RuntimeError(f"ffmpeg failed ({return_code}): {stderr[-1000:]}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            stderr_thread.join(timeout=2)

        transcript = " ".join(self.parts).strip()
        if transcript:
            state_dir = require_private_directory(Path(self.config["state_dir"]), create=True)
            transcript_dir = require_private_directory(state_dir / "transcripts", create=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            output = transcript_dir / f"{source.stem}-{stamp}-{self.session_id[:8]}.txt"
            atomic_write_text(output, transcript + "\n")
            copy_to_clipboard(transcript)
            send_notification("File transcription complete", source.name)
            LOG.info("saved file transcript to %s", output)
        else:
            send_notification("No speech detected", source.name)
        return transcript


def process_rss_kib() -> int:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return -1


class DuskyDaemon:
    def __init__(self, config: JsonObject) -> None:
        self.config = config
        self.worker = WorkerManager(config)
        self._state_lock = threading.RLock()
        self._state = "idle"
        self._session: RecordingSession | None = None
        self._shutdown = threading.Event()
        self._listener: socket.socket | None = None
        self._socket_identity: tuple[int, int] | None = None

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state
        systemd_notify(f"STATUS=Dusky STT: {state}")

    def status(self) -> JsonObject:
        with self._state_lock:
            return {
                "ok": True,
                "state": self._state,
                "daemon_pid": os.getpid(),
                "daemon_rss_kib": process_rss_kib(),
                "worker_pid": self.worker.pid,
                "worker_inflight": self.worker.inflight,
                "backend": "cuda13",
                "model": self.config["model"],
                "quantization": self.config["quantization"],
            }

    def _recording_thread(self, session: RecordingSession) -> None:
        try:
            session.run()
        except Exception as exc:
            LOG.exception("recording session failed")
            send_notification("Dusky STT failed", str(exc), critical=True)
        finally:
            with self._state_lock:
                if self._session is session:
                    self._session = None
                    self._state = "idle"
            systemd_notify("STATUS=Dusky STT: idle")

    def start_recording(self, realtime: bool) -> JsonObject:
        with self._state_lock:
            if self._state != "idle":
                return {"ok": False, "error": f"daemon is busy ({self._state})"}
            session = RecordingSession(self.config, self.worker, realtime)
            self._session = session
            self._state = "recording-realtime" if realtime else "recording-push"
            threading.Thread(
                target=self._recording_thread,
                args=(session,),
                name="dusky-recording",
                daemon=True,
            ).start()
        systemd_notify(f"STATUS=Dusky STT: {self._state}")
        return {"ok": True, "state": self._state, "session_id": session.session_id}

    def stop_recording(self) -> JsonObject:
        with self._state_lock:
            if self._session is None or not self._state.startswith("recording-"):
                return {"ok": False, "error": f"not recording ({self._state})"}
            self._state = "finalizing"
            self._session.request_stop()
        systemd_notify("STATUS=Dusky STT: finalizing")
        return {"ok": True, "state": "finalizing"}

    def toggle(self, realtime: bool) -> JsonObject:
        with self._state_lock:
            recording = self._state.startswith("recording-")
        return self.stop_recording() if recording else self.start_recording(realtime)

    def _file_thread(self, source: Path) -> None:
        try:
            FileTranscriber(self.config, self.worker).run(source)
        except Exception as exc:
            LOG.exception("file transcription failed")
            send_notification("File transcription failed", str(exc), critical=True)
        finally:
            self._set_state("idle")

    def start_file(self, source_text: str) -> JsonObject:
        source = Path(source_text).expanduser().resolve()
        with self._state_lock:
            if self._state != "idle":
                return {"ok": False, "error": f"daemon is busy ({self._state})"}
            if not source.is_file():
                return {"ok": False, "error": f"file not found: {source}"}
            self._state = "transcribing-file"
            threading.Thread(
                target=self._file_thread,
                args=(source,),
                name="dusky-file",
                daemon=True,
            ).start()
        systemd_notify("STATUS=Dusky STT: transcribing-file")
        return {"ok": True, "state": "transcribing-file", "file": str(source)}

    def handle_command(self, request: JsonObject) -> JsonObject:
        command = request.get("command")
        if command == "status":
            return self.status()
        if command == "start":
            return self.start_recording(bool(request.get("realtime", True)))
        if command == "stop":
            return self.stop_recording()
        if command == "toggle":
            return self.toggle(bool(request.get("realtime", True)))
        if command == "file":
            return self.start_file(str(request.get("path", "")))
        return {"ok": False, "error": "unsupported command"}

    def _prepare_control_socket(self) -> socket.socket:
        if CONTROL_PATH.exists() or CONTROL_PATH.is_symlink():
            existing = os.lstat(CONTROL_PATH)
            if existing.st_uid != os.getuid():
                raise RuntimeError("control path is owned by another user")
            if not stat.S_ISSOCK(existing.st_mode):
                raise RuntimeError("refusing to replace a non-socket control path")
            CONTROL_PATH.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
        old_umask = os.umask(0o077)
        try:
            listener.bind(str(CONTROL_PATH))
        finally:
            os.umask(old_umask)
        os.chmod(CONTROL_PATH, 0o600)
        metadata = os.lstat(CONTROL_PATH)
        self._socket_identity = (metadata.st_dev, metadata.st_ino)
        listener.listen(16)
        listener.settimeout(0.5)
        return listener

    def _control_loop(self) -> None:
        assert self._listener is not None
        while not self._shutdown.is_set():
            try:
                connection, _address = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._shutdown.is_set():
                    return
                raise
            with connection:
                try:
                    credentials = connection.getsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_PEERCRED,
                        PEERCRED_SIZE,
                    )
                    _pid, uid, _gid = struct.unpack("3i", credentials)
                    if uid != os.getuid():
                        response = {"ok": False, "error": "peer uid rejected"}
                    else:
                        packet = recv_seqpacket(connection)
                        if not packet:
                            continue
                        request = json.loads(packet.decode("utf-8"))
                        if not isinstance(request, dict):
                            raise ValueError("request must be a JSON object")
                        response = self.handle_command(request)
                except Exception as exc:
                    LOG.warning("control request rejected: %s", exc)
                    response = {"ok": False, "error": str(exc)[:500]}
                encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()
                if len(encoded) > MAX_PACKET:
                    encoded = b'{"ok":false,"error":"response exceeds packet limit"}'
                try:
                    connection.send(encoded)
                except OSError:
                    LOG.debug("control peer disconnected before response", exc_info=True)

    def _unlink_owned_socket(self) -> None:
        if self._socket_identity is None:
            return
        try:
            metadata = os.lstat(CONTROL_PATH)
        except FileNotFoundError:
            return
        if (
            stat.S_ISSOCK(metadata.st_mode)
            and (metadata.st_dev, metadata.st_ino) == self._socket_identity
        ):
            CONTROL_PATH.unlink()

    def run(self) -> int:
        def handle_signal(signum: int, frame: Any) -> None:
            del signum, frame
            self._shutdown.set()
            with self._state_lock:
                if self._session is not None:
                    self._session.request_stop()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
        self._listener = self._prepare_control_socket()
        threading.Thread(target=self._control_loop, name="dusky-control", daemon=True).start()
        LOG.info("control socket ready at %s", CONTROL_PATH)
        systemd_notify("READY=1\nSTATUS=Dusky STT: idle")
        heartbeat = watchdog_interval()

        try:
            while not self._shutdown.wait(heartbeat):
                systemd_notify("WATCHDOG=1")
            with self._state_lock:
                session = self._session
            if session is not None:
                session.request_stop()
                deadline = time.monotonic() + 12
                while self._session is not None and time.monotonic() < deadline:
                    time.sleep(0.1)
            return 0
        finally:
            systemd_notify("STOPPING=1\nSTATUS=Dusky STT: stopping")
            if self._listener is not None:
                self._listener.close()
            self.worker.stop()
            self._unlink_owned_socket()


def load_config() -> JsonObject:
    metadata = os.lstat(CONFIG_PATH)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise RuntimeError("config.json must be a regular file owned by the service user")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "model",
        "model_dir",
        "quantization",
        "gpu_device",
        "gpu_mem_limit_mb",
        "state_dir",
        "idle_timeout_seconds",
        "max_inflight_requests",
        "worker_queue_timeout_seconds",
        "realtime_interval_seconds",
        "finalize_timeout_seconds",
        "max_phrase_seconds",
        "file_chunk_seconds",
        "pre_roll_seconds",
        "phrase_silence_seconds",
        "vad_onset_seconds",
        "vad_min_speech_seconds",
        "vad_start_threshold",
        "vad_end_threshold",
        "stable_holdback_words",
        "silero_sha256",
        "output_mode",
        "push_type_at_end",
        "keep_audio",
    }
    missing = required.difference(config)
    if missing:
        raise RuntimeError(f"configuration is missing keys: {sorted(missing)}")
    if config["schema_version"] != 2:
        raise RuntimeError("unsupported configuration schema")
    if config["model"] not in {
        "nemo-parakeet-tdt-0.6b-v2",
        "nemo-parakeet-tdt-0.6b-v3",
    }:
        raise RuntimeError("unsupported onnx-asr Parakeet model")
    if config["quantization"] not in {"int8", "fp16", "fp32"}:
        raise RuntimeError("unsupported quantization")
    if int(config["max_inflight_requests"]) not in {1, 2}:
        raise RuntimeError("max_inflight_requests must be one or two")
    if not 0.0 < float(config["vad_end_threshold"]) <= float(config["vad_start_threshold"]) < 1.0:
        raise RuntimeError("VAD thresholds are invalid")
    vad_path = APP_DIR / "models" / "silero_vad.onnx"
    digest = hashlib.sha256()
    with vad_path.open("rb") as model_file:
        while chunk := model_file.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != config["silero_sha256"]:
        raise RuntimeError("Silero VAD integrity check failed")
    require_private_directory(Path(config["state_dir"]), create=True)
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Dusky STT CPU daemon")
    parser.add_argument("--daemon", action="store_true", required=True)
    parser.parse_args()
    assert_cpu_ort_namespace()
    assert_no_cuda_mappings()
    return DuskyDaemon(load_config()).run()


if __name__ == "__main__":
    raise SystemExit(main())
