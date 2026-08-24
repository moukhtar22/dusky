# Engine: `systemd_boot`

- **Class:** `SystemdBootEngine` — `engines/systemd_boot.py`
- **Engine types:** `systemd_boot`
- **Default target:** `/boot/loader/entries/arch-linux.conf`

## Target format

A systemd-boot loader entry. The engine operates **only** on the `options` line (token list); `title`, `linux`, `initrd`, comments and other lines pass through untouched. If the file has no `options` line, one is appended:

```
title   Arch Linux
linux   /vmlinuz-linux
initrd  /initramfs-linux.img
options rw root=UUID=1234-abcd quiet splash nvidia-drm.modeset=1
```

## Scope / key mapping

- `scope` must be `"DEFAULT"` — the engine ignores scope and reads/writes only `DEFAULT/…` keys.
- `key` = argument name (`quiet`, `root`, `nvidia-drm.modeset`). Duplicate arguments are addressed as `key:N` (`root:2` = 2nd occurrence). The plain base key always targets the **last** occurrence (kernel precedence).
- State keys: `DEFAULT/<key>` and `DEFAULT/<key>:<N>` for each occurrence; the base key holds the last occurrence's value.

## Types & value handling

- `bool` `true` → bare flag (`quiet`); `bool` `false` → token removed.
- `"__DELETE__"`, `"unset"`, `""` (any type) → token removed (surrounding whitespace collapsed).
- Any other type (`string`, `int`, `float`, `cycle`, `picker`, `color`) → written as `key=value` with the serialized value verbatim.
- Args in `changes` that don't exist in the file are appended to the `options` line; a `key:N` key is stripped to `key` when appending.
- Missing state keys read back as `"unset"` (bridged state — see Quirks).

## Quirks

- **Bridged state:** `load_state` returns a `BridgedStateDict` that claims every key exists (`__contains__` → True) and returns `"unset"` for absent keys, so optional kernel flags never render as `[Missing]`.
- Deletion/false checks are case-insensitive (`"FALSE"`, `"__DELETE__"`), but a kept value is written exactly as serialized.
- TOCTOU guard: if the file's mtime is newer than the load snapshot, the write is aborted with "File … modified externally. Reload required."
- Atomic commit: temp file + fsync + `os.replace`, preserving mode/owner. On `PermissionError` it falls back to `sudo -n tee <file>`; if that fails it returns `(False, "AUTH_REQUIRED", …)`.
- Tokenization respects single/double-quoted arguments.

## Example items

```python
ConfigItem(label="Quiet Boot", key="quiet", scope="DEFAULT", type_="bool",
           default=True, group="Boot"),
ConfigItem(label="Root UUID", key="root", scope="DEFAULT", type_="string",
           default="UUID=1234-abcd", group="Boot"),
ConfigItem(label="NVIDIA Modeset", key="nvidia-drm.modeset", scope="DEFAULT",
           type_="int", default=1, options=[0, 1], group="GPU"),
```