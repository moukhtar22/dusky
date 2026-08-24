#!/usr/bin/env python3
"""
Dusky LLM Side Panel - Backend Module
Handles Ollama API interactions, GGUF auto-import, state persistence,
streaming response workers, and memory management.
"""

import sys
import os
import gc
import json
import time
import shutil
import ctypes
import signal
import tomllib
import logging
import urllib.request
import urllib.error
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Final, Optional

sys.dont_write_bytecode = True

# Logging Configuration
logging.basicConfig(level=logging.INFO, format="[dusky-llm] %(levelname)s: %(message)s")
LOG = logging.getLogger("dusky-llm")

# Application Identifiers & Directories
APP_ID: Final[str] = "org.dusky.llm"
HOME: Final[str] = os.path.expanduser("~")
OLLAMA_URL: Final[str] = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
CONFIG_DIR: Final[Path] = Path(HOME) / ".config" / "dusky" / "llm_side_panal"
CONFIG_FILE: Final[Path] = CONFIG_DIR / "config.toml"
HISTORY_FILE: Final[Path] = CONFIG_DIR / "chat_history.json"
GGUF_SEARCH_DIR: Final[Path] = Path("/mnt/zram1/owao")

DEFAULT_TOML_CONFIG: Final[str] = """[panel]
width = 420
position = "right"
font_size = 13
autofocus = true
click_away_dismiss = true
save_history = true

[llm]
default_model = "nanbeige:3b"
temperature = 0.7
system_prompt = "You are Dusky AI, an intelligent, helpful, and concise desktop AI assistant running locally."
stream = true
"""

# PyCapsule pointer extraction for libwaylandgrab
try:
    _PYTHONAPI = ctypes.pythonapi
    _PYTHONAPI.PyCapsule_GetPointer.restype = ctypes.c_void_p
    _PYTHONAPI.PyCapsule_GetPointer.argtypes = (ctypes.py_object, ctypes.c_char_p)
except Exception:
    _PYTHONAPI = None

def gi_object_c_pointer(gi_obj: object) -> ctypes.c_void_p | None:
    """Extract underlying GObject C pointer from PyGObject wrapper."""
    if gi_obj is None:
        return None
    capsule = getattr(gi_obj, "__gpointer__", None)
    if capsule is None:
        return None
    if isinstance(capsule, int):
        return ctypes.c_void_p(capsule) if capsule else None
    if _PYTHONAPI is None:
        return None
    try:
        raw = _PYTHONAPI.PyCapsule_GetPointer(capsule, None)
    except Exception:
        return None
    return ctypes.c_void_p(raw) if raw else None

def _reclaim_idle_memory() -> None:
    """Trigger Python GC and malloc trim to minimize RAM usage when panel is hidden."""
    gc.collect()
    try:
        libc = ctypes.CDLL("libc.so.6")
        if hasattr(libc, "malloc_trim"):
            libc.malloc_trim(0)
    except Exception:
        pass

def load_or_create_config() -> dict[str, Any]:
    """Load config.toml or create default if missing."""
    if not CONFIG_FILE.exists():
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(DEFAULT_TOML_CONFIG, encoding="utf-8")
        except OSError as e:
            LOG.error(f"Failed to create config file: {e}")
            return tomllib.loads(DEFAULT_TOML_CONFIG)
    try:
        with CONFIG_FILE.open("rb") as f:
            return tomllib.load(f)
    except Exception as e:
        LOG.error(f"Error loading {CONFIG_FILE}: {e}")
        return tomllib.loads(DEFAULT_TOML_CONFIG)

def save_config_value(key_path: list[str], value: Any) -> None:
    """Helper to update config settings on disk."""
    try:
        cfg = load_or_create_config()
        curr = cfg
        for k in key_path[:-1]:
            curr = curr.setdefault(k, {})
        curr[key_path[-1]] = value
        
        # Simple TOML serializer fallback for standard keys
        lines = []
        for sec, content in cfg.items():
            if isinstance(content, dict):
                lines.append(f"[{sec}]")
                for k, v in content.items():
                    if isinstance(v, bool):
                        lines.append(f"{k} = {str(v).lower()}")
                    elif isinstance(v, (int, float)):
                        lines.append(f"{k} = {v}")
                    else:
                        escaped = str(v).replace('\\', '\\\\').replace('"', '\\"')
                        lines.append(f'{k} = "{escaped}"')
                lines.append("")
        CONFIG_FILE.write_text("\n".join(lines), encoding="utf-8")
    except Exception as e:
        LOG.error(f"Failed to save config value: {e}")

def check_ollama_alive() -> bool:
    """Check if local Ollama API is active."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False

def ensure_ollama_service() -> bool:
    """Ensure ollama.service is running via systemctl if not responsive."""
    if check_ollama_alive():
        return True
    LOG.info("Ollama API non-responsive. Attempting to start ollama.service...")
    try:
        subprocess.run(["systemctl", "is-active", "--quiet", "ollama.service"])
    except Exception:
        pass
    
    try:
        subprocess.run(["sudo", "-n", "systemctl", "start", "ollama.service"], check=False, stderr=subprocess.DEVNULL)
    except Exception:
        try:
            subprocess.run(["systemctl", "--user", "start", "ollama.service"], check=False, stderr=subprocess.DEVNULL)
        except Exception:
            pass
            
    # Poll for readiness up to 10s
    for _ in range(20):
        time.sleep(0.5)
        if check_ollama_alive():
            return True
    return False

def get_installed_models() -> list[str]:
    """Retrieve list of locally registered Ollama models."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                return sorted(models)
    except Exception as e:
        LOG.warning(f"Could not fetch Ollama models: {e}")
    return []

def auto_import_local_gguf() -> Optional[str]:
    """
    Scans /mnt/zram1/owao/ for GGUF files (e.g. Nanbeige4.2-3B-Q4_K_M.gguf)
    and registers them into Ollama if not present yet.
    """
    if not GGUF_SEARCH_DIR.exists():
        return None

    gguf_files = list(GGUF_SEARCH_DIR.rglob("*.gguf"))
    if not gguf_files:
        return None

    target_gguf = gguf_files[0]
    model_name = "nanbeige:3b" if "nanbeige" in target_gguf.name.lower() else "local-gguf:latest"
    
    existing = get_installed_models()
    if any(m.startswith(model_name) or model_name in m for m in existing):
        LOG.info(f"Local GGUF model '{model_name}' already registered in Ollama.")
        return model_name

    LOG.info(f"Found unregistered GGUF model: {target_gguf}. Registering as '{model_name}'...")
    
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    modelfile_path = CONFIG_DIR / "Modelfile.tmp"
    try:
        modelfile_content = f"FROM {target_gguf.resolve()}\nPARAMETER temperature 0.7\n"
        modelfile_path.write_text(modelfile_content, encoding="utf-8")
        
        # Execute ollama create
        cmd = ["ollama", "create", model_name, "-f", str(modelfile_path)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode == 0:
            LOG.info(f"Successfully registered '{model_name}' into Ollama!")
            return model_name
        else:
            LOG.error(f"Failed to create Ollama model: {res.stderr}")
    except Exception as e:
        LOG.error(f"Error during GGUF auto-import: {e}")
    finally:
        if modelfile_path.exists():
            try:
                modelfile_path.unlink()
            except Exception:
                pass
    return None

class LLMStreamingWorker:
    """Threaded worker for streaming Ollama API responses using unbuffered curl."""

    def __init__(self, on_chunk: Callable[[str], None], on_finish: Callable[[Optional[str]], None]) -> None:
        self.on_chunk = on_chunk
        self.on_finish = on_finish
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._cancel_flag = False

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self._cancel_flag = True
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def send_chat(self, model: str, messages: list[dict[str, str]], temperature: float = 0.7) -> None:
        if self.is_running():
            self.cancel()
            time.sleep(0.1)

        self._cancel_flag = False
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"num_ctx": 4096, "temperature": temperature}
        }
        
        self._thread = threading.Thread(
            target=self._run_stream,
            args=(payload,),
            daemon=True
        )
        self._thread.start()

    def _run_stream(self, payload: dict[str, Any]) -> None:
        err_msg: Optional[str] = None
        try:
            json_payload = json.dumps(payload)
            cmd = [
                "curl", "-sS", "-N",
                "-X", "POST",
                f"{OLLAMA_URL}/api/chat",
                "-H", "Content-Type: application/json",
                "-d", json_payload
            ]
            
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0
            )
            
            if self._proc.stdout:
                for raw_bytes in iter(self._proc.stdout.readline, b""):
                    if self._cancel_flag:
                        break
                    line = raw_bytes.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        chunk_obj = json.loads(line)
                        if "error" in chunk_obj:
                            err_msg = chunk_obj["error"]
                            break
                        msg_obj = chunk_obj.get("message", {})
                        content = msg_obj.get("content", "")
                        thinking = msg_obj.get("thinking", "")
                        token = content or thinking
                        if token:
                            self.on_chunk(token)
                        if chunk_obj.get("done", False):
                            break
                    except json.JSONDecodeError:
                        continue
            
            if self._proc:
                self._proc.wait()

        except Exception as e:
            if not self._cancel_flag:
                err_msg = f"LLM error: {str(e)}"

        self.on_finish(err_msg)
