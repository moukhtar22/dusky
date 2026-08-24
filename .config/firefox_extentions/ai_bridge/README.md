# Local AI Web Bridge

A lightweight, zero-dependency, event-driven bridge for Firefox MV3 that allows any local AI agent (Antigravity, Ollama, Llama.cpp, LangChain, or custom CLI scripts) to communicate seamlessly with web-based AI platforms (**Google Gemini**, **ChatGPT**, **Claude**, **Meta AI**).

---

## 🤖 Instructions for AI Agents & Assistant Context

If you are an AI assistant or automated coding agent reading this file in a fresh conversation, **you do not need to scan the entire codebase**. Everything you need to understand the system, query web AI models, and follow the required verification workflows is documented below.

### 🔑 Local vs. Online AI Token Economy Mandate

- **Local AI Token Constraints**: Local AI agents (you) operate under tight token limits.
- **Online AI Subscription & Capacity**: The USER has a **high-capacity paid subscription** for web AI models (ChatGPT, Gemini, Claude, Meta AI) with substantially higher token limits than the local AI.
- **Mandated Workflow**: You **MUST leverage the online web AI as much as possible** for all heavy lifting, deep reasoning, code generation, architectural design, and extensive verification to conserve local tokens.

---

## 💻 Bleeding-Edge System Environment & Target Specifications

When generating scripts, configuration files, system commands, or code edits for this host, **ALWAYS target the absolute bleeding edge**. 

```bash
# Check current rolling-release versions live on system:
uname -r          # Linux Kernel version
python3 --version  # Python interpreter version
bash --version     # Bash shell version
systemctl --version # systemd version
```

### 🚫 Strict Modernity Directives:
1. **Dynamic Version Inspection**: Do NOT assume hardcoded static version numbers. Arch Linux is a rolling-release distribution — versions update continuously. Always inspect runtime versions or target the absolute latest bleeding-edge release available for Arch Linux.
2. **NO Legacy / Deprecated Code**: NEVER use outdated Python syntax, deprecated shell constructs, or legacy Web APIs.
3. **NO Backward Compatibility**: Do NOT add fallbacks or polyfills for older Linux kernels, obsolete Python versions, legacy Bash, or obsolete systemd. We are not trying to look back; we are moving forward.
4. **Future-Proof Standards**: Always write cutting-edge, idiomatically modern code targeting the absolute newest standards.

---

## 🤝 Hand-in-Glove Collaboration & Verification Protocol

The Local AI agent and Online Web AI model work as a **hand-in-glove pair**:

```
┌───────────────────────────────────────────────────────────────────────────┐
│                           LOCAL AI AGENT                                  │
│  - Has DIRECT ACCESS to local filesystem, terminal, and system commands   │
│  - MUST act strictly as the EMPIRICAL DATA COLLECTOR                      │
│  - NO guesswork or unverified assumptions in prompts                      │
│  - MUST NEVER interrupt or re-prompt while web AI is generating            │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │ Complete Empirical Data Payload
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                       ONLINE WEB AI MODEL                                 │
│  - Has HIGH-CAPACITY TOKEN ALLOCATION and web search capabilities         │
│  - Does NOT have direct access to local filesystem                        │
│  - Allowed to hypothesize, analyze from first principles, & synthesize     │
└───────────────────────────────────────────────────────────────────────────┘
```

### 📊 1. Empirical Data Collection Mandate (No Guesswork)
- **Local AI Role**: The Local AI must NOT construct prompts based on guesswork or unverified assumptions. 
- **Gather Context First**: Before prompting the Online AI, the Local AI MUST inspect the environment and collect **all verifiable empirical data**: exact error stack traces, un-truncated log outputs, file contents, kernel/runtime versions (`uname -r`, `python3 --version`), and configuration parameters.
- **Provide Complete Payload**: Pass this full empirical dataset to the Online AI in Pass 1 so it has complete, un-truncated context to perform deep reasoning.

### ⏳ 2. Non-Interruption & Generation Patience Directive

> [!CAUTION]
> **STRICT MANDATE: NEVER INTERRUPT AN IN-FLIGHT GENERATION OR SEND STATUS PINGS**
> 
> 1. **NO STATUS CHECK PROMPTS**: NEVER send queries like `"Is Gemini ready?"`, `"Are you active?"`, or `"Which platform is this?"` into the web AI prompt box. The bridge automatically handles routing to the active Firefox tab. Sending meta-status questions into the chat prompt box clutters the conversation and cancels active work.
> 2. **NO INTERRUPTIONS MID-GENERATION**: NEVER send a follow-up prompt, re-query, or new command while the online AI is actively generating or thinking. On all web AI platforms, **submitting a new prompt while the AI is generating IMMEDIATELY CANCELS AND DESTROYS THE IN-FLIGHT RESPONSE**, wasting all previous reasoning work.
> 3. **WAIT SILENTLY FOR FULL COMPLETION**: Deep reasoning models (e.g. Gemini Thinking, ChatGPT Reasoning) can take anywhere from **2 to 20+ minutes** to complete a response. The Local AI MUST wait silently until `bridge.py` finishes completely (`FINAL` status received, exit code 0) before sending any follow-up query.

---

## 🔄 Mandatory 2-Pass Verification Protocol

Whenever a complex coding, refactoring, or architectural task is requested, the Local AI agent MUST execute the following **2-pass protocol**:

### Pass 1 Prompt Template (Empirical Data + Research Proposal)

The Local AI collects empirical data locally, then sends:

> "I am providing you with all empirical data collected from the local system (file contents, error logs, and runtime diagnostics below). Analyze this problem from first principles using the empirical evidence. Research online for the absolute latest documentation and bleeding-edge standards for Arch Linux (inspecting runtime versions of Kernel, Python 3.14+, Bash 5.3+, systemd). Do NOT hallucinate or guess. Think long, hard, and critically. Do NOT use old, deprecated, or legacy methodologies, and do NOT provide backward compatibility for older versions. Provide ONLY the precise, minimal surgical code blocks that need to be replaced, added, or edited."

### Pass 2 Prompt Template (Rigorous Double-Verification Press)

After the Online AI completes Pass 1, send a **follow-up query** in the same conversation turn:

> "Re-verify everything you just proposed from scratch from first principles using the empirical data. Think through the logic entirely again. Perform a second pass over your previous response to ensure there are zero logical holes, false positives, or hallucinated suggestions. Are all proposed improvements 100% necessary, optimal, and compatible with the absolute latest bleeding-edge specs? Only output the final, verified, minimal code diff blocks if they pass this second verification."

Only after the Online AI completes Pass 2 and confirms the recommendations should the Local AI apply the changes.

---

## 🚀 How to Execute Queries from Terminal & Scripts

### 1. Primary Command Line Interface (`ask` Alias & `bridge.py`)

To query the active web AI tab (e.g. Gemini, ChatGPT, Claude, Meta AI) synchronously:

```bash
ask "YOUR PROMPT HERE"
```

Or directly via Python script:

```bash
python3 /home/dusk/.config/firefox_extentions/ai_bridge/bridge.py "YOUR PROMPT HERE"
```

- **Interactive Mode**: Run `ask` with no arguments to enter interactive prompt mode (prevents all shell escaping/quoting issues for complex strings containing `?`, `'`, `"`, `$`, `*`).
- **Noglob Protection**: The `ask` alias uses `zsh` `noglob` so unquoted question marks (`ask what ?`) will never fail with glob errors.
- **Output**: Streams text token-by-token directly to `stdout`. Shows `[Thinking...]` during model reasoning.
- **Exit Code**: Returns `0` on completion.

### 2. Python Programmatic Integration

#### Full Response (Synchronous):
```python
import subprocess

def ask_web_ai(prompt: str) -> str:
    """Send a prompt to the Web AI bridge and return the complete answer."""
    result = subprocess.run(
        ["python3", "/home/dusk/.config/firefox_extentions/ai_bridge/bridge.py", prompt],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        return result.stdout.strip()
    else:
        raise RuntimeError(f"Web AI Bridge Error: {result.stderr}")

answer = ask_web_ai("Explain quantum computing in 2 sentences.")
print(answer)
```

#### Real-Time Token Streaming (Generator):
```python
import subprocess

def stream_web_ai(prompt: str):
    """Stream response tokens live in real-time as they are generated by the web AI."""
    cmd = ["python3", "/home/dusk/.config/firefox_extentions/ai_bridge/bridge.py", prompt]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    for chunk in proc.stdout:
        yield chunk

for token in stream_web_ai("Write a 1-paragraph explanation of AI."):
    print(token, end="", flush=True)
```

---

## 🏗️ Complete System Architecture & Codebase Map

```
┌──────────────────────────────────────────────────────────┐
│                   bridge.py (Daemon & CLI)              │
│  - WebSocket Server (127.0.0.1:8765)                     │
│  - Hyprland Window/Workspace Focus (hyprctl)            │
│  - Hardware Keyboard Input Engine (wtype)               │
│  - Diff-based Stream Output & THINKING State Handler     │
└────────────────────────────▲─────────────────────────────┘
                             │ WebSocket
┌────────────────────────────▼─────────────────────────────┐
│                background.js (MV3 Service Worker)        │
│  - Tab Locks & Router                                    │
└────────────────────────────▲─────────────────────────────┘
                             │ postMessage
┌────────────────────────────▼─────────────────────────────┐
│             content_isolated.js (Isolated World)         │
└────────────────────────────▲─────────────────────────────┘
                             │ postMessage
┌────────────────────────────▼─────────────────────────────┐
│               content_main.js (Page World)               │
│  - Modular Site Adapter Architecture (defineSite)        │
│  - Adapters: Gemini, ChatGPT, Claude, Meta AI, _default  │
│  - DOM MutationObserver & Site-Specific Input Encoders   │
└──────────────────────────────────────────────────────────┘
```

### Detailed File Responsibilities

1. **`bridge.py`**:
   - Runs as background daemon (`localai_bridge.service`) and CLI client.
   - Listens on `127.0.0.1:8765` via raw WebSocket implementation (zero external pip dependencies).
   - Focuses Firefox window on Hyprland workspace prior to query submission.
   - For Gemini, dispatches hardware Return keystroke (`wtype -k Return`) to bypass `isTrusted:false` restriction.
   - Handles `THINKING` state events from reasoning models and formats live streamed stdout without tautological duplication.

2. **`background.js`**:
   - MV3 extension background page. Manages active AI tab locks and relays messages between WebSocket daemon and content scripts.

3. **`content_isolated.js`**:
   - Content script in isolated extension realm. Relays `postMessage` calls between background script and page script.

4. **`content_main.js`**:
   - Content script running directly in the webpage main DOM realm (`MAIN` world).
   - Built on a **Modular Site Adapter Architecture** (`defineSite()`):
     - **Gemini**: Shadow DOM (`rich-textarea`) traversal, Angular `InputEvent` chain, hardware wtype focus lock.
     - **ChatGPT**: Lexical editor binding, single `btn.click()` (prevents duplicate submissions).
     - **Claude**: ProseMirror editor binding, streaming observer.
     - **Meta AI**: React contenteditable input encoder (prevents text tripling), role="article" container observer.
     - **`_default`**: Fallback cascade for unrecognized AI interfaces.

5. **`~/.zshrc` (`ask` shortcut)**:
   - Configured with `alias ask='noglob _ask_func'`. Prevents Zsh globbing errors on question marks (`?`) and supports interactive mode when called with no arguments.

---

## 🛠️ Diagnostics & Troubleshooting Commands

```bash
# 1. Check Daemon Service Status
systemctl --user status localai_bridge.service

# 2. Restart Daemon Service
systemctl --user restart localai_bridge.service

# 3. View Daemon Logs Live
journalctl --user -u localai_bridge.service -f

# 4. Test Query Connection
ask "Test connection"
```
