#!/usr/bin/env python3
"""Atomic Arch Linux installer for the CUDA 13 Dusky STT architecture.

Two virtual environments are deliberate, not duplication:

* .venv-main owns the CPU onnxruntime namespace and can never import CUDA EP.
* .venv-worker owns the onnxruntime-gpu namespace and CUDA 13 PyPI runtimes.

No environment ever overlays two distributions that export the onnxruntime
package. The installer performs CPU VAD and full CUDA ASR inference tests before
atomically replacing the deployed application.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
from typing import Any, Sequence
import urllib.request
import uuid


MIN_PYTHON = (3, 14, 6)
MIN_KERNEL = (7, 1)
MIN_NVIDIA_DRIVER = (580, 0)
PYTHON_LABEL = "3.14.6"

APP_DIR = Path.home() / ".local" / "lib" / "dusky-stt"
BIN_DIR = Path.home() / ".local" / "bin"
SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"
STATE_DIR = Path.home() / ".local" / "state" / "dusky-stt"
HF_HOME = Path.home() / ".cache" / "huggingface"
SERVICE_NAME = "dusky_stt.service"

ONNX_ASR_VERSION = "0.12.0"
ORT_VERSION = "1.27.0"
NUMPY_VERSION = "2.5.1"
SOUNDDEVICE_VERSION = "0.5.5"
HUGGINGFACE_HUB_VERSION = "0.36.0"
HF_XET_VERSION = "1.1.9"

NVIDIA_PACKAGES = (
    "nvidia-cuda-runtime==13.0.88",
    "nvidia-cublas==13.0.2.14",
    "nvidia-cudnn-cu13==9.13.1.26",
    "nvidia-cuda-nvrtc==13.0.88",
    "nvidia-cufft==12.0.0.15",
    "nvidia-curand==10.4.0.35",
    "nvidia-nvjitlink==13.0.88",
)

SILERO_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    "v6.2.1/src/silero_vad/data/silero_vad.onnx"
)
SILERO_SIZE = 2_327_524
SILERO_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"

REQUIRED_SOURCES = (
    "dusky_main.py",
    "dusky_worker.py",
    "dusky_trigger.py",
    "dusky_verify.sh",
    "dusky_stt.service",
)

SYSTEM_PACKAGES = (
    "pipewire",
    "pipewire-audio",
    "pipewire-alsa",
    "wireplumber",
    "portaudio",
    "ffmpeg",
    "wtype",
    "wl-clipboard",
    "libnotify",
    "nvidia-utils",
    "nvtop",
    "uv",
)

type JsonObject = dict[str, Any]


class InstallError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"[dusky-installer] {message}", flush=True)


def run(
    command: Sequence[str | os.PathLike[str]],
    *,
    check: bool = True,
    capture: bool = False,
    environment: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    rendered = [str(part) for part in command]
    env = os.environ.copy() if environment is None else environment.copy()
    env.setdefault("UV_HTTP_TIMEOUT", "300")
    completed = subprocess.run(
        rendered,
        text=True,
        capture_output=capture,
        check=False,
        env=env,
        cwd=cwd,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise InstallError(
            f"command failed with status {completed.returncode}: {' '.join(rendered)}\n{detail[-5000:]}"
        )
    return completed


def numeric_version(value: str, fields: int = 2) -> tuple[int, ...]:
    numbers: list[int] = []
    for component in value.split("."):
        digits = "".join(character for character in component if character.isdigit())
        if not digits:
            break
        numbers.append(int(digits))
        if len(numbers) == fields:
            break
    if len(numbers) != fields:
        raise InstallError(f"could not parse version: {value}")
    return tuple(numbers)


def assert_runtime() -> None:
    if sys.version_info < MIN_PYTHON:
        raise InstallError(
            f"CPython {PYTHON_LABEL}+ is required; running {sys.version.split()[0]}. "
            f"Use: uv run --python {PYTHON_LABEL} dusky_installer.py"
        )
    if sys.implementation.name != "cpython":
        raise InstallError("the installer requires CPython")
    if sysconfig.get_config_var("Py_GIL_DISABLED") == 1 or not sys._is_gil_enabled():
        raise InstallError("the GIL-enabled CPython ABI is required")
    if os.geteuid() == 0:
        raise InstallError("run the installer as the desktop user, not root")
    if not Path("/etc/arch-release").is_file():
        raise InstallError("this build targets Arch Linux only")
    if numeric_version(platform.release(), 2) < MIN_KERNEL:
        raise InstallError(f"Linux {MIN_KERNEL[0]}.{MIN_KERNEL[1]}+ is required")
    if not os.environ.get("XDG_RUNTIME_DIR"):
        raise InstallError("install from an active systemd desktop user session")


def assert_sources(source_dir: Path) -> None:
    missing: list[str] = []
    for name in REQUIRED_SOURCES:
        path = source_dir / name
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            missing.append(name)
            continue
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise InstallError(f"source must be a regular non-symlink file: {path}")
    if missing:
        raise InstallError("missing extracted source files: " + ", ".join(missing))


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise InstallError(f"refusing non-directory path: {path}")
    if metadata.st_uid != os.getuid():
        raise InstallError(f"path is not owned by the installing user: {path}")
    os.chmod(path, 0o700)


def install_system_packages(skip: bool) -> None:
    if skip:
        log("skipping pacman package installation by explicit request")
        return
    missing: list[str] = []
    for package in SYSTEM_PACKAGES:
        result = run(["pacman", "-Q", package], check=False, capture=True)
        if result.returncode != 0:
            missing.append(package)
    if missing:
        run(["sudo", "pacman", "-S", "--needed", "--noconfirm", *missing])


def query_nvidia(gpu_device: int) -> tuple[int, str]:
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        raise InstallError("nvidia-smi is unavailable after installing nvidia-utils")
    result = run(
        [
            nvidia_smi,
            "--query-gpu=index,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        capture=True,
        timeout=10,
    )
    selected: tuple[int, str] | None = None
    for line in result.stdout.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 3:
            continue
        try:
            index = int(fields[0])
            memory_mb = int(fields[1])
        except ValueError:
            continue
        if index == gpu_device:
            selected = (memory_mb, fields[2])
            break
    if selected is None:
        raise InstallError(f"nvidia-smi did not report GPU index {gpu_device}")
    if numeric_version(selected[1], 2) < MIN_NVIDIA_DRIVER:
        raise InstallError(
            f"NVIDIA driver {MIN_NVIDIA_DRIVER[0]}.{MIN_NVIDIA_DRIVER[1]}+ is required; "
            f"found {selected[1]}"
        )
    return selected


def choose_vram_limit(total_mb: int, requested_mb: int | None) -> int:
    safe_maximum = total_mb - 768
    if safe_maximum < 1024:
        raise InstallError(f"GPU has insufficient VRAM: {total_mb} MiB")
    if requested_mb is not None:
        if requested_mb < 1024 or requested_mb > safe_maximum:
            raise InstallError(f"GPU memory limit must be in [1024, {safe_maximum}] MiB")
        return requested_mb
    return min(round(total_mb * 0.70), safe_maximum)


def download_silero(destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(destination.name + ".download")
    request = urllib.request.Request(SILERO_URL, headers={"User-Agent": "dusky-stt-installer/10"})
    digest = hashlib.sha256()
    total = 0
    log(f"downloading SHA-pinned Silero VAD v6.2.1 from {SILERO_URL}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 16 * 1024 * 1024:
                    raise InstallError("Silero download exceeded the hard size limit")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        actual_digest = digest.hexdigest()
        if total != SILERO_SIZE:
            raise InstallError(
                f"Silero size mismatch: expected {SILERO_SIZE}, downloaded {total} bytes"
            )
        if actual_digest != SILERO_SHA256:
            raise InstallError(
                f"Silero SHA-256 mismatch: expected {SILERO_SHA256}, got {actual_digest}"
            )
        os.replace(temporary, destination)
        os.chmod(destination, 0o644)
        return actual_digest
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, payload: JsonObject, mode: int = 0o600) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install_python_environments(stage: Path) -> tuple[Path, Path]:
    uv = shutil.which("uv")
    if uv is None:
        raise InstallError("uv is required after the system package phase")
    main_venv = stage / ".venv-main"
    worker_venv = stage / ".venv-worker"
    run([uv, "venv", "--python", sys.executable, "--clear", main_venv])
    run([uv, "venv", "--python", sys.executable, "--clear", worker_venv])
    main_python = main_venv / "bin" / "python"
    worker_python = worker_venv / "bin" / "python"

    run(
        [
            uv,
            "pip",
            "install",
            "--python",
            main_python,
            f"onnxruntime=={ORT_VERSION}",
            f"numpy=={NUMPY_VERSION}",
            f"sounddevice=={SOUNDDEVICE_VERSION}",
        ]
    )
    run(
        [
            uv,
            "pip",
            "install",
            "--python",
            worker_python,
            f"onnxruntime-gpu=={ORT_VERSION}",
            f"numpy=={NUMPY_VERSION}",
            f"huggingface-hub=={HUGGINGFACE_HUB_VERSION}",
            f"hf-xet=={HF_XET_VERSION}",
            *NVIDIA_PACKAGES,
        ]
    )
    # onnx-asr's extras are intentionally not used: they are allowed to resolve
    # an ORT wheel and would weaken exclusive namespace ownership.
    run(
        [
            uv,
            "pip",
            "install",
            "--python",
            worker_python,
            "--no-deps",
            f"onnx-asr=={ONNX_ASR_VERSION}",
        ]
    )
    run([uv, "pip", "check", "--python", main_python])
    run([uv, "pip", "check", "--python", worker_python])
    return main_python, worker_python


def verify_ort_namespaces(main_python: Path, worker_python: Path) -> None:
    verification = """
import ctypes, glob, importlib.metadata, os, site, sys
expected = sys.argv[1]
if expected == 'onnxruntime-gpu':
    for p in glob.glob(os.path.join(site.getsitepackages()[0], 'nvidia', '*', 'lib')):
        for so in sorted(glob.glob(os.path.join(p, '*.so*'))):
            try:
                ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
            except Exception:
                pass
owners = {name.lower().replace('_', '-') for name in importlib.metadata.packages_distributions().get('onnxruntime', [])}
installed = {
    dist.metadata['Name'].lower().replace('_', '-')
    for dist in importlib.metadata.distributions()
    if dist.metadata.get('Name', '').lower().startswith('onnxruntime')
}
if owners != {expected} or installed != {expected}:
    raise SystemExit(f'ORT namespace collision: owners={owners}, installed={installed}, expected={expected}')
import onnxruntime as ort
if ort.__version__ != '1.27.0':
    raise SystemExit(f'unexpected ORT version: {ort.__version__}')
print(expected, ort.__version__, ort.get_available_providers())
"""
    cpu_environment = os.environ.copy()
    cpu_environment["CUDA_VISIBLE_DEVICES"] = "-1"
    run([main_python, "-c", verification, "onnxruntime"], environment=cpu_environment)
    gpu_environment = os.environ.copy()
    gpu_environment["CUDA_VISIBLE_DEVICES"] = "0"
    run([worker_python, "-c", verification, "onnxruntime-gpu"], environment=gpu_environment)


def verify_cpu_vad(main_python: Path, model_path: Path) -> None:
    verification = """
import pathlib, sys
import numpy as np
import onnxruntime as ort
options = ort.SessionOptions()
options.intra_op_num_threads = 1
options.inter_op_num_threads = 1
session = ort.InferenceSession(sys.argv[1], sess_options=options, providers=['CPUExecutionProvider'])
if session.get_providers() != ['CPUExecutionProvider']:
    raise SystemExit(f'VAD provider mismatch: {session.get_providers()}')
inputs = {item.name for item in session.get_inputs()}
if inputs != {'input', 'state', 'sr'}:
    raise SystemExit(f'VAD input mismatch: {inputs}')
output = session.run(None, {
    'input': np.zeros((1, 576), dtype=np.float32),
    'state': np.zeros((2, 1, 128), dtype=np.float32),
    'sr': np.array(16000, dtype=np.int64),
})
if len(output) != 2:
    raise SystemExit('VAD output count mismatch')
maps = pathlib.Path('/proc/self/maps').read_text().lower()
for name in ('libcuda.so', 'libcudart.so', 'libcublas.so', 'libcudnn.so', 'onnxruntime_providers_cuda'):
    if name in maps:
        raise SystemExit(f'CPU VAD process mapped forbidden CUDA object: {name}')
print('CPU-only Silero VAD execution passed')
"""
    environment = os.environ.copy()
    environment["CUDA_VISIBLE_DEVICES"] = "-1"
    run([main_python, "-c", verification, model_path], environment=environment)


def prefetch_model(
    worker_python: Path,
    stage: Path,
    model: str,
    quantization: str,
) -> None:
    model_dir = stage / "models" / "asr"
    code = """
import ctypes, glob, os, pathlib, site, sys
for p in glob.glob(os.path.join(site.getsitepackages()[0], 'nvidia', '*', 'lib')):
    for so in sorted(glob.glob(os.path.join(p, '*.so*'))):
        try:
            ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
        except Exception:
            pass
import onnx_asr
quantization = None if sys.argv[3] == 'fp32' else sys.argv[3]
model = onnx_asr.load_model(
    sys.argv[1],
    pathlib.Path(sys.argv[2]),
    quantization=quantization,
    providers=['CPUExecutionProvider'],
    preprocessor_config={'max_concurrent_workers': 1, 'use_numpy_preprocessors': True},
)
result = model.recognize(__import__('numpy').zeros(16000, dtype='float32'), sample_rate=16000)
if not isinstance(result, str):
    raise SystemExit(f'unexpected ASR result type: {type(result).__name__}')
print('ASR model downloaded and CPU-load validated')
"""
    environment = os.environ.copy()
    environment["CUDA_VISIBLE_DEVICES"] = "-1"
    environment["HF_HOME"] = str(HF_HOME)
    environment["HF_HUB_DISABLE_TELEMETRY"] = "1"
    run(
        [worker_python, "-c", code, model, model_dir, quantization],
        environment=environment,
        timeout=1800,
    )


def verify_cuda_asr(worker_python: Path, stage: Path, gpu_device: int) -> None:
    environment = os.environ.copy()
    environment["CUDA_VISIBLE_DEVICES"] = str(gpu_device)
    environment["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    environment["CUDA_MODULE_LOADING"] = "LAZY"
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    run(
        [
            worker_python,
            stage / "dusky_worker.py",
            "--self-test",
            "--config",
            stage / "config.json",
        ],
        environment=environment,
        cwd=stage,
        timeout=600,
    )


def verify_wayland_typing(skip: bool) -> None:
    if skip:
        log("skipping Wayland protocol smoke test by explicit request")
        return
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise InstallError("WAYLAND_DISPLAY is required for the wtype smoke test")
    wtype = shutil.which("wtype")
    if wtype is None:
        raise InstallError("wtype is unavailable")
    result = run([wtype, ""], check=False, capture=True, timeout=10)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise InstallError(
            "wtype cannot bind the compositor's virtual-keyboard protocol: " + detail
        )


def freeze_environment(python: Path, output: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise InstallError("uv disappeared during installation")
    result = run([uv, "pip", "freeze", "--python", python], capture=True)
    output.write_text(result.stdout, encoding="utf-8")
    os.chmod(output, 0o644)


def deploy_stage(stage: Path) -> Path | None:
    backup: Path | None = None
    if APP_DIR.exists():
        backup = APP_DIR.with_name(f"dusky-stt.backup-{int(time.time())}-{uuid.uuid4().hex[:8]}")
        APP_DIR.rename(backup)
    try:
        stage.rename(APP_DIR)
    except Exception:
        if backup is not None and not APP_DIR.exists():
            backup.rename(APP_DIR)
        raise
    return backup


def install_entrypoints(source_dir: Path) -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
    final_python = APP_DIR / ".venv-main" / "bin" / "python"

    trigger_source = (source_dir / "dusky_trigger.py").read_text(encoding="utf-8")
    trigger_lines = trigger_source.splitlines()
    trigger_lines[0] = f"#!{final_python}"
    trigger_temporary = BIN_DIR / f".dusky_trigger.{uuid.uuid4().hex}.tmp"
    trigger_temporary.write_text("\n".join(trigger_lines) + "\n", encoding="utf-8")
    os.chmod(trigger_temporary, 0o755)
    os.replace(trigger_temporary, BIN_DIR / "dusky_trigger")

    verify_temporary = BIN_DIR / f".dusky_verify.{uuid.uuid4().hex}.tmp"
    shutil.copyfile(source_dir / "dusky_verify.sh", verify_temporary)
    os.chmod(verify_temporary, 0o755)
    os.replace(verify_temporary, BIN_DIR / "dusky_verify")

    service_destination = SYSTEMD_DIR / SERVICE_NAME
    service_temporary = SYSTEMD_DIR / f".{SERVICE_NAME}.{uuid.uuid4().hex}.tmp"
    shutil.copyfile(source_dir / SERVICE_NAME, service_temporary)
    os.chmod(service_temporary, 0o644)
    os.replace(service_temporary, service_destination)


def import_wayland_environment() -> None:
    names = [name for name in ("WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP") if os.environ.get(name)]
    if names:
        run(["systemctl", "--user", "import-environment", *names])


def configure_systemd(no_start: bool) -> None:
    import_wayland_environment()
    run(["systemd-analyze", "--user", "verify", SYSTEMD_DIR / SERVICE_NAME])
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", SERVICE_NAME])
    if no_start:
        return
    run(["systemctl", "--user", "start", SERVICE_NAME])
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        active = run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            check=False,
            capture=True,
        )
        socket_path = Path(os.environ["XDG_RUNTIME_DIR"]) / "dusky-stt" / "control.sock"
        if active.returncode == 0 and socket_path.exists():
            return
        failed = run(
            ["systemctl", "--user", "is-failed", "--quiet", SERVICE_NAME],
            check=False,
        )
        if failed.returncode == 0:
            break
        time.sleep(0.2)
    status = run(
        ["systemctl", "--user", "status", SERVICE_NAME, "--no-pager", "--full"],
        check=False,
        capture=True,
    )
    raise InstallError("service did not become ready:\n" + status.stdout[-5000:])


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install CUDA 13 Dusky STT on Arch Linux")
    parser.add_argument(
        "--model",
        choices=("nemo-parakeet-tdt-0.6b-v2", "nemo-parakeet-tdt-0.6b-v3"),
        default="nemo-parakeet-tdt-0.6b-v2",
    )
    parser.add_argument("--quantization", choices=("int8", "fp16", "fp32"), default="int8")
    parser.add_argument("--gpu-device", type=int, default=0)
    parser.add_argument("--gpu-mem-limit-mb", type=int)
    parser.add_argument("--input-device", help="sounddevice input device name or index")
    parser.add_argument("--idle-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--keep-audio", action="store_true")
    parser.add_argument("--skip-system-packages", action="store_true")
    parser.add_argument("--skip-wayland-smoke", action="store_true")
    parser.add_argument("--no-start", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    assert_runtime()
    if args.gpu_device < 0:
        raise InstallError("GPU device index cannot be negative")
    if args.idle_timeout_seconds < 5:
        raise InstallError("idle timeout must be at least five seconds")
    source_dir = Path(__file__).resolve().parent
    assert_sources(source_dir)
    install_system_packages(args.skip_system_packages)
    total_vram_mb, driver_version = query_nvidia(args.gpu_device)
    gpu_limit = choose_vram_limit(total_vram_mb, args.gpu_mem_limit_mb)
    log(
        f"GPU {args.gpu_device}: driver={driver_version} vram={total_vram_mb} MiB "
        f"arena_limit={gpu_limit} MiB"
    )
    verify_wayland_typing(args.skip_wayland_smoke)

    APP_DIR.parent.mkdir(parents=True, exist_ok=True)
    app_parent_metadata = os.lstat(APP_DIR.parent)
    if (
        not stat.S_ISDIR(app_parent_metadata.st_mode)
        or stat.S_ISLNK(app_parent_metadata.st_mode)
        or app_parent_metadata.st_uid != os.getuid()
    ):
        raise InstallError(f"invalid application parent directory: {APP_DIR.parent}")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
    private_directory(STATE_DIR)
    HF_HOME.mkdir(parents=True, exist_ok=True)

    was_active = run(
        ["systemctl", "--user", "is-active", "--quiet", SERVICE_NAME],
        check=False,
    ).returncode == 0
    run(["systemctl", "--user", "stop", SERVICE_NAME], check=False)
    stage = Path(tempfile.mkdtemp(prefix=".dusky-stt-stage-", dir=APP_DIR.parent))
    os.chmod(stage, 0o700)
    backup: Path | None = None
    deployed = False
    try:
        for name in REQUIRED_SOURCES:
            shutil.copy2(source_dir / name, stage / name)
        for name in ("dusky_main.py", "dusky_worker.py", "dusky_trigger.py", "dusky_verify.sh"):
            os.chmod(stage / name, 0o755)

        main_python, worker_python = install_python_environments(stage)
        vad_hash = download_silero(stage / "models" / "silero_vad.onnx")
        prefetch_model(
            worker_python,
            stage,
            args.model,
            args.quantization,
        )
        config: JsonObject = {
            "schema_version": 2,
            "python_baseline": PYTHON_LABEL,
            "backend": "cuda13",
            "model": args.model,
            "model_dir": "models/asr",
            "quantization": args.quantization,
            "gpu_device": args.gpu_device,
            "gpu_mem_limit_mb": gpu_limit,
            "input_device": args.input_device,
            "state_dir": str(STATE_DIR),
            "output_mode": "realtime-both",
            "push_type_at_end": True,
            "keep_audio": args.keep_audio,
            "llm_enabled": True,
            "llm_endpoint": "http://localhost:11434",
            "llm_model": "s1-mini",
            "llm_cleanup_style": "semi-formal",
            "llm_cleanup_structure": "prose",
            "llm_cleanup_context": "general",
            "llm_timeout_seconds": 60,
            "llm_max_tokens": 2048,
            "idle_timeout_seconds": args.idle_timeout_seconds,
            "max_inflight_requests": 2,
            "worker_queue_timeout_seconds": 30,
            "realtime_interval_seconds": 1.2,
            "finalize_timeout_seconds": 120,
            "max_request_seconds": 30,
            "max_phrase_seconds": 15,
            "file_chunk_seconds": 25,
            "pre_roll_seconds": 0.32,
            "phrase_silence_seconds": 0.80,
            "vad_onset_seconds": 0.096,
            "vad_min_speech_seconds": 0.25,
            "vad_start_threshold": 0.50,
            "vad_end_threshold": 0.35,
            "stable_holdback_words": 2,
            "silero_source": SILERO_URL,
            "silero_sha256": vad_hash,
            "onnx_asr_version": ONNX_ASR_VERSION,
            "onnxruntime_version": ORT_VERSION,
            "cuda_runtime": "13.0",
        }
        write_json(stage / "config.json", config)
        verify_ort_namespaces(main_python, worker_python)
        verify_cpu_vad(main_python, stage / "models" / "silero_vad.onnx")
        verify_cuda_asr(worker_python, stage, args.gpu_device)
        freeze_environment(main_python, stage / "environment-main.lock")
        freeze_environment(worker_python, stage / "environment-worker.lock")

        backup = deploy_stage(stage)
        deployed = True
        install_entrypoints(APP_DIR)
        configure_systemd(args.no_start)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if deployed:
            run(["systemctl", "--user", "stop", SERVICE_NAME], check=False)
            shutil.rmtree(APP_DIR, ignore_errors=True)
            if backup is not None and backup.exists():
                backup.rename(APP_DIR)
                if all((APP_DIR / name).is_file() for name in REQUIRED_SOURCES):
                    install_entrypoints(APP_DIR)
                    run(["systemctl", "--user", "daemon-reload"], check=False)
                    if was_active:
                        run(["systemctl", "--user", "start", SERVICE_NAME], check=False)
        raise
    else:
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)

    log("installation complete")
    log(f"trigger: {BIN_DIR / 'dusky_trigger'}")
    log(f"verification: {BIN_DIR / 'dusky_verify'}")
    log(f"config: {APP_DIR / 'config.json'}")
    log(f"service: {SYSTEMD_DIR / SERVICE_NAME}")
    if args.no_start:
        log(f"start with: systemctl --user start {SERVICE_NAME}")
    else:
        log("service is ready; run dusky_trigger to toggle realtime capture")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InstallError, OSError, subprocess.SubprocessError) as exc:
        print(f"[dusky-installer] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
