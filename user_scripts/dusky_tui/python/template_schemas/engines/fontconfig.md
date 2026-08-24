# Engine: `fontconfig`

- **Class:** `FontconfigEngine` — `engines/fontconfig.py`
- **Engine types:** `fontconfig`
- **Default target:** `~/.config/fontconfig/conf.d/99-dusky-fonts.conf` (expanded + resolved; the schema may point `TARGET_FILE` elsewhere).

## Target format

A fontconfig XML file. The engine regenerates the whole file from its state on every write: generic family aliases (`<alias binding="strong">`), `<dir>` entries, render settings in `<match target="font">` blocks (`mode="assign"`), and preserved legacy pattern rewrites:

```xml
<fontconfig>
  <alias binding="strong"><family>sans-serif</family><prefer><family>Inter</family></prefer></alias>
  <dir>/usr/share/fonts</dir>
  <match target="font"><edit mode="assign" name="antialias"><bool>true</bool></edit></match>
</fontconfig>
```

## Scope / key mapping

All keys are `scope="DEFAULT"`. Unlike most engines, `load_state()` does NOT emit `DEFAULT/`-prefixed copies.

**Family aliases** (generic class → prefer list; value is a single family `str`, or a `list` for multiple):

`sans-serif`, `serif`, `monospace`, `emoji`, `sans`

Any OTHER string/list-valued key outside the render whitelist and dir keys is also emitted as an alias (custom generic classes are supported). Avoid keys named `family` / `familylang` — edits with those names are ignored on load.

**Render properties** (emitted in one `<match target="font">` block):

| key | type | accepted values |
|---|---|---|
| `antialias` | bool | |
| `hinting` | bool | |
| `autohint` | bool | |
| `embeddedbitmap` | bool | (see emoji guard quirk) |
| `hintstyle` | picker/cycle/string | `hintnone`, `hintslight`, `hintmedium`, `hintfull` |
| `rgba` | picker/cycle/string | `none`, `rgb`, `bgr`, `vrgb`, `vbgr` |
| `lcdfilter` | picker/cycle/string | `lcdnone`, `lcddefault`, `lcdlight`, `lcdlegacy` |
| `rasterizer` | string | |

**Directories:** `font_dir` (str or list). `font_dirs` is accepted on write too; on load only `font_dir` is emitted. Paths are `~`-expanded, resolved to absolute, deduplicated and sorted.

## Types & value handling

- Values serialize by Python type: `bool` → `<bool>`, `int` → `<int>`, `float` → `<double>`, numeric strings → `<int>`/`<double>`, known consts (above) → `<const>`, anything else → `<string>`.
- **Deletion sentinel:** a value of `""` or `None` removes the key from the file entirely (setting = `state.pop(key)`).
- Family alias values: single string → one `<prefer><family>`; list → one per entry.

## Quirks

- `embeddedbitmap=false` is emitted in its OWN `<match target="font">` block guarded by `<test name="family" compare="not_eq">Noto Color Emoji</test>` (a shared block would exempt the emoji font from the other render settings too), plus a derived `<test compare="eq">` block pinning `embeddedbitmap=true` for the emoji font — the derived block is dropped on load, the guard block feeds state.
- Hand-authored pattern rewrites (`target="pattern"` blocks and any `match` with a family/familylang test) are preserved verbatim across write cycles, but on write are neutered: family tests get `qual="first"`, and rewrites targeting an alias class, a current prefer family, or the synonyms `times new roman` / `liberation serif` / `vera serif` are dropped.
- Legacy `~/.config/fontconfig/fonts.conf` is absorbed on the first write when the target file is missing, then deleted (no backup).
- After every write: `fc-cache -f` is launched asynchronously (if installed) and `sync_system_fonts()` mirrors `sans-serif`/`monospace` into GTK (`gtk-3.0`/`gtk-4.0` `settings.ini` `gtk-font-name`, removing `gtk-monospace-font-name`; `gsettings` `font-name`/`document-font-name`/`monospace-font-name`) and Qt (`qt5ct`/`qt6ct` `.conf` `[Fonts]` `general`/`fixed`), reusing existing sizes.
- Running the file standalone (`python3 .../engines/fontconfig.py`) re-runs the toolkit sync — a schema action item can invoke it.
- Missing or empty target file → empty state.

## Example items

```python
ConfigItem(label="Sans Serif", key="sans-serif", scope="DEFAULT", type_="picker",
           default="Inter", options=["Inter", "Roboto", "JetBrains Mono"],
           hints=["UI", "UI", "Monospace"], group="Families"),
ConfigItem(label="Monospace", key="monospace", scope="DEFAULT", type_="picker",
           default="JetBrains Mono", options=["JetBrains Mono", "Fira Code"], group="Families"),
ConfigItem(label="Antialias", key="antialias", scope="DEFAULT", type_="bool",
           default=True, group="Rendering"),
ConfigItem(label="Hint Style", key="hintstyle", scope="DEFAULT", type_="picker",
           default="hintslight",
           options=["hintnone", "hintslight", "hintmedium", "hintfull"],
           hints=["No pixel alignment", "Light alignment (Recommended)",
                  "Medium alignment", "Strict pixel alignment"], group="Rendering"),
ConfigItem(label="Sync GTK & Qt", key="action_sync_toolkits", scope="DEFAULT",
           type_="action", default="python3 ~/user_scripts/dusky_tui/python/engines/fontconfig.py",
           group="Actions"),
```