# DwarFS Universal Toolkit — Arch Linux (Aug 2026)

**One engine for any directory or game.** Compress once, mount instantly (no extract), writes go to `overlay-storage` (saves/mods persist, base image stays immutable). Replaces `FitGirl` `FreeArc` hours with milliseconds.

Upstream: `mhx/dwarfs` `0.15.7` (`https://github.com/mhx/dwarfs`, `/mnt/zram1/dwarfs-main`). JC141 (`-jc141`) is a curated distro of this + `fuse-overlayfs` + `bubblewrap`.

---

## Layout

```
01_universal_actions/actions.sh   # THE engine — 10 cmds: mount/unmount/extract/compress/verify/recompress/info (universal [src] [dst], game is just a profile)
02_templates/start.sh             # Single auto-detect launcher (native ELF vs Wine EXE)
02_templates/generic/             # Reference templates (copy, don't edit code)
  local.config.template           # Per-game overrides (all tunables, remove # to enable)
  profile.toml.template           # Compression + game runner profile (all options documented)
03_tools/mkdwarfs_auto.sh         # Profile-aware wrapper: --profile fast/balanced/max
03_tools/profiles/                # 7 profiles — no code edits needed
  balanced.toml  # default l7 64M zstd22 nilsimsa
  fast.toml      # l3 quick
  max.toml       # l9 lzma max ratio
  reproducible.toml # bit-identical
  game.toml      # alias to balanced (game = profile, not hardcoded)
  game_ue.toml   # l5 for .pak-heavy UE
  audio.toml     # pcmaudio (flac)
```

No hardcoded `dusk`/`12700H` — everything `$(id -u)`, `$(nproc)`, `MemTotal`, `$HOME`.

---

## Requirements (Arch `doctor` passes)

`dwarfs`, `fuse-overlayfs`, `fuse3`, `bubblewrap`, `psmisc` (`fuser`), `tree` (optional), `wine-staging` (wine games), `gamescope`/`gamemode` (optional).

---

## Quick Start

### Any folder (universal)

```bash
# Compress any dir
bash 01_universal_actions/actions.sh dwarfs-compress ~/Documents ~/Documents.dwarfs
# Or via wrapper with profile
bash 03_tools/mkdwarfs_auto.sh ~/Documents --profile balanced  # → ~/Documents.dwarfs

# Mount (no extract)
mkdir /tmp/mnt && dwarfs ~/Documents.dwarfs /tmp/mnt && ls /tmp/mnt
# Or overlay (writable)
mkdir -p /tmp/game/files && cp -a ~/Documents /tmp/game/files/game-root
cp 01_universal_actions/actions.sh /tmp/game/ && cp /usr/bin/dwarfs /tmp/game/files/dwarfs-binary
cd /tmp/game && bash actions.sh dwarfs-compress && rm -rf files/game-root
bash actions.sh dwarfs-mount && ls files/game-root && bash actions.sh dwarfs-unmount
```

### Game (native or Wine)

```bash
# 1. Prepare installed game (GOG/Steam/Lutris) → /tmp/MyGame/files/game-root
mkdir -p /tmp/MyGame/files/game-root && cp -a ~/Games/MyGame/* /tmp/MyGame/files/game-root/

# 2. Add engine + launcher
cp 01_universal_actions/actions.sh /tmp/MyGame/
cp 02_templates/start.sh /tmp/MyGame/start.sh
cp /usr/bin/dwarfs /tmp/MyGame/files/dwarfs-binary && chmod +x /tmp/MyGame/files/dwarfs-binary

# 3. Compress (game is just balanced profile)
cd /tmp/MyGame && bash actions.sh dwarfs-compress
# Generic alternative: bash ../../03_tools/mkdwarfs_auto.sh /tmp/MyGame --profile game

# 4. Run (auto-detects ./Game.x86_64 vs steamclient_loader_x64.exe vs *.exe)
bash start.sh
# Config: cp 02_templates/generic/local.config.template local.config && edit CUSTOM_CMD, GAMESCOPE, ISOLATE
```

### Master Runner (Python TOML, for library)

```bash
cp 02_templates/generic/profile.toml.template ~/user_scripts/gaming/runner/profiles/my_game.toml
# edit game_dir, executable, extends = "base_wine_dxvk" / "base_native"
python3 ~/user_scripts/gaming/runner/master_runner.py validate my_game
python3 ~/user_scripts/gaming/runner/master_runner.py run my_game
```

---

## Profiles — No Code Edits

Add a game or folder by **copying a TOML**, not editing code:

```bash
cp 03_tools/profiles/balanced.toml 03_tools/profiles/my.toml
# edit level, block_size_bits, categorize
DWARFS_PROFILE=my bash actions.sh dwarfs-compress ~/MyFolder ~/MyFolder.dwarfs
# Or: bash 03_tools/mkdwarfs_auto.sh ~/MyGame --profile my
```

| Profile | Use |
|---|---|
| `balanced` | Default `l7` 64M — games, docs |
| `fast` | `l3` quick test |
| `max` | `l9` lzma — archival |
| `reproducible` | Bit-identical (`--set-time=0 --num-workers=1`) |
| `game` | Alias to `balanced` (game = profile) |
| `game_ue` | `l5` for `.pak` (UE, already compressed) |
| `audio` | `pcmaudio` flac for `.wav` |

Env overrides (alternative to profile): `DWARFS_REPRODUCIBLE=1`, `DWARFS_AUTOCATEGORIZE=1`, `DWARFS_OWNER=1000`, `DWARFS_PROFILE=game`, `DWARFS_FILTER="-*.tmp"`.

---

## How It Works

1. **Compress:** `mkdwarfs -l7 -B26 -S26 --order=nilsimsa` → `game-root.dwarfs` (dedup + similarity, 64M blocks, `zstd22`). `tree` → `dwarfs-tree`.
2. **Mount:** `dwarfs image mnt -o cachesize=25%RAM,clone_fd,tidy_strategy=time` (kernel cache) + `fuse-overlayfs` (`lowerdir=mnt,upperdir=overlay-storage,workdir=.work` → `game-root`). Base is read-only, writes go to `upperdir` (saves survive remount).
3. **Verify:** `dwarfs-verify` = `dwarfsck --check-integrity`.
4. **Recompress:** `dwarfs-recompress 5` = `mkdwarfs --recompress -l5` (no rescan).

---

## Config (local.config.template)

All tunables in one file — `local.config` per game overrides `~/.jc141rc` global. See `02_templates/generic/local.config.template` for every option (`UNMOUNT`, `EXTRACT`, `SYSWINE`, `ISOLATE`, `BLOCK_NET`, `GAMESCOPE`, `DWARFS_*`). Remove `#` to enable.

---

## Verify

```bash
bash 01_universal_actions/actions.sh dwarfs-compress ~/Documents ~/Documents.dwarfs
dwarfsck -i ~/Documents.dwarfs --check-integrity && echo OK
dwarfs ~/Documents.dwarfs /tmp/mnt && ls /tmp/mnt && fusermount3 -u /tmp/mnt
```

Years from now: `README` + `local.config.template` + `profile.toml.template` are your only docs — everything configurable lives there.

