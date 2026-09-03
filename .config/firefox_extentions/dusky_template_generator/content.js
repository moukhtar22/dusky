/*
 * Dusky Template Generator — content.js
 *
 * Injected on demand (activeTab) by background.js. Two jobs:
 *   scan    map the site's colour custom properties to Matugen palette tokens
 *   picker  click page elements, give them a palette role, see it live; every
 *           change is written straight into the template's picks region on disk
 *
 * Each rule is one plain CSS line — "selector { declarations }" followed by a short
 * meta comment — hydrated from disk when the picker starts, so disk stays the only truth.
 * All UI lives in a closed shadow root: page CSS and page scripts cannot touch it.
 * Nothing is registered on the page while the picker is idle.
 */
"use strict";
(() => {
  if (globalThis.__duskyTemplateGenerator) return;
  globalThis.__duskyTemplateGenerator = true;

  // ─── palette contract (matugen/generated/dusky_sites.css) ─────────────────
  const TOKENS = [
    ["surface", "Surface"], ["surface_container", "Surface container"],
    ["surface_container_high", "Surface container high"], ["surface_container_low", "Surface container low"],
    ["primary", "Primary"], ["primary_container", "Primary container"],
    ["secondary", "Secondary"], ["secondary_container", "Secondary container"],
    ["tertiary", "Tertiary"], ["tertiary_container", "Tertiary container"],
    ["on_surface", "On surface (text)"], ["on_surface_variant", "On surface variant"],
    ["outline", "Outline (border)"], ["outline_variant", "Outline variant"],
    ["error", "Error"], ["error_container", "Error container"]
  ];
  const ACCENT = /^(primary|secondary|tertiary|error)(_container)?$/;   // tokens that have an on_* partner
  const paletteLoaded = () => !!getComputedStyle(document.documentElement).getPropertyValue("--surface").trim();

  // ─── ⚡ scan: site custom properties → tokens ──────────────────────────────
  const SKIP_NAME = /^--(tw|fa|darkreader|dusky)-|^--(surface|primary|secondary|tertiary|error|outline)(_[a-z_]+)?$|^--on_/;
  const IS_COLOR = /^(#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$|(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|light-dark)\()/i;
  const IS_RGB_TRIPLET = /^\d{1,3}(?:\s*,\s*|\s+)\d{1,3}(?:\s*,\s*|\s+)\d{1,3}$/;
  const IS_HSL_TRIPLET = /^[\d.]+(?:deg)?\s+[\d.]+%\s+[\d.]+%$/;
  const KEYWORD = /^(inherit|initial|unset|revert|revert-layer|currentcolor|transparent|none|auto)$/i;
  const NAME_RULES = [
    [/(^|-)(error|danger|destructive|critical|negative)(-|$)/, "error"],
    [/(^|-)(border|outline|divider|stroke|separator|rule)(-|$)/, "outline"],
    [/(^|-)(text|txt|fg|foreground|font|heading|title|label|caption|placeholder|ink|on)(-|$)/, "text"],
    [/(^|-)(primary|brand|accent|link|highlight|selected|active|focus|selection|interactive|cta)(-|$)/, "primary"],
    [/(^|-)secondary(-|$)/, "secondary"],
    [/(^|-)tertiary(-|$)/, "tertiary"],
    [/(^|-)(overlay|tooltip|popover|popup|dropdown|menu|modal|dialog|elevated|raised|floating|toast|sheet|drawer)(-|$)/, "surface_container_high"],
    [/(^|-)(inset|sunken|well|track|input|field|code|pre|subtle|muted|hover)(-|$)/, "surface_container_low"],
    [/(^|-)(card|container|panel|sidebar|sidenav|nav|navbar|header|footer|toolbar|box|tile|widget|section|elevation|level|layer)(-|$)|(^|-)(surface|bg|background)-?\d/, "surface_container"],
    [/(^|-)(bg|background|surface|canvas|page|body|base|app|root|default|window|backdrop|paper|main)(-|$)/, "surface"]
  ];
  const TEXT_ACCENT = /(^|-)(accent|link|brand)(-|$)/;
  const TEXT_MUTED = /(^|-)(secondary|muted|subtle|tertiary|disabled|placeholder|hint|dim|faint|weak|quiet|soft|light)(-|$)/;

  function tokenFor(name) {
    const n = name.slice(2).toLowerCase().replace(/[_.]/g, "-");
    for (const [re, token] of NAME_RULES) {
      if (!re.test(n)) continue;
      if (token !== "text") return token;
      if (TEXT_ACCENT.test(n)) return "primary";
      return TEXT_MUTED.test(n) ? "on_surface_variant" : "on_surface";
    }
    return "";
  }

  // Only variables in effect on <html>/<body> can be overridden from ":root, body";
  // component-scoped ones cannot, so those are not collected at all.
  function collectVariables() {
    const names = new Set();
    const rootCs = getComputedStyle(document.documentElement);
    const bodyCs = document.body ? getComputedStyle(document.body) : rootCs;
    for (const cs of [rootCs, bodyCs]) for (const p of cs) if (p.startsWith("--")) names.add(p);
    const rootish = /(^|,)\s*(:root|html|body)\b/;
    const walk = (rules) => {
      for (const r of rules) {
        if (r.styleSheet) { try { walk(r.styleSheet.cssRules); } catch (_) { /* cross-origin @import */ } }
        if (r.cssRules && r.cssRules.length) walk(r.cssRules);
        if (r.style && r.selectorText && rootish.test(r.selectorText)) {
          for (const p of r.style) if (p.startsWith("--")) names.add(p);
        }
      }
    };
    for (const sheet of document.styleSheets) {
      try { walk(sheet.cssRules); } catch (_) { /* cross-origin sheet: computed style still covers it */ }
    }
    const out = new Map();
    for (const n of names) {
      const v = (rootCs.getPropertyValue(n) || bodyCs.getPropertyValue(n)).trim();
      if (v) out.set(n, v);
    }
    return out;
  }

  function scan() {
    const groups = new Map();
    const unmapped = [];
    let found = 0;
    for (const [name, value] of collectVariables()) {
      if (SKIP_NAME.test(name)) continue;
      let kind = "";
      if (IS_COLOR.test(value)) kind = "color";
      else if (IS_RGB_TRIPLET.test(value)) kind = "rgb";
      else if (IS_HSL_TRIPLET.test(value)) kind = "hsl";
      else if (/^[a-z]+$/i.test(value) && !KEYWORD.test(value) && CSS.supports("color", value)) kind = "color";
      if (!kind) continue;
      found++;
      const token = tokenFor(name);
      if (!token || kind === "hsl") { unmapped.push(name); continue; }
      const key = kind === "rgb" ? token + "_rgb" : token;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(name);
    }
    const order = TOKENS.flatMap(([t]) => [t, t + "_rgb"]);
    const lines = ["    :root, body {"];
    let mapped = 0;
    for (const key of order) {
      const names = groups.get(key);
      if (!names) continue;
      lines.push("        /* " + key.replace(/_rgb$/, " (rgb components)") + " */");
      for (const n of names.sort()) { lines.push("        " + n + ": var(--" + key + ") !important;"); mapped++; }
    }
    lines.push("    }");
    if (unmapped.length) {
      const shown = unmapped.sort().slice(0, 40).join(", ") + (unmapped.length > 40 ? ", +" + (unmapped.length - 40) + " more" : "");
      lines.push("    /* colour variables left unmapped (map by hand if you like): " + shown + " */");
    }
    return { ok: true, found, mapped, body: mapped ? lines.join("\n") : "" };
  }

  // ─── 🎯 picker: state and rule model ──────────────────────────────────────
  const S = {
    active: false, hydrated: false, note: "",
    rules: [],                // [{ sel, decl, meta }] — or { raw } for a hand-edited line we could not parse
    undo: [], redo: [],       // snapshots of rules, this page session only
    stack: [], depth: 0,      // ancestor chain of the hovered element; depth 0 = the element itself
    locked: false,            // true while the dialog is open: hovering no longer retargets
    group: "bg", dialogPos: null, raf: 0, saveSeq: 0
  };
  const RULE_RE = /^(.+?)\s*\{\s*(.*?)\s*\}\s*(?:\/\*\s*(.*?)\s*\*\/)?$/;
  const parseRule = (line) => { const m = RULE_RE.exec(line); return m ? { sel: m[1], decl: m[2], meta: m[3] || "" } : { raw: line }; };
  const ruleCss = (r) => (r.raw !== undefined ? r.raw : r.sel + " { " + r.decl + " }");
  const ruleLine = (r) => (r.raw !== undefined ? r.raw : ruleCss(r) + (r.meta ? " /* " + r.meta + " */" : ""));
  const target = () => S.stack[S.depth] || null;

  const GROUPS = {
    bg: { extra: ["👻 Transparent", "background: transparent !important; box-shadow: none !important;", "bg: transparent"] },
    text: { extra: ["↩ Inherit colour", "color: inherit !important;", "text: inherit"] },
    border: { extra: ["⊘ No border", "border-color: transparent !important;", "border: none"] }
  };
  function declFor(group, token) {
    if (group === "text") return "color: var(--" + token + ") !important;";
    if (group === "border") return "border-color: var(--" + token + ") !important;";
    let d = "background: var(--" + token + ") !important;";
    if (ACCENT.test(token)) d += " color: var(--on_" + token + ", var(--surface)) !important;";
    return d;
  }
  function important(text) {
    return text.split(";").map((d) => d.trim()).filter(Boolean)
      .map((d) => (/!important$/i.test(d) ? d : d + " !important") + ";").join(" ");
  }

  // ─── styles applied to the page itself ────────────────────────────────────
  const liveStyle = document.createElement("style");
  const hoverStyle = document.createElement("style");
  function mountStyles() {
    if (!liveStyle.isConnected) (document.head || document.documentElement).append(liveStyle, hoverStyle);
  }
  function renderLive() { mountStyles(); liveStyle.textContent = S.rules.map(ruleCss).join("\n"); }
  function setHover(css) { mountStyles(); hoverStyle.textContent = css || ""; }

  // ─── shadow UI ────────────────────────────────────────────────────────────
  const UI_CSS = [
    // NOTE: the outer stacking order is decided by the *host* element, not by
    // anything inside the shadow root. A fixed panel with z-index 2147483647
    // inside the shadow still paints *below* any page element with a positive
    // z-index when the host itself is z-index:auto — which is exactly why the
    // dialog looked "hidden behind" white cards. The host must carry the max
    // z-index itself, plus pointer-events:none so only panels hit-test.
    ":host { all: initial !important; display: block !important; position: fixed !important; top: 0 !important; left: 0 !important; width: 0 !important; height: 0 !important; overflow: visible !important; z-index: 2147483647 !important; pointer-events: none !important; isolation: isolate !important; }",
    "* { box-sizing: border-box; }",
    ".mask { position: fixed; z-index: 2147483646; display: none; pointer-events: none !important; border-radius: 2px; outline: 2px dashed #e6c280; box-shadow: 0 0 0 200vmax rgba(18, 15, 12, 0.5); }",
    ".panel { position: fixed; z-index: 2147483647; pointer-events: auto; background: #191614; color: #f5ebe0; border: 1px solid #d4a359; border-radius: 10px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.75); font: 12px/1.4 system-ui, sans-serif; user-select: none; }",
    ".bar { top: 12px; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: 8px; padding: 6px 10px; white-space: nowrap; cursor: grab; touch-action: none; max-width: calc(100vw - 24px); }",
    ".grip { opacity: 0.5; padding: 0 2px; cursor: grab; }",
    ".title { font-weight: 700; color: #e6c280; }",
    ".info { max-width: 340px; overflow: hidden; text-overflow: ellipsis; color: #c4b8aa; font: 11px ui-monospace, monospace; }",
    ".state { font-size: 11px; color: #c4b8aa; } .state.ok { color: #81c784; } .state.err { color: #e57373; } .state.warn { color: #e6c280; }",
    "button { font: inherit; color: #f5ebe0; background: #2d2722; border: 1px solid #3d342c; border-radius: 6px; padding: 4px 8px; cursor: pointer; white-space: nowrap; }",
    "button:hover:not(:disabled) { border-color: #d4a359; } button:disabled { opacity: 0.4; cursor: default; }",
    "button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid #e6c280; outline-offset: 1px; }",
    ".x { background: #b8545e; border-color: #b8545e; color: #fff; font-weight: 700; }",
    ".grow { flex: 1; }",
    ".dlg { top: 64px; right: 16px; width: 390px; max-width: calc(100vw - 32px); max-height: calc(100vh - 96px); overflow: auto; padding: 12px; outline: none; transition: opacity 0.15s; }",
    ".dlg.ghost:not(:hover):not(:focus-within) { opacity: 0.25; }",
    ".head { display: flex; align-items: center; gap: 6px; padding-bottom: 8px; margin-bottom: 8px; border-bottom: 1px solid #3d342c; cursor: grab; touch-action: none; }",
    ".head .title { flex: 1; }",
    ".row { display: flex; align-items: center; gap: 6px; margin: 6px 0; }",
    ".lbl { flex: none; width: 58px; color: #c4b8aa; font-size: 11px; }",
    ".tag { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #e6c280; font: 11px ui-monospace, monospace; }",
    "input[type=range] { flex: 1; accent-color: #e6c280; margin: 0; }",
    "select, input[type=text] { flex: 1; min-width: 0; font: 11px ui-monospace, monospace; color: #f5ebe0; background: #25201c; border: 1px solid #3d342c; border-radius: 6px; padding: 5px 6px; user-select: text; }",
    ".seg { flex: 1; display: flex; } .seg button { flex: 1; border-radius: 0; } .seg button:first-child { border-radius: 6px 0 0 6px; } .seg button:last-child { border-radius: 0 6px 6px 0; }",
    ".seg button[aria-pressed=true] { background: #e6c280; border-color: #e6c280; color: #191614; font-weight: 700; }",
    ".grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; margin: 8px 0; }",
    ".grid button { display: flex; align-items: center; gap: 7px; text-align: left; padding: 4px 7px; }",
    ".sw { flex: none; width: 13px; height: 13px; border-radius: 50%; border: 1px solid #55493d; }",
    ".hint { margin: 8px 0 0; color: #8f857a; font-size: 10.5px; }",
    ".drawer { bottom: 16px; right: 16px; width: 380px; max-width: calc(100vw - 32px); max-height: 60vh; padding: 12px; display: flex; flex-direction: column; }",
    ".list { overflow: auto; display: flex; flex-direction: column; gap: 4px; }",
    ".item { display: flex; align-items: center; gap: 6px; padding: 4px 6px; background: #25201c; border: 1px solid #3d342c; border-radius: 6px; }",
    ".item:hover { border-color: #d4a359; }",
    ".item .sel { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 11px ui-monospace, monospace; }",
    ".item .meta { flex: none; color: #e6c280; font-size: 10.5px; }",
    ".item button { padding: 1px 6px; }"
  ].join("\n");

  const BAR_HTML = [
    "<span class='grip' aria-hidden='true'>⠿</span>",
    "<span class='title'>🎯 Dusky picker</span>",
    "<span class='info' id='binfo'></span>",
    "<span class='state' id='bstate'></span>",
    "<button id='bundo' title='Undo (Ctrl+Z)'>↶</button>",
    "<button id='bredo' title='Redo (Ctrl+Shift+Z)'>↷</button>",
    "<button id='brules' title='Rules saved for this site'>Rules</button>",
    "<button id='bexit' class='x' title='Stop picking (Esc)'>✕ Exit</button>"
  ].join("");

  const DIALOG_HTML = [
    "<div class='head' id='dhead'>",
    "<span class='grip' aria-hidden='true'>⠿</span>",
    "<span class='title'>🎨 Theme this element</span>",
    "<button id='dghost' title='See-through while the pointer is elsewhere' aria-pressed='false'>👁</button>",
    "<button id='dclose' title='Close (Esc)'>✕</button>",
    "</div>",
    "<div class='row'><span class='lbl'>Element</span><span class='tag' id='dtag'></span></div>",
    "<div class='row'><span class='lbl'>Depth</span>",
    "<button id='dchild' title='Down, towards what you hovered (↓)'>↓ child</button>",
    "<input type='range' id='dslider' min='0' max='0' value='0' aria-label='DOM depth: 0 is the clicked element, higher values select its parents'>",
    "<button id='dparent' title='Up, to the parent element (↑)'>↑ parent</button>",
    "</div>",
    "<div class='row'><span class='lbl'>Selector</span><select id='dsel' aria-label='CSS selector written to the template'></select></div>",
    "<div class='row'><span class='lbl'>Apply to</span>",
    "<div class='seg' id='dseg' role='group' aria-label='Property'>",
    "<button data-group='bg' aria-pressed='true'>Background</button>",
    "<button data-group='text' aria-pressed='false'>Text</button>",
    "<button data-group='border' aria-pressed='false'>Border</button>",
    "</div></div>",
    "<div class='grid' id='dgrid'></div>",
    "<div class='row'><button id='dextra' class='grow'></button>",
    "<button id='dhide' class='x grow' title='display: none — Shift+click on the page does this in one go'>🙈 Hide element</button></div>",
    "<div class='row'><input type='text' id='dcustom' placeholder='custom CSS, e.g. border-radius: 8px; opacity: .9' aria-label='Custom CSS declarations'>",
    "<button id='dapply'>Apply</button></div>",
    "<p class='hint' id='dhint'>Hover a swatch to preview · click to save · ↑ ↓ change depth · Esc closes</p>"
  ].join("");

  const DRAWER_HTML = [
    "<div class='head' id='rhead'>",
    "<span class='grip' aria-hidden='true'>⠿</span>",
    "<span class='title'>📋 Rules for this site</span>",
    "<button id='rclear' class='x' title='Remove every rule (Ctrl+Z brings them back)'>Clear all</button>",
    "<button id='rclose' title='Close'>✕</button>",
    "</div>",
    "<div class='list' id='rlist'></div>",
    "<p class='hint'>Hover a rule to highlight its elements · ✕ removes it · these are the lines in the picks block of the template</p>"
  ].join("");

  const host = document.createElement("dusky-picker");
  // Defense in depth: page stylesheets CAN target the host element itself
  // (closed shadow DOM does not protect it). Inline !important styles beat any
  // page rule — e.g. sites with `* { position: static }` or a competing
  // `dusky-picker { z-index: 0 }` — and keep the picker in the top layer.
  // Keep in sync with the `:host` rule above.
  for (const [prop, value] of [
    ["all", "initial"], ["display", "block"], ["position", "fixed"],
    ["top", "0"], ["left", "0"], ["width", "0"], ["height", "0"],
    ["overflow", "visible"], ["z-index", "2147483647"],
    ["pointer-events", "none"], ["isolation", "isolate"],
  ]) {
    try { host.style.setProperty(prop, value, "important"); } catch (_) { /* very old engine */ }
  }
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = "<style>" + UI_CSS + "</style><div class='mask' id='mask'></div>";
  const q = (id) => root.getElementById(id);
  const isOurs = (e) => {
    try {
      if (e.composedPath().includes(host)) return true;
    } catch (_) { /* composedPath unavailable — fall through */ }
    // Fallback for synthetic events: anything whose target lives inside the
    // shadow root retargets to the host in the light DOM.
    try {
      const t = e.target;
      if (t === host) return true;
      if (t instanceof Node && t.getRootNode() === root) return true;
    } catch (_) { /* ignore */ }
    return false;
  };
  let bar = null, dialog = null, drawer = null;

  function el(tag, attrs, ...children) {
    const n = document.createElement(tag);
    for (const k in attrs) {
      const v = attrs[k];
      if (k === "text") n.textContent = v;
      else if (k === "class") n.className = v;
      else if (k === "style") Object.assign(n.style, v);
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (k === "disabled") n.disabled = !!v;
      else n.setAttribute(k, v);
    }
    n.append(...children);
    return n;
  }

  function drag(panel, handle, onMove) {
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest("button, input, select, textarea, a, [contenteditable]")) return;
      // Ignore clicks that would start a text selection inside the handle itself.
      if (e.target.closest("input, select, textarea")) return;
      const r = panel.getBoundingClientRect();
      const ox = e.clientX - r.left, oy = e.clientY - r.top;
      const w = r.width, h = r.height;
      const move = (ev) => {
        const x = Math.min(Math.max(0, ev.clientX - ox), Math.max(0, innerWidth - w));
        const y = Math.min(Math.max(0, ev.clientY - oy), Math.max(0, innerHeight - h));
        Object.assign(panel.style, { left: x + "px", top: y + "px", right: "auto", bottom: "auto", transform: "none" });
        if (onMove) onMove(x, y);
      };
      const stop = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", stop);
        handle.removeEventListener("pointercancel", stop);
        try { if (handle.hasPointerCapture(e.pointerId)) handle.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
      };
      try { handle.setPointerCapture(e.pointerId); } catch (_) { /* touch/mouse without capture support */ }
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", stop);
      handle.addEventListener("pointercancel", stop);
      e.preventDefault();
      e.stopPropagation();
    });
  }

  // ─── spotlight mask ───────────────────────────────────────────────────────
  function drawMask() {
    const m = q("mask"), t = target();
    if (!t || !t.isConnected) { m.style.display = "none"; return; }
    const r = t.getBoundingClientRect();
    Object.assign(m.style, { display: "block", left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" });
  }
  function scheduleMask() {
    if (!S.raf) S.raf = requestAnimationFrame(() => { S.raf = 0; drawMask(); });
  }

  // ─── selector candidates ──────────────────────────────────────────────────
  const SKIP_CLASS = /^(is-|has-|js-|dusky)|^(active|selected|open|hover|focus|focused|visible|hidden|show|shown|collapsed|expanded|disabled|checked|current)$/;
  const HASHY = /^(css|sc|jsx|jss|svelte|emotion)-|^_[a-z0-9]+$|__[a-z0-9]{5,}$|^[^-_]*\d[^-_]*$/i;
  function goodClasses(n) {
    const all = [...n.classList].filter((c) => !SKIP_CLASS.test(c));
    const good = all.filter((c) => !HASHY.test(c));
    return (good.length ? good : all).slice(0, 3);
  }
  const simple = (n) => n.localName + goodClasses(n).map((c) => "." + CSS.escape(c)).join("");
  const describe = (n) => n.localName + (n.id ? "#" + n.id : "") + goodClasses(n).map((c) => "." + c).join("");
  const attrStr = (v) => '"' + v.replace(/["\\]/g, "\\$&") + '"';

  function pathSel(n) {
    const parts = [];
    for (let cur = n; cur && cur !== document.body && cur !== document.documentElement && parts.length < 3; cur = cur.parentElement) {
      if (cur.id) { parts.unshift("#" + CSS.escape(cur.id)); break; }
      let s = simple(cur);
      const siblings = cur.parentElement ? [...cur.parentElement.children] : [];
      if (siblings.some((c) => c !== cur && c.matches(s))) {
        s += ":nth-of-type(" + (siblings.filter((c) => c.localName === cur.localName).indexOf(cur) + 1) + ")";
      }
      parts.unshift(s);
    }
    return parts.join(" > ") || n.localName;
  }

  function candidates(n) {
    if (!n || n === document.documentElement) return [{ sel: "html", count: 1 }];
    if (n === document.body) return [{ sel: "body", count: 1 }];
    const out = [];
    if (n.id) out.push("#" + CSS.escape(n.id));
    const s = simple(n);
    if (s !== n.localName) out.push(s);
    for (const a of ["role", "aria-label", "data-testid", "name"]) {
      const v = n.getAttribute(a);
      if (v && v.length < 60) out.push(n.localName + "[" + a + "=" + attrStr(v) + "]");
    }
    out.push(pathSel(n), n.localName);
    const seen = new Set();
    return out.filter((sel) => !seen.has(sel) && seen.add(sel)).map((sel) => {
      let count = 0;
      try { count = document.querySelectorAll(sel).length; } catch (_) { /* unusual selector */ }
      return { sel, count };
    });
  }

  // ─── control bar ──────────────────────────────────────────────────────────
  function buildBar() {
    bar = el("section", { class: "panel bar", role: "toolbar", "aria-label": "Dusky picker" });
    bar.innerHTML = BAR_HTML;
    root.append(bar);
    drag(bar, bar);
    q("bundo").addEventListener("click", undo);
    q("bredo").addEventListener("click", redo);
    q("brules").addEventListener("click", toggleDrawer);
    q("bexit").addEventListener("click", () => setActive(false));
    if (S.note) setState("⚠ not saving: " + S.note, "err");
    else if (!paletteLoaded()) setState("⚠ palette variables are not loaded on this page — previews may look transparent", "warn");
    refreshBar();
  }
  function refreshBar() {
    if (!bar) return;
    const t = target();
    q("binfo").textContent = t
      ? "<" + describe(t) + ">" + (S.stack.length > 1 ? "  · depth " + S.depth + "/" + (S.stack.length - 1) : "")
      : "Hover an element · click to theme it · Shift+click hides it · Esc exits";
    q("bundo").disabled = !S.undo.length;
    q("bredo").disabled = !S.redo.length;
    q("brules").textContent = "Rules (" + S.rules.length + ")";
  }
  function setState(text, cls) {
    const s = q("bstate");
    if (s) { s.textContent = text; s.className = "state " + cls; }
  }

  // ─── element dialog ───────────────────────────────────────────────────────
  const selected = () => (dialog ? q("dsel").value : candidates(target())[0].sel);
  function outline(extra) {
    const sel = selected();
    setHover(sel + " { outline: 2px dashed #e6c280 !important; outline-offset: -2px !important; }" + (extra ? "\n" + sel + " { " + extra + " }" : ""));
  }

  function openDialog() {
    if (dialog) dialog.remove();
    dialog = el("section", { class: "panel dlg", role: "dialog", "aria-label": "Theme this element", tabindex: "-1" });
    dialog.innerHTML = DIALOG_HTML;
    if (S.dialogPos) Object.assign(dialog.style, { left: S.dialogPos.x + "px", top: S.dialogPos.y + "px", right: "auto" });
    root.append(dialog);
    drag(dialog, q("dhead"), (x, y) => { S.dialogPos = { x, y }; });

    q("dclose").addEventListener("click", closeDialog);
    q("dghost").addEventListener("click", (e) => {
      const on = dialog.classList.toggle("ghost");
      e.currentTarget.setAttribute("aria-pressed", String(on));
    });
    q("dslider").addEventListener("input", (e) => { S.depth = Number(e.target.value); retarget(); });
    q("dchild").addEventListener("click", () => step(-1));
    q("dparent").addEventListener("click", () => step(1));
    q("dsel").addEventListener("change", () => outline());
    q("dseg").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-group]");
      if (!b) return;
      S.group = b.dataset.group;
      for (const x of q("dseg").children) x.setAttribute("aria-pressed", String(x === b));
      q("dextra").textContent = GROUPS[S.group].extra[0];
    });

    const grid = q("dgrid");
    for (const [token, label] of TOKENS) {
      const b = el("button", { title: "var(--" + token + ")" },
        el("i", { class: "sw", style: { background: "var(--" + token + ", transparent)" } }),
        el("span", { text: label }));
      b.addEventListener("mouseenter", () => outline(declFor(S.group, token)));
      b.addEventListener("mouseleave", () => outline());
      b.addEventListener("click", () => apply(declFor(S.group, token), S.group + ": " + token));
      grid.append(b);
    }
    const extra = q("dextra");
    extra.textContent = GROUPS[S.group].extra[0];
    extra.addEventListener("mouseenter", () => outline(GROUPS[S.group].extra[1]));
    extra.addEventListener("mouseleave", () => outline());
    extra.addEventListener("click", () => apply(GROUPS[S.group].extra[1], GROUPS[S.group].extra[2]));
    const hide = q("dhide");
    hide.addEventListener("mouseenter", () => outline("display: none !important;"));
    hide.addEventListener("mouseleave", () => outline());
    hide.addEventListener("click", () => apply("display: none !important;", "hidden"));
    const custom = q("dcustom");
    const applyCustom = () => { const t = custom.value.trim(); if (t) apply(important(t), "custom"); };
    q("dapply").addEventListener("click", applyCustom);
    custom.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); applyCustom(); } });

    refreshDialog();
    dialog.focus({ preventScroll: true });
  }

  function refreshDialog() {
    const t = target();
    if (!dialog || !t) return;
    q("dtag").textContent = "<" + describe(t) + ">";
    const sl = q("dslider");
    sl.max = String(Math.max(0, S.stack.length - 1));
    sl.value = String(S.depth);
    q("dchild").disabled = S.depth === 0;
    q("dparent").disabled = S.depth >= S.stack.length - 1;
    const sel = q("dsel");
    sel.textContent = "";
    for (const c of candidates(t)) {
      sel.append(el("option", { value: c.sel, text: c.sel + "   — " + c.count + (c.count === 1 ? " match" : " matches") }));
    }
    outline();
  }

  function closeDialog() {
    if (dialog) dialog.remove();
    dialog = null;
    S.locked = false;
    setHover("");
  }

  function apply(decl, meta) {
    addRule(selected(), decl, meta);
    closeDialog();
  }

  // ─── rules drawer ─────────────────────────────────────────────────────────
  function toggleDrawer() {
    if (drawer) { drawer.remove(); drawer = null; return; }
    drawer = el("section", { class: "panel drawer", role: "dialog", "aria-label": "Rules for this site" });
    drawer.innerHTML = DRAWER_HTML;
    root.append(drawer);
    drag(drawer, q("rhead"));
    q("rclose").addEventListener("click", toggleDrawer);
    q("rclear").addEventListener("click", () => { if (S.rules.length) { snapshot(); S.rules = []; commit(); } });
    refreshDrawer();
  }
  function refreshDrawer() {
    if (!drawer) return;
    const list = q("rlist");
    list.textContent = "";
    if (!S.rules.length) {
      list.append(el("p", { class: "hint", text: "No rules yet — click any element on the page." }));
      return;
    }
    S.rules.forEach((r, i) => {
      const item = el("div", { class: "item" },
        el("span", { class: "sel", title: ruleLine(r), text: r.raw !== undefined ? r.raw : r.sel }),
        el("span", { class: "meta", text: r.raw !== undefined ? "manual" : r.meta }),
        el("button", { title: "Remove this rule", text: "✕", onclick: () => { snapshot(); S.rules.splice(i, 1); commit(); } }));
      if (r.raw === undefined) {
        item.addEventListener("mouseenter", () => setHover(r.sel + " { outline: 2px dashed #e6c280 !important; outline-offset: -2px !important; }"));
        item.addEventListener("mouseleave", () => (dialog ? outline() : setHover("")));
      }
      list.append(item);
    });
  }

  // ─── rule mutations, undo, persistence ────────────────────────────────────
  function snapshot() {
    S.undo.push(S.rules.slice());
    if (S.undo.length > 100) S.undo.shift();
    S.redo = [];
  }
  function addRule(sel, decl, meta) {
    const group = meta.split(":")[0];
    const i = group === "custom" ? -1 : S.rules.findIndex((r) => r.sel === sel && (r.meta || "").split(":")[0] === group);
    snapshot();
    const rule = { sel, decl, meta };
    if (i >= 0) S.rules[i] = rule; else S.rules.push(rule);
    commit();
  }
  function undo() { if (S.undo.length) { S.redo.push(S.rules); S.rules = S.undo.pop(); commit(); } }
  function redo() { if (S.redo.length) { S.undo.push(S.rules); S.rules = S.redo.pop(); commit(); } }
  function commit() { renderLive(); refreshBar(); refreshDrawer(); persist(); }

  async function persist() {
    const seq = ++S.saveSeq;
    setState("saving…", "");
    const body = S.rules.map((r) => "    " + ruleLine(r)).join("\n");
    const reply = await browser.runtime.sendMessage({ type: "splice", region: "picks", body })
      .catch((e) => ({ ok: false, error: String((e && e.message) || e) }));
    if (seq !== S.saveSeq) return;                                  // a newer save is in flight
    if (reply && reply.ok) setState("✓ saved " + String(reply.path).split("/").pop(), "ok");
    else setState("⚠ not saved: " + ((reply && reply.error) || "no reply"), "err");
  }

  async function hydrate() {
    if (S.hydrated) return;
    const reply = await browser.runtime.sendMessage({ type: "read" })
      .catch((e) => ({ ok: false, error: String((e && e.message) || e) }));
    if (reply && reply.ok) {
      S.hydrated = true;
      S.note = "";
      S.rules = String(reply.picks || "").split("\n").map((l) => l.trim()).filter(Boolean).map(parseRule);
    } else {
      S.note = (reply && reply.error) || "cannot reach the native host";
    }
  }

  // ─── page events (registered only while active) ───────────────────────────
  function setStack(elm) {
    const chain = [];
    for (let n = elm; n && n.nodeType === 1; n = n.parentElement) chain.push(n);
    S.stack = chain;
    S.depth = 0;
    retarget();
  }
  function retarget() { drawMask(); refreshBar(); refreshDialog(); }
  function step(delta) {
    if (!S.stack.length) return;
    S.depth = Math.min(Math.max(0, S.depth + delta), S.stack.length - 1);
    retarget();
  }
  function typing() {
    const a = root.activeElement || document.activeElement;
    return !!a && (a.isContentEditable || /^(input|select|textarea)$/i.test(a.tagName));
  }

  function onOver(e) { if (!S.locked && !isOurs(e)) setStack(e.target); }
  function onPointerDown(e) {
    if (isOurs(e)) return;
    e.preventDefault();                                             // no focus, drag or selection on the page
    e.stopImmediatePropagation();
  }
  function onClick(e) {
    if (isOurs(e)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (!S.stack.length) setStack(e.target);
    if (e.shiftKey) { addRule(candidates(target())[0].sel, "display: none !important;", "hidden"); return; }
    S.locked = true;
    openDialog();
  }
  function onKey(e) {
    const k = e.key, ctrl = e.ctrlKey || e.metaKey;
    if (k === "Escape") { if (dialog) closeDialog(); else if (drawer) toggleDrawer(); else setActive(false); }
    else if (typing()) return;
    else if (k === "ArrowUp" || k === "ArrowDown") { if (!S.stack.length) return; step(k === "ArrowUp" ? 1 : -1); }
    else if (ctrl && !e.altKey && k.toLowerCase() === "z") { if (e.shiftKey) redo(); else undo(); }
    else if (ctrl && !e.altKey && k.toLowerCase() === "y") redo();
    else return;
    e.preventDefault();
    e.stopImmediatePropagation();
  }
  const LISTENERS = [["mouseover", onOver], ["pointerdown", onPointerDown], ["click", onClick], ["keydown", onKey]];

  async function setActive(on) {
    if (on === S.active) return;
    S.active = on;
    if (on) {
      await hydrate();
      document.documentElement.append(host);
      buildBar();
      renderLive();
      for (const [type, fn] of LISTENERS) window.addEventListener(type, fn, true);
      window.addEventListener("scroll", scheduleMask, { capture: true, passive: true });
      window.addEventListener("resize", scheduleMask, { passive: true });
    } else {
      closeDialog();
      if (drawer) toggleDrawer();
      if (bar) bar.remove();
      bar = null;
      for (const [type, fn] of LISTENERS) window.removeEventListener(type, fn, true);
      window.removeEventListener("scroll", scheduleMask, { capture: true });
      window.removeEventListener("resize", scheduleMask);
      S.stack = [];
      S.depth = 0;
      S.locked = false;
      drawMask();
      host.remove();
    }
  }

  // ─── messages from popup / background ─────────────────────────────────────
  browser.runtime.onMessage.addListener((msg) => {
    switch (msg && msg.type) {
      case "ping":
        return Promise.resolve({ ok: true, active: S.active, rules: S.rules.length });
      case "scan":
        try { return Promise.resolve(scan()); }
        catch (e) { return Promise.resolve({ ok: false, error: "Scan failed: " + ((e && e.message) || e) }); }
      case "picker":
        return setActive(msg.enable === undefined ? !S.active : !!msg.enable).then(() => ({ ok: true, active: S.active }));
      case "reset":
        S.rules = []; S.undo = []; S.redo = []; S.hydrated = true; S.note = "";
        renderLive(); refreshBar(); refreshDrawer();
        return Promise.resolve({ ok: true });
      default:
        return undefined;
    }
  });
})();
