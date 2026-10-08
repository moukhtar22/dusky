# DwarFS compression and game toolkit

Linux, Bash, Python (stdlib TOML), DwarFS and FUSE3. Audited with DwarFS
0.15.7, Bash 5.3.20, Python 3.14.7 and fuse-overlayfs 1.18. Verify the final
ISO package versions before release. Games optionally use Wine, bubblewrap
and gamescope. No Xorg compatibility setup is included.

## Compress any directory

Run from the toolkit directory:

```bash
bash 03_tools/mkdwarfs_auto.sh ~/Documents --profile balanced
# Output: ~/Documents.dwarfs
bash 03_tools/mkdwarfs_auto.sh --profile fast ~/Documents --output ~/Documents-fast.dwarfs
bash 01_universal_actions/actions.sh dwarfs-compress ~/Documents ~/archive.dwarfs
DWARFS_IMAGE=~/archive.dwarfs bash 01_universal_actions/actions.sh dwarfs-verify
DWARFS_IMAGE=~/archive.dwarfs bash 01_universal_actions/actions.sh dwarfs-info
DWARFS_IMAGE=~/archive.dwarfs bash 01_universal_actions/actions.sh dwarfs-recompress 5
```

Options can appear before or after the source: `-l N`, `--level N`, `-lN`,
`--profile NAME`, `--output IMAGE`, `--reproducible`, `--par2`. A source containing
`files/game-root` is treated as a game bundle; otherwise the directory itself
is compressed. Explicit CLI choices override configuration defaults.
A level override takes precedence over the profile's level;
explicit profile codec and block settings still apply.

Existing outputs produce an error. Output must be outside the source tree.
Empty directories are supported. Partial images and interrupted extractions
stay in temporary sibling directories and are cleaned on ordinary failure or
handled signals. Signal forwarding stops workers and waits for cleanup before
the command returns. SIGKILL or power loss can leave these temporary directories;
the final image is published only after compression completes. Recompression
verifies the temporary image before replacing the original. Publication by
`mv --no-copy` must succeed as a rename; it cannot fall back to a partial copy.

## Game bundle

```bash
mkdir -p /tmp/MyGame/files/game-root
cp -a ~/Games/MyGame/. /tmp/MyGame/files/game-root/
cp 01_universal_actions/actions.sh /tmp/MyGame/actions.sh
cp 02_templates/start.sh /tmp/MyGame/start.sh
cp 03_tools/profiles/balanced.toml /tmp/MyGame/balanced.toml
cp 02_templates/generic/local.config.template /tmp/MyGame/local.config
cd /tmp/MyGame
bash actions.sh dwarfs-compress
bash actions.sh dwarfs-verify
# After verification, move the original game-root elsewhere to enable mounting.
bash start.sh
```

System DwarFS tools are used by default. An optional `files/dwarfs-binary`
can be a universal executable (`--tool=...`), standalone FUSE driver, or
standalone compressor; other tools come from PATH. A copied engine needs a profile
TOML beside it or `DWARFS_PROFILES_DIR` pointing to the toolkit's profiles.

The launcher prefers `steamclient_loader_x64.exe`, then an executable
`*.x86_64`, then an EXE, searching at most three directory levels. Set
`CUSTOM_CMD=("./My Game" "--option")` when discovery is ambiguous. Strings
support shell quoting but no expansion. Launcher arguments are appended.
Automatic Wine launch uses the absolute EXE path and honors `SYSWINE` and
`WINEPREFIX`; Wine initializes a missing prefix. A custom Wine command should
set `WINEPREFIX` and `ISOLATION_TYPE=wine` explicitly if isolation is enabled.

## Mounting and extraction

`dwarfs-mount` mounts the image read-only and adds a writable fuse-overlayfs
layer at `files/game-root`. Writes persist in `files/overlay-storage`, with
`files/.game-root-work` on the same filesystem. The block cache limit is 25%
of physical RAM per mounted image; DwarFS uses its documented cache tidying
options. Paths for overlays cannot contain commas, colons or backslashes (option delimiters).

Mounting is idempotent. Directory locks serialize mount/unmount/extraction
transitions for each `GAME_DIR`. A launcher holds the lock for its session; a
second launcher for the same directory fails immediately. Standalone mount and
unmount commands wait for that session to finish. FUSE daemons do not inherit
the lock. Local Linux filesystems with working `flock` are required.
Existing extracted files are used as-is. Unmounting uses normal FUSE3 unmounts, reports busy mounts, retains backing directories
on failure, and removes only empty mountpoint directories. It never kills
unrelated game processes or recursively deletes backing directories.
The launcher forwards signals to the launched process group, then cleans up
only a mount it created and only with `UNMOUNT=1`.
If mounting fails, it attempts a clean unmount before extraction.

`dwarfs-extract` extracts into a sibling temporary directory before publishing
`files/game-root`. A nonempty existing extracted directory is treated as
already installed; move incomplete pre-existing extractions aside to retry.
`dwarfs-extract-language` uses `_LANGUAGE` from `language.config`.
When selected, a language image takes precedence over the base mount.
Compression with no source arguments also compresses visible language
subdirectories; explicit source arguments operate on that source only.

## Profiles and configuration

| Profile | Settings |
|---|---|
| balanced | Level 7, 16MiB blocks, zstd22, nilsimsa, one-block lookback |
| game | Level 7, 16MiB blocks, nilsimsa; game path convention |
| fast | Level 3, 2MiB blocks, lz4hc9 |
| max | Level 9, 64MiB blocks, lzma9; slower reads |
| reproducible | Level 7, fixed time, no creation timestamp/history, one worker |
| game_ue | Level 5, 8MiB blocks, zstd19, incompressible category with null codec |
| audio | Level 7, zstd22; PCM waveforms use FLAC |

Profiles are parsed with `tomllib`; malformed TOML, unknown keys and invalid
basic types fail before compression. `input`/`output` in game profiles are
descriptive; command arguments and path environment variables select paths.
`profile.toml.template` includes commented runner examples; the separate
Python game runner is not required by this toolkit.

Configuration loads in order: `~/.jc141rc`, `GAME_DIR/local.config`, then
`GAME_DIR/language.config`. These are trusted Bash files. No configuration
files are automatically generated. Paths default to the directory containing
`actions.sh`. Configured paths resolve against `GAME_DIR`; explicit relative
source/image arguments use the calling working directory.
See `local.config.template` for launcher options and overrides.

Compression preserves source permissions and timestamps unless the profile
or reproducible mode overrides them; image ownership defaults to the current
UID/GID. `DWARFS_REPRODUCIBLE=1` fixes time and worker settings and removes the
creation timestamp. Reproducibility requires unchanged input, profile and tool
build; it is not a guarantee across DwarFS or codec upgrades.
`DWARFS_AUTOCATEGORIZE=1` scans filenames once: more than five WAVs enables
PCM categorization; over 500MiB of PAK/ZIP/MP4/BIN files enables incompressible
categorization. Automatic categorization puts `incompressible` last, allowing
specialized categorizers to inspect files first. `DWARFS_PAR2=1` requires `par2`.

`DWARFS_EXTRA_OPTS`, `DWARFSEXTRACT_EXTRA`, and `ADDITIONAL_FLAGS` accept quoted
argument strings. `ENV` is trusted shell setup executed before the command.
`ISOLATE=1` uses bubblewrap with writable game files and Wine prefix; native
launches use `JC_DIRECTORY/native-docs` as the writable home. Narrow game binds
follow the home bind so games under `$HOME` stay visible.
An external `GAME_ROOT` is also writable. The existing Wayland/audio runtime
sockets remain visible. `GAMESCOPE=1` wraps the launch.
These optional graphical paths require testing in the target desktop session.

`dwarfs-verify` checks integrity of every image block. Optional
`DWARFS_VERIFY_CHECKSUM=1` also prints SHA-256 checksums of image files; these
are not automatically compared against the host's source files.

Upstream reference: https://github.com/mhx/dwarfs/blob/main/doc/mkdwarfs.md
