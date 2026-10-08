#!/usr/bin/env python3
"""Atomic Arch Linux installer for Dusky STT (hardware-agnostic, bleeding-edge).

Targets Arch Linux rolling (kernel 7.3+, CPython 3.14.7+ GIL, uv).
Hardware is explicit and auto-detected -- no hardcoded users or machines:

  --hardware auto    (default) detect nvidia > amd > cpu
  --hardware nvidia  NVIDIA dGPU via onnxruntime-gpu + CUDA 13 (D3cold capable)
  --hardware amd     AMD system using the packaged CPU inference runtime
  --hardware cpu     CPU inference

Layout (username-agnostic, all under $HOME):
  APP_DIR = ~/.local/lib/dusky-stt
    .venv-main    CPU onnxruntime + numpy + sounddevice (daemon, never CUDA)
    .venv-worker  nvidia: onnxruntime-gpu + CUDA 13 + onnx-asr --no-deps
                  cpu/amd: onnxruntime + onnx-asr (CPU)
"""

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

MIN_PYTHON = (3, 14, 7)
MIN_KERNEL = (7, 3)
MIN_DRIVER_MAJOR = 580
SCHEMA_VERSION = 2

APP_DIR = Path(os.environ.get("DUSKY_APP_DIR", Path.home() / ".local" / "lib" / "dusky-stt")).expanduser()
BIN_DIR = Path.home() / ".local" / "bin"
UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"
UNIT_NAME = "dusky_stt.service"
DEFAULT_STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "dusky-stt"
DEFAULT_MODEL_ROOT = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "dusky-stt/models"
VAD_CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dusky-stt/silero-v6.2.1.onnx"

SOURCE_DIR = Path(__file__).resolve().parent
REQUIRED_SOURCES = ("dusky_main.py", "dusky_worker.py", "dusky_trigger.py", "dusky_rec_indicator.py", "dusky_verify.sh", "README.md", UNIT_NAME)

BASE_PACKAGES = (
    "pipewire",
    "pipewire-audio",
    "pipewire-alsa",
    "pipewire-pulse",
    "wireplumber",
    "portaudio",
    "ffmpeg",
    "wtype",
    "wl-clipboard",
    "libnotify",
    "uv",
    "gtk3",
    "python-gobject",
    "libpulse",  # parec for the recording indicator
)
NVIDIA_PACKAGES = ("nvidia-utils",)

MAIN_PACKAGES = ("onnxruntime==1.30.0", "numpy==2.5.3", "sounddevice==0.5.6")

# CUDA 13.0.x is deliberately HELD (not bumped to 13.3.x): 13.3 needs
# driver >= 610.43 per NVIDIA release notes, which would brick every
# 580-609 user this installer explicitly accepts (MIN_DRIVER_MAJOR=580).
# ORT 1.30 + CUDA 13.0 runtime remain ABI-compatible (SONAME .so.13).
WORKER_CUDA_PACKAGES = (
    "nvidia-cuda-runtime==13.0.88",
    "nvidia-cublas==13.0.2.14",
    "nvidia-cudnn-cu13==9.13.1.26",
    "nvidia-cuda-nvrtc==13.0.88",
    "nvidia-cufft==12.0.0.15",
    "nvidia-curand==10.4.0.35",
    "nvidia-nvjitlink==13.0.88",
)
WORKER_NVIDIA_PACKAGES = ("onnxruntime-gpu==1.30.0", "numpy==2.5.3", "huggingface-hub>=0.34")
WORKER_NVIDIA_NO_DEPS = ("onnx-asr==0.12.0",)
WORKER_CPU_PACKAGES = ("onnxruntime==1.30.0", "numpy==2.5.3", "huggingface-hub>=0.34", "onnx-asr==0.12.0")

SILERO_TAG = "v6.2.1"
SILERO_URL = f"https://raw.githubusercontent.com/snakers4/silero-vad/{SILERO_TAG}/src/silero_vad/data/silero_vad.onnx"
SILERO_BYTES = 2_327_524
SILERO_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"

MODEL_REPOS = {
    "nemo-parakeet-tdt-0.6b-v2": "istupakov/parakeet-tdt-0.6b-v2-onnx",
    "nemo-parakeet-tdt-0.6b-v3": "istupakov/parakeet-tdt-0.6b-v3-onnx",
}

type JsonObject = dict[str, Any]

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
if not sys.stdout.isatty():
    RESET = BOLD = GREEN = RED = YELLOW = ""
VERBOSE = False
OFFLINE = False
LOG_FILE: Path | None = None


class InstallError(RuntimeError):
    pass


def log_step(msg: str) -> None:
    print(f"{BOLD}==> {msg}{RESET}", flush=True)


def log_ok(msg: str) -> None:
    if LOG_FILE:
        with LOG_FILE.open("a") as stream:
            stream.write(msg + "\n")
    if VERBOSE:
        print(f"{GREEN}  ok {RESET}{msg}", flush=True)


def log_warn(msg: str) -> None:
    print(f"{YELLOW}  ** {RESET}{msg}", flush=True)


def report_error(exc: BaseException) -> None:
    message = str(exc)
    if LOG_FILE:
        with LOG_FILE.open("a") as stream:
            stream.write("ERROR: " + message + "\n")
    print(f"Installation failed: {message if VERBOSE else message.splitlines()[0] if message else type(exc).__name__}", file=sys.stderr)
    if LOG_FILE:
        print(f"Log: {LOG_FILE}", file=sys.stderr)


def run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float = 3600.0,
    check: bool = True,
    quiet: bool = True,
) -> subprocess.CompletedProcess[str]:
    child_env = dict(os.environ)
    child_env.update(env or {})
    child_env["ORT_DISABLE_TELEMETRY"] = "1"
    if OFFLINE:
        child_env["UV_OFFLINE"] = "1"
        child_env["HF_HUB_OFFLINE"] = "1"
    if LOG_FILE:
        with LOG_FILE.open("a") as stream:
            stream.write("$ " + shlex.join(cmd) + "\n")
    if not quiet and LOG_FILE:
        with LOG_FILE.open("a") as stream:
            with subprocess.Popen(cmd, env=child_env, cwd=cwd, stdout=subprocess.PIPE if VERBOSE else stream,
                                  stderr=subprocess.STDOUT, text=True) as proc:
                reader: threading.Thread | None = None
                if VERBOSE:
                    def show_output() -> None:
                        assert proc.stdout is not None
                        for line in proc.stdout:
                            stream.write(line)
                            stream.flush()
                            print(line, end="", flush=True)
                    reader = threading.Thread(target=show_output, daemon=True)
                    reader.start()
                try:
                    started = time.monotonic()
                    while True:
                        try:
                            code = proc.wait(timeout=min(20.0, timeout))
                            break
                        except subprocess.TimeoutExpired:
                            elapsed = time.monotonic() - started
                            if elapsed >= timeout:
                                proc.kill()
                                proc.wait()
                                raise InstallError(f"Command timed out after {timeout:g}s: {cmd[0]}")
                            if sys.stdout.isatty():
                                print(f"\r  Working… {int(elapsed)}s", end="", flush=True)
                    if sys.stdout.isatty() and time.monotonic() - started >= 20:
                        print("\r" + " " * 32 + "\r", end="", flush=True)
                except BaseException:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
                    raise
                finally:
                    if reader is not None:
                        reader.join(timeout=5)
        res = subprocess.CompletedProcess(cmd, code)
    else:
        res = subprocess.run(cmd, env=child_env, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
        output = (res.stdout or "") + (res.stderr or "")
        if LOG_FILE:
            with LOG_FILE.open("a") as stream:
                stream.write(output)
        if VERBOSE and output:
            print(output, end="", flush=True)
    if check and res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        if not detail and LOG_FILE:
            with LOG_FILE.open("rb") as stream:
                stream.seek(max(0, LOG_FILE.stat().st_size - 4000))
                detail = stream.read().decode(errors="replace")
        raise InstallError(f"Command failed ({res.returncode}): {Path(cmd[0]).name}\n{detail[-4000:]}")
    return res


# ------------------------------------------------------------------ hardware
def detect_hardware() -> tuple[str, JsonObject]:
    """Auto-detect: nvidia > amd > cpu. Never fails; returns (kind, info)."""
    # NVIDIA: nvidia-smi must work and report a GPU with driver >= 580.
    try:
        smi = shutil.which("nvidia-smi")
        if smi:
            res = subprocess.run(
                [smi, "--query-gpu=index,name,driver_version,memory.total,compute_cap", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                gpus: list[JsonObject] = []
                for line in res.stdout.splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) != 5:
                        continue
                    try:
                        gpus.append({"index": int(parts[0]), "name": parts[1],
                                     "driver": parts[2], "memory_total_mib": int(float(parts[3])),
                                     "compute_cap": float(parts[4])})
                    except ValueError:
                        continue
                if gpus:
                    try:
                        major = int(gpus[0]["driver"].split(".")[0])
                    except (ValueError, IndexError):
                        major = 0
                    usable = [g for g in gpus if g["compute_cap"] >= 7.5 and g["memory_total_mib"] >= 1792]
                    if major >= MIN_DRIVER_MAJOR and usable:
                        return "nvidia", {"gpus": usable, "driver_major": major}
    except (OSError, subprocess.SubprocessError):
        pass
    # AMD: ROCm stack, /dev/kfd, or AMD VGA in lspci. Acceleration is
    # opportunistic (CPU fallback always works), so detection is lenient.
    try:
        if shutil.which("rocm-smi") or Path("/dev/kfd").exists():
            return "amd", {"reason": "rocm-smi or /dev/kfd present"}
        lspci = shutil.which("lspci")
        if lspci:
            res = subprocess.run([lspci, "-nn"], capture_output=True, text=True, timeout=10, check=False)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    low = line.lower()
                    if ("vga" in low or "3d" in low or "display" in low) and ("1002:" in line or " amd/" in low or "amd " in low and "advanced micro" in low):
                        return "amd", {"reason": f"lspci: {line.strip()[:100]}"}
    except (OSError, subprocess.SubprocessError):
        pass
    return "cpu", {}


def query_nvidia_gpu(gpu_device: int) -> tuple[int, str]:
    """Return (total_mib, driver) for the requested NVIDIA index. Raises if missing."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        raise InstallError("nvidia-smi not found; install nvidia-utils for --hardware nvidia.")
    res = run([smi, "--query-gpu=index,memory.total,driver_version,compute_cap", "--format=csv,noheader,nounits"], timeout=30)
    for line in res.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        try:
            index = int(parts[0])
            total_mb = int(float(parts[1]))
        except ValueError:
            continue
        if index == gpu_device:
            driver = parts[2]
            try:
                major = int(driver.split(".")[0])
            except (ValueError, IndexError):
                raise InstallError(f"Cannot parse NVIDIA driver version: {driver!r}")
            if major < MIN_DRIVER_MAJOR:
                raise InstallError(f"NVIDIA driver {driver} < required {MIN_DRIVER_MAJOR}+ for CUDA 13.")
            if float(parts[3]) < 7.5:
                raise InstallError("CUDA 13 requires Turing or newer (compute capability 7.5+); use --hardware cpu")
            log_ok(f"GPU {gpu_device}: driver {driver}, {total_mb} MiB VRAM")
            return total_mb, driver
    raise InstallError(f"nvidia-smi did not report GPU index {gpu_device}.")


def choose_vram_limit(total_mb: int, requested_mb: int | None) -> int:
    safe_max = total_mb - 768
    if safe_max < 1024:
        raise InstallError(f"GPU has insufficient VRAM ({total_mb} MiB). Need >= 1792 MiB total.")
    if requested_mb is not None:
        if requested_mb <= 0:
            raise InstallError("GPU arena budget must be positive.")
        if requested_mb > safe_max:
            raise InstallError(f"Requested {requested_mb} MiB exceeds safe ceiling {safe_max} MiB.")
        return requested_mb
    return min(round(total_mb * 0.70), safe_max)


# ------------------------------------------------------------------ preflight
def assert_runtime() -> None:
    log_step("Checking system")
    missing = [name for name in REQUIRED_SOURCES if not (SOURCE_DIR / name).is_file()]
    if missing:
        raise InstallError("Incomplete source directory; missing: " + ", ".join(missing))
    if os.geteuid() == 0:
        raise InstallError("Do not run as root; installs into your user home.")
    try:
        os_release = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallError(f"Cannot read /etc/os-release: {exc}") from exc
    release = dict(line.split("=", 1) for line in os_release.splitlines() if "=" in line)
    ident = release.get("ID", "").strip().strip('"')
    like = release.get("ID_LIKE", "")
    pretty = release.get("PRETTY_NAME", "").strip().strip('"')
    if ident != "arch" and "arch" not in like:
        raise InstallError(f"Targets Arch Linux rolling only (found {pretty or ident}).")
    kernel = platform.release()
    match = re.match(r"^(\d+)\.(\d+)", kernel)
    if not match:
        raise InstallError(f"Cannot parse kernel release: {kernel}")
    if (int(match.group(1)), int(match.group(2))) < MIN_KERNEL:
        raise InstallError(f"Kernel {kernel} < required {MIN_KERNEL[0]}.{MIN_KERNEL[1]}+.")
    log_ok(f"Arch / kernel {kernel}")
    if sys.version_info < MIN_PYTHON:
        raise InstallError(f"CPython {MIN_PYTHON[0]}.{MIN_PYTHON[1]}.{MIN_PYTHON[2]}+ required (found {sys.version.split()[0]}).")
    gil = getattr(sys, "_is_gil_enabled", None)
    if gil is None or not gil():
        raise InstallError("GIL-enabled CPython required; the installed ONNX Runtime wheels require the GIL ABI.")
    log_ok(f"CPython {sys.version.split()[0]} (GIL enabled)")
    if not os.environ.get("XDG_RUNTIME_DIR"):
        raise InstallError("XDG_RUNTIME_DIR unset; run inside a systemd user session.")
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise InstallError("WAYLAND_DISPLAY unset; Dusky types via wtype on Wayland.")
    log_ok(f"Wayland {os.environ['WAYLAND_DISPLAY']}")


def install_pacman_packages(packages: tuple[str, ...], skip: bool) -> None:
    log_step("Checking system packages via pacman")
    missing = [p for p in packages if subprocess.run(["pacman", "-Qq", p], capture_output=True).returncode != 0]
    if not missing:
        log_ok("System dependencies present.")
        return
    if skip:
        log_warn(f"Missing (skipped by --skip-pacman): {' '.join(missing)}")
        return
    if OFFLINE:
        raise InstallError("Offline setup needs preinstalled packages: " + " ".join(missing))
    log_warn(f"Installing: {' '.join(missing)}")
    run(["sudo", "pacman", "-S", "--needed", "--noconfirm", *missing], quiet=False, timeout=1800)
    log_ok("System dependencies installed.")


# ------------------------------------------------------------------ venvs
def install_python_environments(stage: Path, hardware: str) -> tuple[Path, Path]:
    log_step("Preparing Python environments")
    uv = shutil.which("uv")
    if not uv:
        raise InstallError("uv is required on PATH (pacman -S uv).")
    env = dict(os.environ)
    env["UV_PYTHON_DOWNLOADS"] = "never"
    # Detailed download output goes to the install log; long operations
    # show elapsed time in the terminal.
    env.pop("UV_NO_PROGRESS", None)
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    main_venv = stage / ".venv-main"
    worker_venv = stage / ".venv-worker"
    log_ok("Creating daemon and ASR environments")
    run([uv, "venv", "--relocatable", "--python", sys.executable, str(main_venv)], env=env)
    run([uv, "venv", "--relocatable", "--python", sys.executable, str(worker_venv)], env=env)
    main_py = main_venv / "bin" / "python"
    worker_py = worker_venv / "bin" / "python"
    log_ok("Installing CPU daemon packages")
    run([uv, "pip", "install", "--python", str(main_py), *MAIN_PACKAGES], env=env, quiet=False, timeout=1800)
    if hardware == "nvidia":
        log_step("Installing CUDA runtime")
        run([uv, "pip", "install", "--python", str(worker_py), *WORKER_CUDA_PACKAGES], env=env, quiet=False, timeout=3600)
        log_ok("Installing GPU inference packages")
        run([uv, "pip", "install", "--python", str(worker_py), *WORKER_NVIDIA_PACKAGES], env=env, quiet=False, timeout=1800)
        log_ok("Installing ASR library")
        run([uv, "pip", "install", "--python", str(worker_py), "--no-deps", *WORKER_NVIDIA_NO_DEPS], env=env, quiet=False, timeout=600)
    else:
        log_ok("Installing CPU inference packages")
        run([uv, "pip", "install", "--python", str(worker_py), *WORKER_CPU_PACKAGES], env=env, quiet=False, timeout=1800)
    run([uv, "pip", "check", "--python", str(main_py)], env=env)
    run([uv, "pip", "check", "--python", str(worker_py)], env=env)
    log_ok("Virtual environments built.")
    return main_py, worker_py


def verify_namespaces(main_py: Path, worker_py: Path, hardware: str) -> None:
    log_step("Checking runtime isolation")
    probe = (
        "import importlib.metadata as m, sys; "
        "owners = sorted(set(m.packages_distributions().get('onnxruntime', []))); "
        "assert owners == [sys.argv[1]], f'Namespace collision: {owners}'"
    )
    run([str(main_py), "-c", probe, "onnxruntime"], env={"CUDA_VISIBLE_DEVICES": "-1"})
    expected = "onnxruntime-gpu" if hardware == "nvidia" else "onnxruntime"
    run([str(worker_py), "-c", probe, expected],
        env={"CUDA_VISIBLE_DEVICES": "0" if hardware == "nvidia" else "-1"})
    log_ok("ORT namespaces partitioned.")


def download_silero(stage: Path, expected_sha256: str | None) -> str:
    log_step("Preparing voice activity model")
    target_dir = stage / "models"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "silero_vad.onnx"
    data = b""
    for cached in (APP_DIR / "models/silero_vad.onnx", VAD_CACHE):
        if cached.is_file():
            candidate = cached.read_bytes()
            if hashlib.sha256(candidate).hexdigest() == (expected_sha256 or SILERO_SHA256).lower():
                data = candidate
                break
    if not data:
        if OFFLINE:
            raise InstallError("Offline setup needs a valid cached Silero model.")
        req = urllib.request.Request(SILERO_URL, headers={"User-Agent": "dusky-installer/2"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    if len(data) != SILERO_BYTES:
        raise InstallError(f"Silero size mismatch: expected {SILERO_BYTES}, got {len(data)}")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256:
        if digest != expected_sha256.lower():
            raise InstallError(f"Silero SHA-256 mismatch: {digest} != {expected_sha256.lower()}")
    elif digest != SILERO_SHA256:
        raise InstallError(f"Silero SHA-256 mismatch: {digest}")
    target.write_bytes(data)
    VAD_CACHE.parent.mkdir(parents=True, exist_ok=True)
    VAD_CACHE.write_bytes(data)
    os.chmod(target, 0o644)
    log_ok(f"Silero verified ({digest[:16]}...)")
    return digest


def prefetch_model(worker_py: Path, model: str, model_dir: Path, quantization: str) -> None:
    expected_size = "~660 MiB" if quantization == "int8" else "~3.1 GiB"
    log_step(f"Preparing Parakeet model ({quantization}, {expected_size})")
    repo = MODEL_REPOS.get(model)
    if repo is None:
        raise InstallError(f"Unknown model {model!r}; known: {sorted(MODEL_REPOS)}")
    model_dir.mkdir(parents=True, exist_ok=True)
    # Phase 1: snapshot_download the HF repo with allow_patterns matching the requested quantization.
    # Without allow_patterns, snapshot_download pulls uncompressed FP32 weights (.onnx.data ~2.4GB)
    # even when only int8 (~650MB) is requested.
    if quantization == "int8":
        patterns = ["config.json", "vocab.txt", "nemo*.onnx", "*.int8.onnx"]
    elif quantization in ("none", "fp32"):
        patterns = ["config.json", "vocab.txt", "nemo*.onnx", "encoder-model.onnx*", "decoder_joint-model.onnx"]
    elif quantization == "fp16":
        patterns = ["config.json", "vocab.txt", "nemo*.onnx", "*.fp16.onnx*"]
    else:
        patterns = ["*"]
    dl_code = (
        "import sys, json; from huggingface_hub import snapshot_download; "
        "patterns = json.loads(sys.argv[3]); "
        "p = snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2], allow_patterns=patterns); print(p)"
    )
    dl_env = dict(os.environ)
    dl_env["HF_HUB_OFFLINE"] = "1" if OFFLINE else "0"
    dl_env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    if not OFFLINE:
        run([str(worker_py), "-c", dl_code, repo, str(model_dir), json.dumps(patterns)], env=dl_env, timeout=5400, quiet=False)
    onnx_files = list(model_dir.rglob("*.onnx"))
    if not onnx_files:
        raise InstallError(f"No .onnx graphs found in {model_dir} after download of {repo}")
    log_ok(f"Downloaded {repo} ({len(onnx_files)} graphs). Smoke-loading...")
    # Phase 2: smoke-load via CPU EP to prove the graph is valid.
    # NOTE: .venv-worker on nvidia holds onnxruntime-gpu, whose import
    # requires libcudart.so.13 at dlopen time even for CPU EP. Preload
    # the PyPI CUDA libs RTLD_GLOBAL first; no-op on cpu workers.
    code = """
import ctypes, importlib.metadata, os, pathlib, sys
from pathlib import Path as _P
_ORDER = ("libnvJitLink.so.13","libcudart.so.13","libnvrtc-builtins.so.13","libnvrtc.so.13",
 "libcublasLt.so.13","libcublas.so.13","libcufft.so.12","libcurand.so.10",
 "libcudnn_graph.so.9","libcudnn_engines_precompiled.so.9","libcudnn_ops.so.9",
 "libcudnn_adv.so.9","libcudnn_cnn.so.9","libcudnn.so.9")
_DISTS = ("nvidia-cuda-runtime","nvidia-cublas","nvidia-cudnn-cu13","nvidia-cuda-nvrtc","nvidia-cufft","nvidia-curand","nvidia-nvjitlink")
_idx = {}
for _d in _DISTS:
    try: _dist = importlib.metadata.distribution(_d)
    except importlib.metadata.PackageNotFoundError: continue
    for _f in _dist.files or ():
        _p = _P(_dist.locate_file(_f)).resolve()
        if _p.is_file() and ".so" in _p.name:
            _idx.setdefault(_p.name, _p)
            for _s in _ORDER:
                if _p.name == _s or _p.name.startswith(_s + "."): _idx.setdefault(_s, _p)
for _s in _ORDER:
    _m = _idx.get(_s)
    if _m:
        try: ctypes.CDLL(str(_m), mode=ctypes.RTLD_GLOBAL | os.RTLD_NOW)
        except OSError: pass
import onnx_asr
import onnxruntime as ort
opts = ort.SessionOptions()
opts.intra_op_num_threads = min(8, os.process_cpu_count() or 1)
opts.inter_op_num_threads = 1
q = None if sys.argv[3] in ('none', 'fp32') else sys.argv[3]
model = onnx_asr.load_model(
    sys.argv[1],
    pathlib.Path(sys.argv[2]),
    quantization=q,
    providers=['CPUExecutionProvider'],
    sess_options=opts,
    preprocessor_config={'max_concurrent_workers': 1, 'use_numpy_preprocessors': True}
)
res = model.recognize(__import__('numpy').zeros(16000, dtype='float32'), sample_rate=16000)
assert isinstance(res, str)
"""
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = "-1"
    env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    run([str(worker_py), "-c", code, model, str(model_dir), quantization], env=env, timeout=1800, quiet=False)
    log_ok("Model prefetched and smoke-tested.")


def verify_cpu_vad(main_py: Path, vad_path: Path) -> None:
    log_step("Testing voice activity model")
    code = """
import pathlib, sys, numpy as np, onnxruntime as ort
opts = ort.SessionOptions()
opts.intra_op_num_threads = 1
opts.inter_op_num_threads = 1
session = ort.InferenceSession(sys.argv[1], sess_options=opts, providers=['CPUExecutionProvider'])
assert session.get_providers() == ['CPUExecutionProvider']
out = session.run(None, {
    'input': np.zeros((1, 576), dtype=np.float32),
    'state': np.zeros((2, 1, 128), dtype=np.float32),
    'sr': __import__('numpy').array(16000, dtype=__import__('numpy').int64)
})
maps = pathlib.Path('/proc/self/maps').read_text().casefold()
for f in ('libcuda.so', 'libcudart.so', 'libcublas', 'libcudnn', 'onnxruntime_providers_cuda'):
    if f in maps:
        raise SystemExit(f'Forbidden CUDA map: {f}')
"""
    run([str(main_py), "-c", code, str(vad_path)], env={"CUDA_VISIBLE_DEVICES": "-1"})
    log_ok("CPU VAD clean.")


def verify_worker(worker_py: Path, stage: Path, hardware: str, gpu_device: int, config_path: Path) -> JsonObject:
    log_step("Testing speech recognition")
    env = dict(os.environ)
    if hardware == "nvidia":
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_device)
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        env["CUDA_MODULE_LOADING"] = "LAZY"
    else:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
    env["HF_HUB_OFFLINE"] = "1"
    res = run([str(worker_py), str(stage / "dusky_worker.py"), "--config", str(config_path), "--self-test"],
              env=env, cwd=stage, timeout=600, quiet=True)
    stdout = res.stdout or ""
    try:
        report = json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise InstallError("Worker self-test returned no valid report") from exc
    if not isinstance(report, dict) or report.get("ok") is not True:
        raise InstallError(f"Worker self-test failed: {report}")
    log_ok(f"Worker self-test passed ({hardware}).")
    return report


def deploy_stage(stage: Path, *, manage_service: bool = True, was_active: bool = False) -> Path | None:
    log_step("Deploying application")
    if manage_service:
        stopped = subprocess.run(["systemctl", "--user", "stop", UNIT_NAME], capture_output=True, check=False)
        if was_active and stopped.returncode != 0:
            raise InstallError("Could not stop the running service before deployment")
    backup: Path | None = None
    try:
        if APP_DIR.exists():
            backup = APP_DIR.parent / f"dusky-stt.backup-{time.time_ns()}"
            APP_DIR.rename(backup)
        stage.rename(APP_DIR)
    except BaseException as exc:
        if backup and backup.exists() and not APP_DIR.exists():
            backup.rename(APP_DIR)
        if manage_service and was_active:
            run(["systemctl", "--user", "start", UNIT_NAME])
        if not isinstance(exc, Exception):
            raise
        raise InstallError(f"Atomic rename failed: {exc}") from exc
    log_ok("Deployed.")
    return backup


def install_entrypoints(*, start_service: bool = True, enable_service: bool = True) -> None:
    log_step("Installing entry points and unit")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    trigger_dest = BIN_DIR / "dusky_trigger"
    trigger_dest.write_text('#!/usr/bin/env bash\n'
        'app_dir=${DUSKY_APP_DIR:-' + shlex.quote(str(APP_DIR)) + '}\n'
        'export DUSKY_APP_DIR="$app_dir"\n'
        'exec "$app_dir/.venv-main/bin/python" "$app_dir/dusky_trigger.py" "$@"\n', encoding="utf-8")
    os.chmod(trigger_dest, 0o755)
    verify_dest = BIN_DIR / "dusky_verify"
    verify_dest.write_text('#!/usr/bin/env bash\n'
        'app_dir=${DUSKY_APP_DIR:-' + shlex.quote(str(APP_DIR)) + '}\n'
        'export DUSKY_APP_DIR="$app_dir"\n'
        'exec bash "$app_dir/dusky_verify.sh" "$@"\n', encoding="utf-8")
    os.chmod(verify_dest, 0o755)
    unit_dest = UNIT_DIR / UNIT_NAME
    unit = (APP_DIR / UNIT_NAME).read_text()
    def quote(path: Path, *, command: bool = False) -> str:
        value = str(path).replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"')
        return '"' + (value.replace('$', '$$') if command else value) + '"'
    for suffix in ("/.venv-main/bin/python", "/dusky_main.py", "/config.json"):
        unit = unit.replace('"%h/.local/lib/dusky-stt' + suffix + '"', quote(APP_DIR / suffix.lstrip('/'), command=True))
    unit = unit.replace('WorkingDirectory=%h/.local/lib/dusky-stt', 'WorkingDirectory=' + str(APP_DIR).replace('%', '%%'))
    unit = unit.replace('ReadOnlyPaths="%h/.local/lib/dusky-stt"', 'ReadOnlyPaths=' + quote(APP_DIR))
    cfg = json.loads((APP_DIR / "config.json").read_text())
    state = Path(cfg["state_dir"]).expanduser()
    state.mkdir(parents=True, exist_ok=True)
    unit = unit.replace('ReadWritePaths="%h/.local/state/dusky-stt"', 'ReadWritePaths=' + quote(state))
    unit_dest.write_text(unit)
    os.chmod(unit_dest, 0o644)
    run(["systemd-analyze", "--user", "verify", str(unit_dest)])
    if start_service:
        run(["systemctl", "--user", "daemon-reload"])
        if enable_service:
            run(["systemctl", "--user", "enable", "--now", UNIT_NAME])
        else:
            run(["systemctl", "--user", "start", UNIT_NAME])


def choose_interactive(detected: str, info: JsonObject) -> str:
    number = {"nvidia": "1", "cpu": "2", "amd": "3"}[detected]
    print("\n  Backend")
    print("  1  NVIDIA CUDA" + (" · recommended" if number == "1" else ""))
    print("  2  CPU" + (" · recommended" if number == "2" else ""))
    print("  3  AMD · CPU runtime\n")
    try:
        raw_hw = input(f"  Choose [{number}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise InstallError("Installation cancelled by user.")
    hw_map = {"1": "nvidia", "2": "cpu", "3": "amd", "": detected}
    if raw_hw not in hw_map:
        raise InstallError("Choose 1, 2 or 3.")
    return hw_map[raw_hw]


INSTALL_HELP = """Dusky Parakeet · speech setup

  Usage  dusky_installer.py [OPTIONS]
  Quick  dusky_installer.py --yes

Setup
  --hardware NAME       auto | nvidia | cpu | amd
  --model NAME          nemo-parakeet-tdt-0.6b-v2 | -v3
  --quantization NAME   int8 (default) | fp16 | fp32 | none
  -y, --yes             Use defaults without prompts
  --offline             Use cached wheels and local models
  --verbose             Show detailed installation output
  --no-systemd          Install without starting/enabling the service
  --skip-pacman         Skip missing system packages

Hardware & capture
  --gpu-device N        NVIDIA device index
  --gpu-mem-limit-mb N  Provider arena budget, not total VRAM
  --cpu-threads N       CPU inference threads (0 = automatic)
  --input-device NAME   Microphone device; auto = default
  --output-mode NAME    clipboard | both | realtime-both
  --idle-timeout-seconds N  Worker idle timeout

Paths & maintenance
  --model-dir DIR       Cached ASR models
  --state-dir DIR       Transcripts and job results
  --silero-sha256 HASH  Override the VAD checksum
  --uninstall           Remove installation; retain models/transcripts
  -h, --help            Show this help

Existing settings are preserved unless explicitly overridden.
"""


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="dusky_installer", usage="%(prog)s [OPTIONS]", add_help=False)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--hardware", default=None, choices=("auto", "cpu", "nvidia", "amd"))
    p.add_argument("--model", default=None, choices=sorted(MODEL_REPOS))
    p.add_argument("--quantization", default=None, choices=("int8", "fp16", "fp32", "none"))
    p.add_argument("-y", "--yes", action="store_true")
    p.add_argument("--gpu-device", type=int, default=None)
    p.add_argument("--gpu-mem-limit-mb", type=int, default=None)
    p.add_argument("--cpu-threads", type=int, default=None)
    p.add_argument("--input-device", default=None)
    p.add_argument("--state-dir", default=None)
    p.add_argument("--model-dir", default=None)
    p.add_argument("--output-mode", default=None, choices=("clipboard", "both", "realtime-both"))
    p.add_argument("--keep-audio", action="store_true", default=None, help=argparse.SUPPRESS)
    p.add_argument("--idle-timeout-seconds", type=float, default=None)
    p.add_argument("--silero-sha256", default=None)
    p.add_argument("--skip-pacman", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--no-systemd", action="store_true")
    p.add_argument("--uninstall", action="store_true")
    args = p.parse_args(argv)
    if args.help:
        print(INSTALL_HELP, end="")
        p.exit()
    return args


def uninstall() -> int:
    log_step("Uninstalling Dusky STT")
    subprocess.run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    for p in (UNIT_DIR / UNIT_NAME, BIN_DIR / "dusky_trigger", BIN_DIR / "dusky_verify"):
        p.unlink(missing_ok=True)
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    log_ok("Uninstalled (transcripts/models retained).")
    return 0


def rollback(backup: Path | None, entries: dict[Path, tuple[bytes, int] | None],
             *, manage_service: bool, was_active: bool, was_enabled: bool) -> None:
    if manage_service:
        subprocess.run(["systemctl", "--user", "stop", UNIT_NAME], capture_output=True, check=False)
        if not was_enabled:
            subprocess.run(["systemctl", "--user", "disable", UNIT_NAME], capture_output=True, check=False)
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    if backup is not None and backup.exists():
        backup.rename(APP_DIR)
    for path, saved in entries.items():
        if saved is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(saved[0])
            path.chmod(saved[1])
    if manage_service:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=False)
        if was_enabled:
            subprocess.run(["systemctl", "--user", "enable", UNIT_NAME], capture_output=True, check=False)
        if was_active:
            subprocess.run(["systemctl", "--user", "start", UNIT_NAME], capture_output=True, check=False)


def main(argv: list[str]) -> int:
    global VERBOSE, OFFLINE, LOG_FILE
    args = parse_arguments(argv)
    VERBOSE, OFFLINE = args.verbose, args.offline
    if args.uninstall:
        return uninstall()
    LOG_FILE = DEFAULT_STATE_DIR / "install.log"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("")
    assert_runtime()
    previous: JsonObject = {}
    if (APP_DIR / "config.json").is_file():
        previous = json.loads((APP_DIR / "config.json").read_text())
        if not isinstance(previous, dict) or previous.get("schema_version") != SCHEMA_VERSION:
            raise InstallError("Existing config must have schema_version=2")
    detected, info = detect_hardware()
    detected_device = info["gpus"][0]["index"] if detected == "nvidia" else 0
    defaults = {"model": "nemo-parakeet-tdt-0.6b-v2", "quantization": "int8", "gpu_device": detected_device,
                "state_dir": str(DEFAULT_STATE_DIR), "output_mode": "realtime-both", "keep_audio": False,
                "idle_timeout_seconds": 90.0, "cpu_threads": 0, "input_device": None}
    for key, default in defaults.items():
        if getattr(args, key) is None:
            value = previous.get(key, default)
            if key == "quantization" and value is None:
                value = "fp32"
            setattr(args, key, value)
    if args.gpu_device < 0 or args.cpu_threads < 0:
        raise InstallError("Device index and CPU threads must be nonnegative")
    if not math.isfinite(args.idle_timeout_seconds) or args.idle_timeout_seconds < 5:
        raise InstallError("Worker idle timeout must be finite and at least 5 seconds")
    if args.input_device == "auto":
        args.input_device = None
    if sys.stdin.isatty() and not args.yes and args.hardware is None:
        hardware = choose_interactive(previous.get("hardware", detected), info)
    else:
        hardware = (previous.get("hardware", detected) if args.hardware is None
                    else detected if args.hardware == "auto" else args.hardware)
    quantization = args.quantization

    log_step(f"Backend: {hardware}")

    gpu_limit = 4096
    if hardware == "nvidia":
        total_mb, _driver = query_nvidia_gpu(args.gpu_device)
        # 2GB-VRAM guard: fp32 encoder alone is ~2.5 GB and can never fit;
        # fail fast with a clear message instead of a post-download OOM.
        if total_mb < 3072 and quantization in ("none", "fp32"):
            raise InstallError(
                f"GPU has {total_mb} MiB VRAM: fp32 model needs ~2.5 GB just for "
                "weights. Re-run with --quantization int8 (recommended) or fp16.")
        if total_mb < 3072 and quantization == "fp16":
            log_warn(f"Only {total_mb} MiB VRAM with fp16 (~1.25 GB weights + CUDA "
                     "context + activations): tight. Prefer --quantization int8.")
        requested = args.gpu_mem_limit_mb
        if requested is None and previous.get("hardware") == "nvidia":
            requested = previous.get("gpu_mem_limit_mb")
            if requested is not None and requested > total_mb - 768:
                log_warn("Adjusting the saved GPU arena budget to this device.")
                requested = None
        gpu_limit = choose_vram_limit(total_mb, requested)
    elif hardware == "amd":
        log_ok("AMD system: this installation uses CPU inference.")

    packages = BASE_PACKAGES + (NVIDIA_PACKAGES if hardware == "nvidia" else ())
    install_pacman_packages(packages, args.skip_pacman)

    APP_DIR.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".dusky-stage-", dir=APP_DIR.parent))
    os.chmod(stage, 0o700)
    backup: Path | None = None
    deployed = False
    paths = (BIN_DIR / "dusky_trigger", BIN_DIR / "dusky_verify", UNIT_DIR / UNIT_NAME)
    entries = {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) if p.exists() else None for p in paths}
    was_active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", UNIT_NAME], capture_output=True).returncode == 0
    was_enabled = subprocess.run(["systemctl", "--user", "is-enabled", "--quiet", UNIT_NAME], capture_output=True).returncode == 0
    try:
        for name in REQUIRED_SOURCES:
            shutil.copy2(SOURCE_DIR / name, stage / name)
        for name in ("dusky_main.py", "dusky_worker.py", "dusky_trigger.py", "dusky_rec_indicator.py", "dusky_verify.sh"):
            os.chmod(stage / name, 0o755)
        main_py, worker_py = install_python_environments(stage, hardware)
        silero_hash = download_silero(stage, args.silero_sha256)
        verify_namespaces(main_py, worker_py, hardware)
        verify_cpu_vad(main_py, stage / "models" / "silero_vad.onnx")
        model_dir = Path(args.model_dir or previous.get("model_dir", str(DEFAULT_MODEL_ROOT / args.model))).expanduser()
        if args.model != previous.get("model", args.model) and args.model_dir is None:
            model_dir = DEFAULT_MODEL_ROOT / args.model
        prefetch_model(worker_py, args.model, model_dir, quantization)
        config: JsonObject = {
            "schema_version": SCHEMA_VERSION,
            "hardware": hardware,
            "model": args.model,
            "model_dir": str(model_dir),
            "quantization": None if quantization in ("none", "fp32") else quantization,
            "gpu_device": args.gpu_device,
            "gpu_mem_limit_mb": gpu_limit,
            "input_device": args.input_device,
            "state_dir": str(Path(args.state_dir).expanduser()),
            "output_mode": args.output_mode,
            "push_type_at_end": True,
            "keep_audio": args.keep_audio,
            "idle_timeout_seconds": args.idle_timeout_seconds,
            "max_inflight_requests": 2,
            "realtime_interval_seconds": 1.2,
            "finalize_timeout_seconds": 120.0,
            "max_request_seconds": 30.0,
            "max_phrase_seconds": 15.0,
            # 20 s (not 25 s): Parakeet TDT is trained on short utterances and
            # onnx-asr caps at 20-30 s per forward; 20 s cuts O(T^2) attention
            # peak ~1.5x vs 25 s on 2 GB VRAM with fewer mid-word seams.
            "file_chunk_seconds": 20.0,
            "pre_roll_seconds": 0.32,
            "phrase_silence_seconds": 0.80,
            "vad_onset_seconds": 0.096,
            "vad_min_speech_seconds": 0.25,
            "vad_start_threshold": 0.50,
            "vad_end_threshold": 0.35,
            "stable_holdback_words": 2,
            "silero_sha256": silero_hash,
            "vad_model_path": "models/silero_vad.onnx",
            "worker_python": ".venv-worker/bin/python",
            "worker_script": "dusky_worker.py",
        }
        config = {**config, **previous, **{key: config[key] for key in (
            "hardware", "model", "model_dir", "quantization", "gpu_device", "gpu_mem_limit_mb",
            "input_device", "state_dir", "output_mode", "keep_audio", "idle_timeout_seconds", "silero_sha256",
            "schema_version", "vad_model_path", "worker_python", "worker_script")}}
        config["cpu_threads"] = args.cpu_threads
        cfg_path = stage / "config.json"
        cfg_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        os.chmod(cfg_path, 0o600)
        report = verify_worker(worker_py, stage, hardware, args.gpu_device, cfg_path)
        manifest = {
            "schema_version": SCHEMA_VERSION, "hardware": hardware, "detected": detected,
            "kernel": platform.release(), "python": sys.version.split()[0],
            "silero_sha256": silero_hash, "model": args.model,
            "self_test": report, "time": int(time.time()),
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        backup = deploy_stage(stage, manage_service=not args.no_systemd, was_active=was_active)
        deployed = True
        install_entrypoints(start_service=not args.no_systemd,
                            enable_service=was_enabled if entries[UNIT_DIR / UNIT_NAME] else True)
        if backup and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        print(f"\nReady · {hardware}\n\n  Use     dusky_trigger\n  Config  {APP_DIR / 'config.json'}\n  Log     {LOG_FILE}")
        return 0
    except KeyboardInterrupt:
        shutil.rmtree(stage, ignore_errors=True)
        if deployed:
            rollback(backup, entries, manage_service=not args.no_systemd,
                     was_active=was_active, was_enabled=was_enabled)
        print(f"\n{YELLOW}Installation cancelled by user (Ctrl-C). Stale stage removed; re-run to resume.{RESET}",
              file=sys.stderr)
        return 130
    except BaseException as exc:
        shutil.rmtree(stage, ignore_errors=True)
        if deployed:
            rollback(backup, entries, manage_service=not args.no_systemd,
                     was_active=was_active, was_enabled=was_enabled)
        if isinstance(exc, InstallError):
            report_error(exc)
            return 1
        raise


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (InstallError, OSError, ValueError, subprocess.SubprocessError) as exc:
        report_error(exc)
        sys.exit(1)
