#!/usr/bin/env python3
"""Exec-isolated CUDA 13 ONNX ASR worker for Dusky STT.

This file is never imported by the CPU daemon. It runs under .venv-worker,
whose onnxruntime namespace is owned exclusively by onnxruntime-gpu. Required
PyPI NVIDIA shared objects are loaded RTLD_GLOBAL before ONNX Runtime import.
Process exit is the only CUDA teardown mechanism.
"""

import argparse
import array
import ctypes
import fcntl
import gc
import importlib.metadata
import json
import logging
import mmap
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import time
import traceback
from typing import Any


MIN_PYTHON = (3, 14, 6)
MAX_PACKET = 64 * 1024
SAMPLE_RATE = 16_000
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
if os.environ.get("CUDA_VISIBLE_DEVICES", "") in {"", "-1"}:
    raise SystemExit("GPU worker requires exactly one CUDA_VISIBLE_DEVICES selection")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s dusky-worker[%(process)d]: %(message)s",
)
LOG = logging.getLogger("dusky.worker")

type JsonObject = dict[str, Any]


def normalized_name(name: str) -> str:
    return name.casefold().replace("_", "-")


def assert_gpu_ort_namespace() -> None:
    owners = {
        normalized_name(name)
        for name in importlib.metadata.packages_distributions().get("onnxruntime", [])
    }
    if owners != {"onnxruntime-gpu"}:
        raise RuntimeError(
            "GPU ORT namespace is not exclusive: "
            f"expected ['onnxruntime-gpu'], found {sorted(owners)}"
        )
    installed = {
        normalized_name(distribution.metadata["Name"])
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name", "").casefold().startswith("onnxruntime")
    }
    if installed != {"onnxruntime-gpu"}:
        raise RuntimeError(f"conflicting ONNX Runtime distributions are installed: {sorted(installed)}")


def distribution_library(distribution_name: str, soname: str) -> Path:
    distribution = importlib.metadata.distribution(distribution_name)
    candidates: list[Path] = []
    for relative in distribution.files or ():
        name = Path(str(relative)).name
        if name == soname or name.startswith(soname + "."):
            path = Path(distribution.locate_file(relative)).resolve()
            if path.is_file():
                candidates.append(path)
    if not candidates:
        raise RuntimeError(f"{distribution_name} does not provide required library {soname}")
    candidates.sort(key=lambda item: (len(item.name), item.name, str(item)))
    return candidates[0]


def preload_cuda13_runtime() -> list[ctypes.CDLL]:
    """Load a deterministic CUDA 13 dependency closure before importing ORT."""

    requirements = (
        ("nvidia-nvjitlink", "libnvJitLink.so.13"),
        ("nvidia-cuda-runtime", "libcudart.so.13"),
        ("nvidia-cuda-nvrtc", "libnvrtc-builtins.so.13"),
        ("nvidia-cuda-nvrtc", "libnvrtc.so.13"),
        ("nvidia-cublas", "libcublasLt.so.13"),
        ("nvidia-cublas", "libcublas.so.13"),
        ("nvidia-cufft", "libcufft.so.12"),
        ("nvidia-curand", "libcurand.so.10"),
        ("nvidia-cudnn-cu13", "libcudnn.so.9"),
    )
    handles: list[ctypes.CDLL] = []
    mode = ctypes.RTLD_GLOBAL | os.RTLD_NOW
    for distribution_name, soname in requirements:
        path = distribution_library(distribution_name, soname)
        try:
            handles.append(ctypes.CDLL(path, mode=mode))
        except OSError as exc:
            raise RuntimeError(f"failed to preload {path}: {exc}") from exc
        LOG.info("preloaded %s from %s", soname, distribution_name)
    return handles


assert_gpu_ort_namespace()
CUDA_HANDLES = preload_cuda13_runtime()

# Import order is a correctness property: ORT must see the already-global CUDA
# and cuDNN SONAMEs, and onnx-asr must see the GPU-owned ORT namespace.
import numpy as np
import onnxruntime as ort
import onnx_asr


def assert_cuda_provider() -> None:
    if ort.__version__ != "1.27.0":
        raise RuntimeError(f"expected onnxruntime-gpu 1.27.0, found {ort.__version__}")
    available = set(ort.get_available_providers())
    if "CUDAExecutionProvider" not in available:
        raise RuntimeError(
            "CUDAExecutionProvider is unavailable; refusing CPU-only execution. "
            f"Available providers: {sorted(available)}"
        )


def send_json(sock: socket.socket, payload: JsonObject) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_PACKET:
        raise ValueError("worker response exceeds IPC packet limit")
    sent = sock.send(encoded)
    if sent != len(encoded):
        raise OSError(f"short worker response send: {sent}/{len(encoded)}")


def recv_json_with_fd(sock: socket.socket) -> tuple[JsonObject | None, int | None]:
    integer_array = array.array("i")
    data, ancillary, flags, _address = sock.recvmsg(
        MAX_PACKET,
        socket.CMSG_SPACE(integer_array.itemsize),
        socket.MSG_CMSG_CLOEXEC,
    )
    if not data:
        return None, None
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        raise RuntimeError("truncated worker IPC packet")

    received_fds: list[int] = []
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            usable = len(payload) - (len(payload) % integer_array.itemsize)
            current = array.array("i")
            current.frombytes(payload[:usable])
            received_fds.extend(current.tolist())

    if len(received_fds) > 1:
        for received_fd in received_fds:
            os.close(received_fd)
        raise RuntimeError("worker received more than one file descriptor")

    try:
        message = json.loads(data.decode("utf-8"))
        if not isinstance(message, dict):
            raise ValueError("worker IPC payload must be a JSON object")
    except Exception:
        for received_fd in received_fds:
            os.close(received_fd)
        raise
    return message, received_fds[0] if received_fds else None


def validate_config(config: JsonObject, config_path: Path) -> None:
    required = {
        "schema_version",
        "model",
        "model_dir",
        "quantization",
        "gpu_mem_limit_mb",
        "idle_timeout_seconds",
        "max_request_seconds",
    }
    missing = required.difference(config)
    if missing:
        raise RuntimeError(f"worker configuration is missing keys: {sorted(missing)}")
    if config["schema_version"] != 2:
        raise RuntimeError("unsupported configuration schema")
    if config["model"] not in {
        "nemo-parakeet-tdt-0.6b-v2",
        "nemo-parakeet-tdt-0.6b-v3",
    }:
        raise RuntimeError("model is not supported by onnx-asr 0.12.0")
    if config["quantization"] not in {"int8", "fp16", "fp32"}:
        raise RuntimeError("unsupported model quantization")
    model_dir = config_path.parent / str(config["model_dir"])
    if not model_dir.is_dir():
        raise RuntimeError(f"prefetched model directory is absent: {model_dir}")


class AsrEngine:
    def __init__(self, config: JsonObject, config_path: Path, *, profile: bool = False) -> None:
        assert_cuda_provider()
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_mem_pattern = True
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        options.enable_profiling = profile
        if profile:
            options.profile_file_prefix = str(config_path.parent / ".dusky-ort-profile")

        gpu_limit = int(config["gpu_mem_limit_mb"]) * 1024 * 1024
        providers: list[Any] = [
            (
                "CUDAExecutionProvider",
                {
                    # CUDA_VISIBLE_DEVICES exposes one physical GPU, remapped to ordinal zero.
                    "device_id": 0,
                    "arena_extend_strategy": "kSameAsRequested",
                    "gpu_mem_limit": gpu_limit,
                    "cudnn_conv_algo_search": "HEURISTIC",
                    "do_copy_in_default_stream": True,
                    "use_tf32": True,
                },
            ),
            # onnx-asr decoding and any unsupported graph nodes may intentionally use CPU.
            "CPUExecutionProvider",
        ]
        model_dir = config_path.parent / str(config["model_dir"])
        quantization = None if config["quantization"] == "fp32" else str(config["quantization"])
        self._model = onnx_asr.load_model(
            str(config["model"]),
            model_dir,
            quantization=quantization,
            sess_options=options,
            providers=providers,
            preprocessor_config={
                "max_concurrent_workers": 1,
                "use_numpy_preprocessors": False,
                "use_conv_preprocessors": True,
            },
        )
        self._runtime = f"cuda13/onnxruntime-{ort.__version__}/onnx-asr-{onnx_asr.__version__}"
        LOG.info(
            "model ready: model=%s quantization=%s runtime=%s visible_gpu=%s",
            config["model"],
            config["quantization"],
            self._runtime,
            os.environ["CUDA_VISIBLE_DEVICES"],
        )
        self._profile = profile

    @property
    def runtime(self) -> str:
        return self._runtime

    def recognize(self, audio: np.ndarray) -> str:
        result = self._model.recognize(audio, sample_rate=SAMPLE_RATE)
        if result is None:
            return ""
        if not isinstance(result, str):
            raise TypeError(f"onnx-asr returned unexpected result type: {type(result).__name__}")
        return result.strip()

    def assert_profiled_cuda_encoder(self) -> None:
        if not self._profile:
            raise RuntimeError("CUDA profile verification was not enabled")
        asr = getattr(self._model, "asr", None)
        encoder = getattr(asr, "_encoder", None)
        if not isinstance(encoder, ort.InferenceSession):
            raise RuntimeError("onnx-asr 0.12.0 encoder session was not found")
        if encoder.get_providers()[0] != "CUDAExecutionProvider":
            raise RuntimeError(f"encoder provider order is invalid: {encoder.get_providers()}")
        profile_path = Path(encoder.end_profiling())
        try:
            events = json.loads(profile_path.read_text(encoding="utf-8"))
            cuda_nodes = [
                event
                for event in events
                if isinstance(event, dict)
                and isinstance(event.get("args"), dict)
                and event["args"].get("provider") == "CUDAExecutionProvider"
            ]
            if not cuda_nodes:
                raise RuntimeError(
                    "encoder inference profile contains no CUDA nodes; refusing silent CPU fallback"
                )
            LOG.info("CUDA profile verified %d encoder node events", len(cuda_nodes))
        finally:
            profile_path.unlink(missing_ok=True)

    def close(self) -> None:
        model = self._model
        self._model = None
        close = getattr(model, "close", None)
        if callable(close):
            close()
        del model
        gc.collect()


def validate_memfd(fd: int, samples: int, config: JsonObject) -> int:
    max_samples = round(float(config["max_request_seconds"]) * SAMPLE_RATE)
    if samples <= 0 or samples > max_samples:
        raise ValueError(f"invalid sample count: {samples}")
    expected_bytes = samples * 2
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("audio descriptor is not a regular memfd object")
    if metadata.st_size != expected_bytes:
        raise ValueError(
            f"memfd size mismatch: expected {expected_bytes}, got {metadata.st_size}"
        )
    seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
    if seals & REQUIRED_MEMFD_SEALS != REQUIRED_MEMFD_SEALS:
        raise ValueError(f"audio memfd is not immutable; seals={seals:#x}")
    return expected_bytes


def recognize_memfd(engine: AsrEngine, fd: int, samples: int, config: JsonObject) -> str:
    expected_bytes = validate_memfd(fd, samples, config)
    with mmap.mmap(fd, expected_bytes, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ) as mapped:
        pcm = np.frombuffer(mapped, dtype="<i2", count=samples)
        audio = np.multiply(pcm, 1.0 / 32768.0, dtype=np.float32)
        del pcm
        text = engine.recognize(audio)
        del audio
        return text


def validate_parent(ipc: socket.socket) -> None:
    raw = ipc.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, PEERCRED_SIZE)
    peer_pid, peer_uid, _peer_gid = struct.unpack("3i", raw)
    if peer_uid != os.getuid() or peer_pid != os.getppid():
        raise RuntimeError(
            f"worker peer credentials rejected: pid={peer_pid} uid={peer_uid} ppid={os.getppid()}"
        )


def run_worker(ipc_fd: int, config_path: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise RuntimeError("worker config must be a JSON object")
    validate_config(config, config_path)
    idle_timeout = float(config["idle_timeout_seconds"])
    if idle_timeout < 5:
        raise ValueError("idle timeout must be at least five seconds")

    ipc = socket.socket(fileno=ipc_fd)
    validate_parent(ipc)
    ipc.settimeout(min(1.0, idle_timeout / 4.0))
    engine: AsrEngine | None = None
    last_activity = time.monotonic()

    try:
        while True:
            try:
                message, audio_fd = recv_json_with_fd(ipc)
            except TimeoutError:
                if time.monotonic() - last_activity >= idle_timeout:
                    LOG.info("idle deadline reached; exiting to destroy the CUDA context")
                    return 0
                continue

            if message is None:
                return 0
            last_activity = time.monotonic()
            kind = message.get("type")

            if kind == "shutdown":
                if audio_fd is not None:
                    os.close(audio_fd)
                return 0
            if kind != "transcribe":
                if audio_fd is not None:
                    os.close(audio_fd)
                LOG.warning("discarded unsupported worker request type: %r", kind)
                continue

            request_id = message.get("request_id")
            if not isinstance(request_id, str) or len(request_id) != 32:
                if audio_fd is not None:
                    os.close(audio_fd)
                LOG.warning("discarded worker request with invalid request ID")
                continue
            if audio_fd is None:
                send_json(
                    ipc,
                    {
                        "type": "result",
                        "request_id": request_id,
                        "error": "audio file descriptor is missing",
                    },
                )
                continue

            started = time.monotonic()
            try:
                if message.get("encoding") != "s16le":
                    raise ValueError("unsupported audio encoding")
                if engine is None:
                    engine = AsrEngine(config, config_path)
                text = recognize_memfd(engine, audio_fd, int(message["samples"]), config)
                send_json(
                    ipc,
                    {
                        "type": "result",
                        "request_id": request_id,
                        "session_id": message.get("session_id"),
                        "phrase_id": message.get("phrase_id"),
                        "revision": message.get("revision"),
                        "final": bool(message.get("final")),
                        "text": text,
                        "runtime": engine.runtime,
                        "latency_ms": round((time.monotonic() - started) * 1000, 1),
                    },
                )
            except Exception as exc:
                LOG.error("request %s failed: %s\n%s", request_id, exc, traceback.format_exc())
                send_json(
                    ipc,
                    {
                        "type": "result",
                        "request_id": request_id,
                        "session_id": message.get("session_id"),
                        "phrase_id": message.get("phrase_id"),
                        "revision": message.get("revision"),
                        "final": bool(message.get("final")),
                        "text": "",
                        "error": str(exc)[:1000],
                    },
                )
            finally:
                os.close(audio_fd)
                last_activity = time.monotonic()
    finally:
        if engine is not None:
            engine.close()
        ipc.close()
        LOG.info("worker resources released; process exit destroys the CUDA context")


def run_self_test(config_path: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise RuntimeError("worker config must be a JSON object")
    validate_config(config, config_path)
    engine = AsrEngine(config, config_path, profile=True)
    try:
        result = engine.recognize(np.zeros(SAMPLE_RATE, dtype=np.float32))
        engine.assert_profiled_cuda_encoder()
        LOG.info("CUDA ASR self-test passed; silence result length=%d", len(result))
        return 0
    finally:
        engine.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Dusky isolated CUDA ASR worker")
    parser.add_argument("--ipc-fd", type=int)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        if args.ipc_fd is not None:
            parser.error("--self-test and --ipc-fd are mutually exclusive")
        return run_self_test(args.config)
    if args.ipc_fd is None:
        parser.error("--ipc-fd is required unless --self-test is used")
    return run_worker(args.ipc_fd, args.config)


if __name__ == "__main__":
    raise SystemExit(main())
