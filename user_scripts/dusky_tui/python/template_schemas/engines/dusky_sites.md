# Engine: `dusky_sites`

- **Class:** `DuskySitesEngine` — `engines/dusky_sites.py`
- **Engine types:** `dusky_sites`
- **Default target:** `~/.config/dusky/settings/dusky_sites/config.json`
  (also manages `~/.config/dusky_sites/*.css` site templates)

## Scope / key mapping

All keys are `scope="DEFAULT"`:

| key | type | notes |
|---|---|---|
| `webThemeEnabled` | bool | master web theming switch |
| `forceUnthemedWebsites` | bool | |
| `ecoMode` | bool | |
| `browserThemeEnabled` | bool | browser UI theme |
| `userChromeEnabled` | bool | userChrome.css integration |
| `userContentEnabled` | bool | userContent.css integration |
| `colorsPath` | string | matugen CSS path (`~/.config/matugen/generated/dusky_sites.css`) |
| `websitesDir` | string | `~/.config/dusky_sites` |
| `site_<css_stem>` | bool | per-site enable/disable (also cached as `domain_<domain>`) |
| `action_add_site` | string | create a new `<domain>.css` template from an inline `@-moz-document domain(...)` stub |
| `action_delete_site` | string | delete a site template |

## Types & value handling

- Per-site keys derive from the CSS filename stem, e.g. `site_github.com`; the
  engine matches stems with `.`/`-` normalized to `_` and toggles the
  `disabledSites` array in the config JSON.
- `action_add_site` / `action_delete_site` are `string` items whose value is
  the domain; the engine creates/deletes the matching CSS file.

## Example items

```python
ConfigItem(label="Web Theming", key="webThemeEnabled", scope="DEFAULT",
           type_="bool", default=False, group="Global"),
ConfigItem(label="Add Site", key="action_add_site", scope="DEFAULT",
           type_="string", default="", group="Actions"),
ConfigItem(label="Remove Site", key="action_delete_site", scope="DEFAULT",
           type_="string", default="", group="Actions"),
```
