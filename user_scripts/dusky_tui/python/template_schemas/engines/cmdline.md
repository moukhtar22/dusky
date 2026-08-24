# Engine: `cmdline`

- **Class:** `CmdlineEngine` — `engines/cmdline.py`
- **Engine types:** `cmdline`
- **Default target:** `/etc/kernel/cmdline`

## Target format

A single line of space-separated kernel arguments — boolean flags and `key=value` pairs. On write the file is read and any newlines are flattened to spaces (one token line):

```
rw root=UUID=1234-abcd quiet splash nvidia-drm.modeset=1
```

## Scope / key mapping

- `scope` must be `"DEFAULT"` — the engine ignores scope and reads/writes only `DEFAULT/…` keys.
- `key` = argument name. Duplicate arguments are addressed as `key:N`; the plain base key always targets the **last** occurrence (kernel precedence).
- State keys: `DEFAULT/<key>` and `DEFAULT/<key>:<N>`; the base key holds the last occurrence's value.

## Types & value handling

- `bool` `true` → bare flag (`quiet`); `bool` `false` → token removed.
- `"__DELETE__"`, `"unset"`, `""` (any type) → token removed (surrounding whitespace collapsed).
- Any other type (`string`, `int`, `float`, `cycle`, `picker`, `color`) → `key=value` with the serialized value verbatim.
- Args missing from the file are appended to the end of the line (`key:N` stripped to `key` when appending).
- Missing state keys read back as `"unset"` (bridged state — see Quirks).

## Quirks

- **Bridged state:** `load_state` returns a `BridgedStateDict` (`__contains__` → True, absent keys → `"unset"`), so optional kernel flags never render as `[Missing]`.
- Deletion/false checks are case-insensitive; kept values are written exactly as serialized.
- TOCTOU guard: file mtime newer than the load snapshot aborts the write ("modified externally. Reload required.").
- Atomic commit: temp file + fsync + `os.replace`, preserving mode/owner. On `PermissionError` it falls back to `sudo -n tee <file>`; failure returns `(False, "AUTH_REQUIRED", …)` — so the TUI can prompt for the password.
- Tokenization respects single/double-quoted arguments.

## Example items

```python
ConfigItem(label="Quiet", key="quiet", scope="DEFAULT", type_="bool",
           default=True, group="Boot"),
ConfigItem(label="Root Device", key="root", scope="DEFAULT", type_="string",
           default="UUID=1234-abcd", group="Boot"),
ConfigItem(label="NVIDIA Modeset", key="nvidia-drm.modeset", scope="DEFAULT",
           type_="int", default=1, options=[0, 1], group="GPU"),
```