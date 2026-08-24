/* =============================================================================
 * Dusky Sites — Background Engine v5.1
 * Firefox 153+ / Gecko / WebExtension MV3 non-persistent event page.
 *
 * ARCHITECTURE
 *   [dusky_sites_host.py] <--stdio/native--> [PortManager] --> [State]
 *                                                 |             |
 *                                    [ThemeEngine]|             |[CssFactory]
 *                                 browser.theme.update()        |
 *                                                               v
 *                                                         [Broadcaster]
 *                                                    tabs.sendMessage(all frames)
 *
 * INVARIANTS
 *   I1  Every top-level listener is registered SYNCHRONOUSLY during script eval.
 *       An MV3 event page is respawned by the event itself; late registration
 *       loses the wake-up that spawned us.
 *   I2  All mutable runtime state is rehydratable from storage.session; the page
 *       may be torn down between any two ticks.
 *   I3  Exactly one native port may exist. Listener closures are gated on a
 *       monotonic generation counter, so dead closures from a superseded port
 *       are inert by construction and can never null a live port.
 *   I4  browser.theme.update() is called only when the serialized payload hash
 *       changes. Redundant calls repaint every window and cause tab-strip flicker.
 *   I5  Per-tab delivery is latest-wins through a single coalescing slot, so an
 *       APPLY can never be reordered behind a ROLLBACK.
 * ===========================================================================*/

'use strict';

(function duskySitesBackground() {
    'use strict';

    /* ─────────────────────────────────────────────────────────────────────
     * 0. Tunables
     * ────────────────────────────────────────────────────────────────── */
    const APP = 'Dusky Sites';
    const NATIVE_APP = 'dusky_sites';
    const WIRE_VERSION = 2;

    const T = {
        RECONNECT_BASE_MS: 1500,     // first retry delay
        RECONNECT_MAX_MS: 120000,    // ceiling (2 min) - host install is a human-scale event
        HANDSHAKE_MS: 5000,          // port considered dead if the host says nothing
        RPC_MS: 6000,                // per-request timeout
        IDLE_PING_MS: 90000,         // half-open pipe detector
        PING_GRACE_MS: 10000,
        THEME_DEBOUNCE_MS: 60,       // coalesce matugen write-bursts before theme.update()
        TAB_DEBOUNCE_MS: 24,         // ~1.5 frames: coalesce tab churn without visible lag
        CHUNK_TTL_MS: 15000
    };

    const LIMITS = {
        OUTBOX: 64,                  // queued host frames while disconnected
        DOMAIN_CACHE: 256,
        DOMAIN_TTL_MS: 600000,       // positive domain-fix entries
        DOMAIN_NEG_TTL_MS: 60000,    // negative entries: retry within a minute (B4)
        CSS_CACHE: 128,
        PAINT_CACHE: 48,             // storage.local first-paint entries
        PAINT_ENTRY_BYTES: 40960,
        CHUNK_BYTES: 8388608,
        VAR_COUNT: 640,
        VAR_VALUE_LEN: 160,
        SITE_CSS_BYTES: 524288
    };

    const ALARM_WATCHDOG = 'dusky.watchdog';
    const K_CONFIG = 'config';
    const K_THEME = 'themeData';
    const K_PAINT = 'paintCache';

    /* ─────────────────────────────────────────────────────────────────────
     * 1. Logging — gated so a production profile stays console-clean.
     * ────────────────────────────────────────────────────────────────── */
    let DEBUG = false;
    const log = (...a) => { if (DEBUG) console.log('[' + APP + ']', ...a); };
    const warn = (...a) => { if (DEBUG) console.warn('[' + APP + ']', ...a); };
    const fail = (...a) => console.error('[' + APP + ']', ...a);

    /** runtime.sendMessage / tabs.sendMessage reject when nobody listens.
     *  That is the normal case (no popup open, no content script in a PDF frame)
     *  and must never reach the console or it drowns real diagnostics. */
    const NO_RECEIVER = /Receiving end does not exist|Could not establish connection|message port closed/i;
    const swallow = (e) => { if (e && !NO_RECEIVER.test(String(e && e.message || e))) warn(e); };

    /* ─────────────────────────────────────────────────────────────────────
     * 2. Primitives
     * ────────────────────────────────────────────────────────────────── */

    /** FNV-1a 32-bit. Cheap, allocation-free content fingerprint used for every
     *  dedupe decision (theme payload, per-tab CSS, config writes). */
    function hash32(str) {
        let h = 0x811c9dc5;
        for (let i = 0; i < str.length; i++) {
            h ^= str.charCodeAt(i);
            h = Math.imul(h, 0x01000193) >>> 0;
        }
        return h.toString(36);
    }

    /** Insertion-ordered LRU on top of Map. Bounded => no unbounded heap growth
     *  in a long-lived browser session. */
    class Lru {
        constructor(max) { this.max = max; this.m = new Map(); }
        get(k) {
            if (!this.m.has(k)) return undefined;
            const v = this.m.get(k);
            this.m.delete(k); this.m.set(k, v);
            return v;
        }
        set(k, v) {
            if (this.m.has(k)) this.m.delete(k);
            this.m.set(k, v);
            if (this.m.size > this.max) this.m.delete(this.m.keys().next().value);
            return this;
        }
        delete(k) { return this.m.delete(k); }
        clear() { this.m.clear(); }
        get size() { return this.m.size; }
    }

    const nowMs = () => Date.now();

    /* ─────────────────────────────────────────────────────────────────────
     * 3. Colour engine
     *    Pure arithmetic. No DOM, no getComputedStyle, no forced reflow.
     *    Matugen emits #rrggbb, #rrggbbaa, rgb() and occasionally hsl();
     *    browser.theme.update() rejects the ENTIRE payload on one bad token,
     *    so everything is normalised to #rrggbb before it reaches the API.
     * ────────────────────────────────────────────────────────────────── */
    const NAMED_COLORS = {
        transparent: [0, 0, 0, 0], black: [0, 0, 0, 1], white: [255, 255, 255, 1],
        red: [255, 0, 0, 1], lime: [0, 255, 0, 1], blue: [0, 0, 255, 1],
        yellow: [255, 255, 0, 1], cyan: [0, 255, 255, 1], magenta: [255, 0, 255, 1],
        silver: [192, 192, 192, 1], gray: [128, 128, 128, 1], grey: [128, 128, 128, 1],
        maroon: [128, 0, 0, 1], olive: [128, 128, 0, 1], green: [0, 128, 0, 1],
        purple: [128, 0, 128, 1], teal: [0, 128, 128, 1], navy: [0, 0, 128, 1]
    };

    const clamp = (n, lo, hi) => (n < lo ? lo : n > hi ? hi : n);
    const to255 = (n) => clamp(Math.round(n), 0, 255);

    function num(token, scale) {
        if (typeof token !== 'string') return NaN;
        if (token.endsWith('%')) {
            const p = parseFloat(token);
            return Number.isFinite(p) ? (p / 100) * scale : NaN;
        }
        const v = parseFloat(token);
        return Number.isFinite(v) ? v : NaN;
    }

    function hueDeg(token) {
        const v = parseFloat(token);
        if (!Number.isFinite(v)) return NaN;
        if (token.endsWith('turn')) return v * 360;
        if (token.endsWith('rad')) return v * 180 / Math.PI;
        if (token.endsWith('grad')) return v * 0.9;
        return v;
    }

    function hslToRgb(h, s, l) {
        h = ((h % 360) + 360) % 360 / 360;
        s = clamp(s, 0, 1); l = clamp(l, 0, 1);
        if (s === 0) { const g = to255(l * 255); return [g, g, g]; }
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        const ch = (t) => {
            if (t < 0) t += 1; if (t > 1) t -= 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        };
        return [to255(ch(h + 1 / 3) * 255), to255(ch(h) * 255), to255(ch(h - 1 / 3) * 255)];
    }

    /** @returns {{r:number,g:number,b:number,a:number}|null} */
    function parseColor(raw) {
        if (typeof raw !== 'string') return null;
        const s = raw.trim().toLowerCase();
        if (!s || s.length > 64) return null;

        if (s.charCodeAt(0) === 35) {
            const hex = s.slice(1);
            if (!/^[0-9a-f]+$/.test(hex)) return null;
            const x = (i) => parseInt(hex[i] + hex[i], 16);
            const y = (i) => parseInt(hex.slice(i, i + 2), 16);
            if (hex.length === 3) return { r: x(0), g: x(1), b: x(2), a: 1 };
            if (hex.length === 4) return { r: x(0), g: x(1), b: x(2), a: x(3) / 255 };
            if (hex.length === 6) return { r: y(0), g: y(2), b: y(4), a: 1 };
            if (hex.length === 8) return { r: y(0), g: y(2), b: y(4), a: y(6) / 255 };
            return null;
        }

        const fn = s.match(/^(rgba?|hsla?)\s*\(([^()]*)\)$/);
        if (fn) {
            const parts = fn[2].replace(/\//g, ' ').split(/[\s,]+/).filter(Boolean);
            if (parts.length < 3) return null;
            const a = parts.length > 3 ? clamp(num(parts[3], 1), 0, 1) : 1;
            if (!Number.isFinite(a)) return null;
            if (fn[1].startsWith('rgb')) {
                const r = num(parts[0], 255), g = num(parts[1], 255), b = num(parts[2], 255);
                if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b)) return null;
                return { r: to255(r), g: to255(g), b: to255(b), a };
            }
            const h = hueDeg(parts[0]), sa = num(parts[1], 1), li = num(parts[2], 1);
            if (!Number.isFinite(h) || !Number.isFinite(sa) || !Number.isFinite(li)) return null;
            const rgb = hslToRgb(h, sa, li);
            return { r: rgb[0], g: rgb[1], b: rgb[2], a };
        }

        if (Object.prototype.hasOwnProperty.call(NAMED_COLORS, s)) {
            const n = NAMED_COLORS[s];
            return { r: n[0], g: n[1], b: n[2], a: n[3] };
        }
        return null; // color-mix(), lab(), oklch(), var() ... unusable for theme.update()
    }

    const hex2 = (n) => n.toString(16).padStart(2, '0');
    const toHex = (c) => '#' + hex2(c.r) + hex2(c.g) + hex2(c.b);

    /** Composite an alpha colour over an opaque backdrop. Semi-transparent chrome
     *  tokens make Gecko render the frame with a see-through artefact during
     *  window drag; we flatten instead. */
    function flatten(c, bg) {
        if (c.a >= 0.999) return c;
        const b = bg || { r: 0, g: 0, b: 0, a: 1 };
        return {
            r: to255(c.r * c.a + b.r * (1 - c.a)),
            g: to255(c.g * c.a + b.g * (1 - c.a)),
            b: to255(c.b * c.a + b.b * (1 - c.a)),
            a: 1
        };
    }

    const srgbToLinear = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };

    /** CIE L* (0..100). Material You defines "is this tone light" at L* > 50,
     *  which is perceptually correct where a 0.299/0.587/0.114 YIQ
     *  approximation mislabels saturated mid-tones. */
    function lstar(c) {
        const y = 0.2126 * srgbToLinear(c.r) + 0.7152 * srgbToLinear(c.g) + 0.0722 * srgbToLinear(c.b);
        return y <= 216 / 24389 ? y * 24389 / 27 : Math.cbrt(y) * 116 - 16;
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 4. Configuration schema
     *    Typed, clamped, allow-listed. Unknown keys are DROPPED so a buggy host
     *    or a stale popup cannot bloat storage or poison the prototype chain.
     * ────────────────────────────────────────────────────────────────── */
    const BUILTIN_DEFAULT_CONFIG = {
        colorsPath: '~/.config/matugen/generated/dusky_sites.css',
        websitesDir: '~/.config/dusky_sites',
        ecoMode: true,
        browserThemeEnabled: true,
        webThemeEnabled: false,
        forceUnthemedWebsites: false,
        userChromeEnabled: true,
        userContentEnabled: true,
        fontSize: 13,
        fastPaint: true,               // pre-paint from cache at document_start
        contentColorScheme: 'dark',    // auto | light | dark | system
        watchdogMinutes: 0.5,          // event-page revival cadence
        debug: false,                  // console gate
        paletteTemplate: {
            background: '--background',
            backgroundLight: '--surface',
            backgroundExtra: '--surface_container',
            accentPrimary: '--primary',
            accentSecondary: '--secondary',
            text: '--on_background',
            textFocus: '--on_surface'
        },
        browserTemplate: {
            frame: 'background',
            frame_inactive: 'background',
            tab_text: 'textFocus',
            tab_background_text: 'text',
            tab_selected: 'backgroundLight',
            tab_line: 'accentPrimary',
            tab_loading: 'accentPrimary',
            tab_background_separator: 'backgroundExtra',
            toolbar: 'backgroundLight',
            toolbar_text: 'textFocus',
            toolbar_field: 'backgroundExtra',
            toolbar_field_text: 'textFocus',
            toolbar_field_border: 'backgroundExtra',
            toolbar_field_focus: 'backgroundLight',
            toolbar_field_text_focus: 'textFocus',
            toolbar_field_border_focus: 'accentPrimary',
            toolbar_field_highlight: 'accentPrimary',
            toolbar_field_highlight_text: 'background',
            icons: 'text',
            icons_attention: 'accentPrimary',
            sidebar: 'backgroundLight',
            sidebar_text: 'textFocus',
            sidebar_border: 'backgroundExtra',
            sidebar_highlight: 'accentPrimary',
            sidebar_highlight_text: 'background',
            popup: 'backgroundLight',
            popup_text: 'textFocus',
            popup_border: 'backgroundExtra',
            popup_highlight: 'accentPrimary',
            popup_highlight_text: 'background',
            ntp_background: 'background',
            ntp_card_background: 'backgroundLight',
            ntp_text: 'text',
            bookmark_text: 'textFocus',
            toolbar_top_separator: 'backgroundExtra',
            toolbar_bottom_separator: 'backgroundExtra',
            button_background_hover: 'backgroundExtra',
            button_background_active: 'backgroundExtra'
        }
    };

    /** Every colour key Gecko accepts. Anything else makes theme.update() reject
     *  with a schema error and the WHOLE theme is dropped - the single most
     *  common cause of "my theme randomly stopped applying". */
    const VALID_THEME_KEYS = new Set([
        'bookmark_text', 'button_background_active', 'button_background_hover', 'frame',
        'frame_inactive', 'icons', 'icons_attention', 'ntp_background', 'ntp_card_background',
        'ntp_text', 'popup', 'popup_border', 'popup_highlight', 'popup_highlight_text',
        'popup_text', 'sidebar', 'sidebar_border', 'sidebar_highlight', 'sidebar_highlight_text',
        'sidebar_text', 'tab_background_separator', 'tab_background_text', 'tab_line',
        'tab_loading', 'tab_selected', 'tab_text', 'toolbar', 'toolbar_bottom_separator',
        'toolbar_field', 'toolbar_field_border', 'toolbar_field_border_focus',
        'toolbar_field_focus', 'toolbar_field_highlight', 'toolbar_field_highlight_text',
        'toolbar_field_text', 'toolbar_field_text_focus', 'toolbar_text',
        'toolbar_top_separator', 'toolbar_vertical_separator'
    ]);

    /** Structural surfaces must be opaque or the window compositor shows seams. */
    const OPAQUE_THEME_KEYS = new Set([
        'frame', 'frame_inactive', 'toolbar', 'toolbar_field', 'toolbar_field_focus',
        'popup', 'sidebar', 'ntp_background', 'ntp_card_background', 'tab_selected'
    ]);

    const BOOL_KEYS = ['ecoMode', 'browserThemeEnabled', 'webThemeEnabled', 'forceUnthemedWebsites',
        'userChromeEnabled', 'userContentEnabled', 'fastPaint', 'debug'];
    const PATH_KEYS = ['colorsPath', 'websitesDir'];
    const SCHEMES = new Set(['auto', 'light', 'dark', 'system']);
    const SAFE_KEY = /^[A-Za-z_][A-Za-z0-9_]*$/;
    const CSS_VAR_KEY = /^--[A-Za-z0-9_-]+$/;

    function sanitizeTemplate(src, validator) {
        const out = {};
        if (!src || typeof src !== 'object') return out;
        for (const k of Object.keys(src)) {
            if (k === '__proto__' || k === 'constructor' || k === 'prototype') continue;
            const v = src[k];
            if (typeof v !== 'string') continue;
            if (validator(k, v)) out[k] = v;
        }
        return out;
    }

    /** Deep, allow-listed merge. base <- overrides. */
    function mergeConfig(base, updates) {
        const out = { ...base };
        if (!updates || typeof updates !== 'object') return out;

        for (const k of BOOL_KEYS) if (typeof updates[k] === 'boolean') out[k] = updates[k];
        for (const k of PATH_KEYS) {
            const v = updates[k];
            if (typeof v === 'string' && v.length > 0 && v.length < 4096 && !v.includes('\u0000')) out[k] = v.trim();
        }
        if (updates.fontSize !== undefined) {
            const n = Number(updates.fontSize);
            if (Number.isFinite(n)) out.fontSize = clamp(Math.round(n), 6, 48);
        }
        if (typeof updates.contentColorScheme === 'string' && SCHEMES.has(updates.contentColorScheme)) {
            out.contentColorScheme = updates.contentColorScheme;
        }
        if (updates.watchdogMinutes !== undefined) {
            const n = Number(updates.watchdogMinutes);
            if (Number.isFinite(n)) out.watchdogMinutes = clamp(n, 0.25, 10);
        }
        if (updates.paletteTemplate) {
            out.paletteTemplate = {
                ...base.paletteTemplate,
                ...sanitizeTemplate(updates.paletteTemplate, (k, v) => SAFE_KEY.test(k) && CSS_VAR_KEY.test(v))
            };
        }
        if (updates.browserTemplate) {
            out.browserTemplate = {
                ...base.browserTemplate,
                ...sanitizeTemplate(updates.browserTemplate, (k, v) => VALID_THEME_KEYS.has(k) && SAFE_KEY.test(v))
            };
        }
        return out;
    }

    /** defaults.js may or may not exist / may or may not define USER_CONFIG.
     *  Read it defensively: a ReferenceError here would abort the whole script. */
    function readUserDefaults() {
        try {
            if (typeof globalThis.USER_CONFIG === 'object' && globalThis.USER_CONFIG) return globalThis.USER_CONFIG;
        } catch (_) { /* not defined */ }
        return null;
    }

    const DEFAULT_CONFIG = mergeConfig(BUILTIN_DEFAULT_CONFIG, readUserDefaults());

    /* ─────────────────────────────────────────────────────────────────────
     * 5. State + rehydration (invariant I2)
     * ────────────────────────────────────────────────────────────────── */
    const state = {
        config: { ...DEFAULT_CONFIG },
        theme: null,        // { colors, websites, disabledSites, timestamp }
        rev: '0',           // fingerprint of the active palette+rules revision
        enabled: true,      // user "stop" switch
        port: null,
        portReady: false,
        connectedAt: 0,
        lastRxAt: 0,
        attempt: 0,
        nextAttemptAt: 0,
        retryTimer: null,
        pingTimer: null,
        handshakeTimer: null,   // B2: tracked so success/teardown can cancel it
        lastError: null,
        appliedThemeHash: null,
        isApplied: false
    };

    /** B1: generation counter. Incremented on every connect() AND every
     *  teardown() of the live port; every listener closure captures its own
     *  generation and no-ops the instant it is stale. */
    let portGen = 0;

    const cssCache = new Lru(LIMITS.CSS_CACHE);       // rev|host|flags -> {css,hash,scan}
    const domainCache = new Lru(LIMITS.DOMAIN_CACHE); // host -> {css,isDarkSite,neg,at}
    const domainInflight = new Map();                 // host -> Promise
    const tabSlots = new Map();                       // tabId -> {timer,payload}

    let bootPromise = null;
    /** Single-flight bootstrap awaited by every entry point. */
    function ready() {
        if (!bootPromise) bootPromise = boot().catch((e) => { fail('boot failed', e); });
        return bootPromise;
    }

    async function boot() {
        const [local, session] = await Promise.all([
            browser.storage.local.get([K_CONFIG, K_THEME]).catch(() => ({})),
            browser.storage.session.get(['theme', 'rev', 'enabled']).catch(() => ({}))
        ]);

        state.config = mergeConfig(DEFAULT_CONFIG, local[K_CONFIG]);
        DEBUG = !!state.config.debug;

        // storage.session survives event-page suspension but not a browser restart;
        // storage.local is the cold-start fallback so the first paint after a
        // restart does not have to wait for the daemon.
        const t = session.theme || local[K_THEME] || null;
        if (t && t.colors) {
            adoptTheme(t, false);
            if (state.config.browserThemeEnabled) queueBrowserTheme();
        }
        if (typeof session.enabled === 'boolean') state.enabled = session.enabled;

        await ensureWatchdog();
        if (state.enabled) connect();
    }

    function persistVolatile() {
        browser.storage.session.set({
            theme: state.theme, rev: state.rev, enabled: state.enabled
        }).catch(swallow);
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 6. URL helpers
     * ────────────────────────────────────────────────────────────────── */
    const BLOCKED_PROTOCOLS = new Set([
        'about:', 'chrome:', 'resource:', 'moz-extension:', 'view-source:',
        'data:', 'blob:', 'javascript:', 'file:'
    ]);
    /** Gecko refuses content-script injection on these regardless of permissions. */
    const RESTRICTED_HOSTS = new Set([
        'addons.mozilla.org', 'accounts.firefox.com', 'support.mozilla.org',
        'discovery.addons.mozilla.org', 'install.mozilla.org'
    ]);

    function urlOf(u) { try { return new URL(u); } catch (_) { return null; } }

    function isInjectable(url) {
        if (!url) return false;
        const p = urlOf(url);
        if (!p) return false;
        if (BLOCKED_PROTOCOLS.has(p.protocol)) return false;
        if (RESTRICTED_HOSTS.has(p.hostname)) return false;
        return p.protocol === 'http:' || p.protocol === 'https:';   // B5: ftp: is gone from Gecko
    }

    function hostOf(url) { const p = urlOf(url); return p ? p.hostname.toLowerCase() : ''; }

    /**
     * Domain matcher with an explicit specificity score so ordering is
     * deterministic.
     *   exact host .............. 1000 + len
     *   registrable suffix ...... 500 + len
     *   single-label heuristic .. len
     */
    function matchScore(hostname, pattern, allowSingleLabel) {
        const h = hostname;
        let d = String(pattern || '').toLowerCase().trim();
        if (!h || !d) return 0;
        if (d.startsWith('*.')) d = d.slice(2);
        if (d.startsWith('.')) d = d.slice(1);
        if (!d) return 0;
        if (h === d) return 1000 + d.length;
        if (h.endsWith('.' + d)) return 500 + d.length;
        if (allowSingleLabel && !d.includes('.')) {
            const parts = h.split('.').filter(Boolean);
            if (parts.length >= 2 && parts.slice(0, -1).includes(d)) return d.length;
        }
        return 0;
    }

    function isSiteDisabled(hostname, disabled) {
        if (!hostname || !Array.isArray(disabled) || !disabled.length) return false;
        for (const d of disabled) if (matchScore(hostname, d, true) > 0) return true;
        return false;
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 7. Native port — connection, backoff, outbox, RPC, chunk reassembly
     * ────────────────────────────────────────────────────────────────── */
    const outbox = [];            // frames queued while the pipe is down
    const rpc = new Map();        // key -> {resolve,reject,timer}
    const chunks = new Map();     // id -> {parts,total,bytes,at}
    let ridSeq = 0;

    function connect() {
        if (!state.enabled || state.port) return;
        clearTimer('retryTimer');

        let port;
        try {
            port = browser.runtime.connectNative(NATIVE_APP);
        } catch (e) {
            // Thrown synchronously when the native manifest is absent/malformed.
            state.lastError = String(e && e.message || e);
            fail('connectNative threw:', state.lastError);
            scheduleReconnect();
            return;
        }

        // B1: this port's generation. Every closure below is gated on it, so a
        // listener firing after teardown/reconnect is a guaranteed no-op.
        const gen = ++portGen;
        const live = () => portGen === gen && state.port === port;

        state.port = port;
        state.portReady = false;
        state.connectedAt = nowMs();
        state.lastRxAt = nowMs();

        port.onMessage.addListener((m) => { if (live()) onHostMessage(m); });
        port.onDisconnect.addListener(() => { if (live()) onHostDisconnect(port); });

        // Handshake: a native manifest can exist while the interpreter is broken.
        // The pipe then stays "open" and every send is silently voided, so we
        // demand proof of life before reporting connected:true to the UI.
        // B2: tracked timer - cancelled on first RX and in teardown().
        state.handshakeTimer = setTimeout(() => {
            state.handshakeTimer = null;
            if (live() && !state.portReady) {
                warn('handshake timeout');
                state.lastError = 'handshake timeout';
                teardown(port);
                scheduleReconnect();
            }
        }, T.HANDSHAKE_MS);

        // HELLO is additive: legacy hosts ignore unknown types, and SET_CONFIG /
        // FETCH_NOW below are the wire-compatible proof-of-life anyway.
        rawSend({ type: 'HELLO', wire: WIRE_VERSION, extension: browser.runtime.id });
        rawSend({ type: 'SET_CONFIG', config: state.config });
        rawSend({ type: 'FETCH_NOW' });
        flushOutbox();
        armIdlePing();
        log('port opened (gen', gen, ')');
    }

    function teardown(port) {
        if (!port) return;
        try { port.disconnect(); } catch (_) { }
        if (state.port === port) {
            state.port = null;
            state.portReady = false;
            portGen++;                       // B1: invalidate every closure of this port
            clearTimer('pingTimer');
            clearTimer('handshakeTimer');    // B2
            rejectAllRpc('port closed');
            chunks.clear();
        }
    }

    function onHostDisconnect(port) {
        const err = (port.error && port.error.message) ||
            (browser.runtime.lastError && browser.runtime.lastError.message) ||
            'host closed the pipe';
        state.lastError = err;
        const lived = nowMs() - state.connectedAt;
        teardown(port);
        notifyUI({ type: 'HOST_STATUS', connected: false, error: err, manuallyStopped: !state.enabled });
        // A port that lived >30 s was healthy: treat this as a host restart and
        // retry immediately instead of inheriting the previous backoff ladder.
        if (lived > 30000) state.attempt = 0;
        if (state.enabled) scheduleReconnect();
    }

    /** Decorrelated-jitter backoff. Pure exponential makes every window of every
     *  profile retry in lockstep and hammers systemd-spawned hosts. */
    function scheduleReconnect() {
        clearTimer('retryTimer');
        if (!state.enabled) return;
        const exp = Math.min(T.RECONNECT_BASE_MS * Math.pow(2, state.attempt), T.RECONNECT_MAX_MS);
        const delay = Math.round(exp / 2 + Math.random() * exp / 2);
        state.attempt = Math.min(state.attempt + 1, 24);
        state.nextAttemptAt = nowMs() + delay;
        state.retryTimer = setTimeout(() => { state.retryTimer = null; connect(); }, delay);
        log('reconnect in', delay, 'ms (attempt', state.attempt, ')');
    }

    function clearTimer(name) {
        if (state[name]) { clearTimeout(state[name]); state[name] = null; }
    }

    /** Half-open detector: a wedged python process keeps the pipe nominally open
     *  while never reading. Nothing in the WebExtension API surfaces that, so we
     *  probe it ourselves. */
    function armIdlePing() {
        clearTimer('pingTimer');
        state.pingTimer = setTimeout(() => {
            state.pingTimer = null;
            if (!state.port) return;
            if (nowMs() - state.lastRxAt < T.IDLE_PING_MS) { armIdlePing(); return; }
            const port = state.port;
            const gen = portGen;
            rawSend({ type: 'PING', at: nowMs() });
            setTimeout(() => {
                if (portGen !== gen || state.port !== port) return;   // B1 gate
                if (nowMs() - state.lastRxAt > T.IDLE_PING_MS + T.PING_GRACE_MS) {
                    warn('native host unresponsive - recycling port');
                    state.lastError = 'host unresponsive';
                    teardown(port);
                    state.attempt = 0;
                    scheduleReconnect();
                } else {
                    armIdlePing();
                }
            }, T.PING_GRACE_MS);
        }, T.IDLE_PING_MS);
    }

    function rawSend(frame) {
        const port = state.port;
        if (!port) return false;
        try {
            port.postMessage(frame);
            return true;
        } catch (e) {
            // Serialization failure or dead pipe. Both are fatal for this port.
            warn('postMessage failed:', e);
            state.lastError = String(e && e.message || e);
            teardown(port);
            scheduleReconnect();
            return false;
        }
    }

    /** Fire-and-forget with an outbox. Idempotent types collapse so a 10-minute
     *  outage cannot replay 400 stale FETCH_NOW frames on reconnect. */
    const COLLAPSIBLE = new Set(['SET_CONFIG', 'FETCH_NOW', 'LIVE_THEME_RESPONSE', 'PING', 'HELLO']);
    function send(frame) {
        if (state.port && state.portReady) return rawSend(frame);
        if (COLLAPSIBLE.has(frame.type)) {
            for (let i = outbox.length - 1; i >= 0; i--) if (outbox[i].type === frame.type) outbox.splice(i, 1);
        }
        outbox.push(frame);
        while (outbox.length > LIMITS.OUTBOX) outbox.shift();
        return false;
    }

    function flushOutbox() {
        if (!state.port) return;
        const pending = outbox.splice(0, outbox.length);
        for (const f of pending) if (!rawSend(f)) break;
    }

    /**
     * Request/response over a stream-only transport.
     * Correlation is dual-mode:
     *   - modern host echoes {rid}  -> O(1) resolve, concurrency-safe
     *   - legacy host does not      -> resolve by (responseType, matchKey)
     * so this ships against the unmodified dusky_sites_host.py.
     */
    function request(frame, responseType, matchKey) {
        const rid = ++ridSeq;
        const keyA = 'rid:' + rid;
        const keyB = responseType + ':' + (matchKey === undefined ? '' : matchKey);
        const existing = rpc.get(keyB);
        if (existing) return existing.promise; // in-flight dedupe

        let resolveFn, rejectFn;
        const promise = new Promise((res, rej) => { resolveFn = res; rejectFn = rej; });
        const entry = {
            promise,
            resolve: (v) => { cleanup(); resolveFn(v); },
            reject: (e) => { cleanup(); rejectFn(e); },
            timer: setTimeout(() => entry.reject(new Error('rpc timeout ' + frame.type)), T.RPC_MS)
        };
        function cleanup() { clearTimeout(entry.timer); rpc.delete(keyA); rpc.delete(keyB); }
        rpc.set(keyA, entry);
        rpc.set(keyB, entry);
        send({ ...frame, rid });
        return promise;
    }

    function resolveRpc(msg) {
        if (msg.rid !== undefined) {
            const e = rpc.get('rid:' + msg.rid);
            if (e) { e.resolve(msg); return true; }
        }
        const key = msg.type + ':' + (msg.domain !== undefined ? String(msg.domain).toLowerCase() : '');
        const e2 = rpc.get(key);
        if (e2) { e2.resolve(msg); return true; }
        return false;
    }

    function rejectAllRpc(reason) {
        const seen = new Set();
        for (const e of rpc.values()) { if (seen.has(e)) continue; seen.add(e); e.reject(new Error(reason)); }
        rpc.clear();
    }

    /**
     * Gecko caps a single app->extension native message at 1 MB. A large
     * websites/ directory blows straight through that and the host is forced to
     * split. Reassemble {type:'CHUNK', id, seq, total, part} here.
     */
    function reassemble(msg) {
        const id = String(msg.id || '');
        if (!id) return null;
        let slot = chunks.get(id);
        const t = nowMs();
        if (!slot) { slot = { parts: [], total: msg.total | 0, bytes: 0, at: t }; chunks.set(id, slot); }
        for (const [k, v] of chunks) if (t - v.at > T.CHUNK_TTL_MS) chunks.delete(k);
        slot.parts[msg.seq | 0] = String(msg.part || '');
        slot.bytes += (msg.part || '').length;
        if (slot.bytes > LIMITS.CHUNK_BYTES) { chunks.delete(id); fail('chunk stream exceeded cap'); return null; }
        const have = slot.parts.filter((p) => typeof p === 'string').length;
        if (have < slot.total) return null;
        chunks.delete(id);
        try { return JSON.parse(slot.parts.join('')); } catch (e) { fail('chunk JSON parse failed', e); return null; }
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 8. Inbound host protocol
     * ────────────────────────────────────────────────────────────────── */
    async function onHostMessage(raw) {
        state.lastRxAt = nowMs();
        if (!state.portReady) {
            state.portReady = true;
            state.attempt = 0;
            state.lastError = null;
            clearTimer('handshakeTimer');   // B2: proof of life arrived
            flushOutbox();
            notifyUI({ type: 'HOST_STATUS', connected: true });
            log('handshake complete');
        }
        if (!raw || typeof raw !== 'object') return;

        let msg = raw;
        if (msg.type === 'CHUNK') { msg = reassemble(msg); if (!msg) return; }
        if (resolveRpc(msg)) return;
        await ready();

        switch (msg.type) {
            case 'MATUGEN_UPDATE': return onPaletteUpdate(msg.data);
            case 'DOMAIN_FIX_RESPONSE': return cacheDomainFix(msg);
            case 'STORED_CONFIG': return onStoredConfig(msg.config);
            case 'QUERY_LIVE_THEME': return replyLiveTheme();
            case 'PING': send({ type: 'PONG', at: nowMs() }); return;
            case 'PONG': return;
            case 'HELLO_ACK': log('host wire', msg.wire); return;
            case 'SAVE_CONFIG_SUCCESS': return;
            default: notifyUI({ type: 'HOST_RESPONSE', data: msg });
        }
    }

    async function onPaletteUpdate(data) {
        if (!data || !data.colors || typeof data.colors !== 'object') return;

        // The host is authoritative for these two switches. B3: persist locally
        // WITHOUT pushing back - echoing SET_CONFIG + FETCH_NOW to the daemon
        // after every palette burst is a pointless round-trip (and a livelock
        // hazard if a future host revs the timestamp on every FETCH_NOW).
        const patch = {};
        if (typeof data.webThemeEnabled === 'boolean') patch.webThemeEnabled = data.webThemeEnabled;
        if (typeof data.forceUnthemedWebsites === 'boolean') patch.forceUnthemedWebsites = data.forceUnthemedWebsites;
        const cfgChanged = Object.keys(patch).some((k) => state.config[k] !== patch[k]);
        if (cfgChanged) { state.config = mergeConfig(state.config, patch); writeConfig(false); }

        const themeAdopted = adoptTheme(data, true);
        if (!themeAdopted && state.isApplied) { log('palette unchanged - suppressed'); return; }

        if (state.config.browserThemeEnabled) queueBrowserTheme();
        if (state.config.webThemeEnabled) broadcast(); else broadcastRollback();
        notifyUI({ type: 'THEME_APPLIED', colors: state.theme.colors, rev: state.rev });
    }

    /** @returns true when the revision actually changed. */
    function adoptTheme(data, persist) {
        const rev = hash32(JSON.stringify([data.colors, data.websites || null, data.disabledSites || null]));
        if (rev === state.rev && state.theme) return false;
        state.theme = Object.freeze({
            colors: data.colors,
            websites: data.websites || {},
            disabledSites: Array.isArray(data.disabledSites) ? data.disabledSites : [],
            timestamp: data.timestamp || nowMs(),
            status: data.status
        });
        state.rev = rev;
        cssCache.clear();               // every derived artefact is now stale
        if (persist) {
            persistVolatile();
            // storage.local is the cold-start seed only; write it lazily and never
            // on the hot path of a rapid matugen burst.
            browser.storage.local.set({ [K_THEME]: state.theme }).catch(swallow);
        }
        return true;
    }

    function onStoredConfig(cfg) {
        if (!cfg) return;
        const before = JSON.stringify(state.config);
        state.config = mergeConfig(state.config, cfg);
        DEBUG = !!state.config.debug;
        if (JSON.stringify(state.config) === before) return;
        writeConfig();
        notifyUI({ type: 'CONFIG_RECOVERED', config: state.config });
    }

    function cacheDomainFix(msg) {
        if (!msg.domain) return;
        const host = String(msg.domain).toLowerCase();
        domainCache.set(host, {
            css: typeof msg.css === 'string' ? msg.css.slice(0, LIMITS.SITE_CSS_BYTES) : '',
            isDarkSite: !!msg.isDarkSite,
            neg: false,
            at: nowMs()
        });
        refreshTabsForHost(host);
    }

    async function replyLiveTheme() {
        try {
            const cur = await browser.theme.getCurrent();
            send({ type: 'LIVE_THEME_RESPONSE', theme: cur });
        } catch (e) { warn('theme.getCurrent failed', e); }
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 9. Browser chrome theming (browser.theme)
     * ────────────────────────────────────────────────────────────────── */
    let themeTimer = null;

    /** Trailing-edge debounce: matugen rewrites its output file in several
     *  syscalls and the host may emit 3-5 updates inside ~40 ms. Each raw
     *  theme.update() repaints every window (visible tab-strip flicker). */
    function queueBrowserTheme() {
        if (themeTimer) clearTimeout(themeTimer);
        themeTimer = setTimeout(() => { themeTimer = null; applyBrowserTheme(); }, T.THEME_DEBOUNCE_MS);
    }

    function buildPalette(colors) {
        const tmpl = state.config.paletteTemplate || DEFAULT_CONFIG.paletteTemplate;
        const p = {};
        for (const role of Object.keys(tmpl)) p[role] = colors ? (colors[tmpl[role]] || null) : null;
        return p;
    }

    function buildThemeColors(colors) {
        const palette = buildPalette(colors);
        const tmpl = state.config.browserTemplate || DEFAULT_CONFIG.browserTemplate;
        const bgRaw = parseColor(palette.background) || { r: 24, g: 26, b: 27, a: 1 };
        const backdrop = flatten(bgRaw, { r: 0, g: 0, b: 0, a: 1 });
        const out = {};
        for (const key of Object.keys(tmpl)) {
            if (!VALID_THEME_KEYS.has(key)) continue;            // schema guard
            const parsed = parseColor(palette[tmpl[key]]);
            if (!parsed) continue;                                 // drop, never send garbage
            out[key] = toHex(OPAQUE_THEME_KEYS.has(key) ? flatten(parsed, backdrop) : parsed);
        }
        return { colors: out, backdrop };
    }

    async function applyBrowserTheme() {
        if (!state.config.browserThemeEnabled) return;
        const colors = state.theme && state.theme.colors;
        if (!colors) return;

        const built = buildThemeColors(colors);
        const c = built.colors;

        // Gecko hard-requires these two; without them theme.update() rejects and
        // the previous theme is left in place with no diagnostic.
        if (!c.frame) c.frame = toHex(built.backdrop);
        if (!c.tab_background_text) c.tab_background_text = lstar(built.backdrop) > 50 ? '#15141a' : '#fbfbfe';

        const dark = lstar(parseColor(c.frame) || built.backdrop) <= 50;
        const payload = {
            colors: c,
            properties: {
                color_scheme: dark ? 'dark' : 'light',
                content_color_scheme: state.config.contentColorScheme === 'auto'
                    ? (dark ? 'dark' : 'light')
                    : state.config.contentColorScheme
            }
        };

        const h = hash32(JSON.stringify(payload));
        if (h === state.appliedThemeHash) return;   // invariant I4

        try {
            await browser.theme.update(payload);
            state.appliedThemeHash = h;
            state.isApplied = true;
        } catch (e) {
            // Graceful degradation: retry with the minimal legal theme so the
            // chrome is never stranded on a stale palette.
            fail('theme.update rejected:', e);
            try {
                await browser.theme.update({
                    colors: { frame: c.frame, tab_background_text: c.tab_background_text },
                    properties: payload.properties
                });
                state.appliedThemeHash = null;
                state.isApplied = true;
            } catch (e2) { fail('minimal theme.update also rejected:', e2); state.isApplied = false; }
        }
    }

    async function resetBrowserTheme() {
        state.appliedThemeHash = null;
        state.isApplied = false;
        try { await browser.theme.reset(); } catch (e) { warn(e); }
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 10. CSS factory
     *     All string building happens ONCE per (revision, host) and is cached.
     *     The content script receives a finished stylesheet + hash and does
     *     nothing but replaceSync() - zero page-thread serialization cost.
     * ────────────────────────────────────────────────────────────────── */
    const UNSAFE_CSS_VALUE = /url\s*\(|image-set\s*\(|-moz-binding|expression\s*\(|@import|<\/|\/\*|\*\/|\\|;|\{|\}/i;

    function buildRootCss(colors) {
        const lines = [':root {'];
        let n = 0;
        for (const key of Object.keys(colors)) {
            if (!CSS_VAR_KEY.test(key)) continue;
            const v = colors[key];
            if (typeof v !== 'string') continue;
            const val = v.trim();
            if (!val || val.length > LIMITS.VAR_VALUE_LEN) continue;
            if (UNSAFE_CSS_VALUE.test(val)) continue;
            lines.push('  ' + key + ': ' + val + ' !important;');
            if (++n >= LIMITS.VAR_COUNT) break;
        }
        lines.push('}');
        return lines.join('\n') + '\n';
    }

    const FALLBACK_CSS = [
        '@media screen {',
        '  /* Dusky structural engine for sites that ship no dark theme. */',
        '  :root { color-scheme: dark !important; }',
        '  html, body,',
        '  header, nav, main, footer, aside, section, article,',
        '  form, table, thead, tbody, tfoot, tr, td, th, ul, ol, li, dl, dt, dd,',
        '  details, summary, figure, fieldset, legend,',
        '  [class*="card"], [class*="header"], [class*="footer"],',
        '  [class*="sidebar"], [class*="panel"], [class*="box"] {',
        '    background-color: var(--background, var(--surface, #181a1b)) !important;',
        '    color: var(--on_background, var(--on_surface, #e0e0e0)) !important;',
        '    border-color: var(--outline_variant, rgba(255,255,255,.08)) !important;',
        '  }',
        '  [class*="overlay"], [class*="backdrop"], [class*="off-canvas"],',
        '  [class*="dialog-off-canvas"], [class*="canvas"], [class*="wrapper"],',
        '  [id*="wrapper"], [class*="screenshot"] { background-color: transparent !important; }',
        '  h1, h2, h3, h4, h5, h6, p, li, dt, dd, label, b, strong, i, em, small, mark, blockquote {',
        '    color: var(--on_background, var(--on_surface, inherit)) !important;',
        '  }',
        '  a:link, a:link *, [role="link"] { color: var(--primary, #8ab4f8) !important; }',
        '  a:visited, a:visited * { color: var(--tertiary, #c58af9) !important; }',
        '  a:hover, a:hover * { color: var(--primary, #8ab4f8) !important; text-decoration: underline; }',
        '  pre, code, kbd, samp {',
        '    background-color: var(--surface_container_high, var(--surface, #2b2a33)) !important;',
        '    color: var(--on_surface, inherit) !important; border-radius: 4px;',
        '  }',
        '  button, select, textarea, option, optgroup,',
        '  [role="button"], [role="combobox"], [role="option"], [role="listbox"] {',
        '    background-color: var(--surface_container, var(--surface, #2b2a33)) !important;',
        '    color: var(--on_surface, #fbfbfe) !important;',
        '    border-color: var(--outline, rgba(255,255,255,.12)) !important;',
        '  }',
        '  [class*="search"] input, form input, [role="combobox"] input,',
        '  input[type="text"], input[type="search"] {',
        '    background-color: transparent !important;',
        '    color: var(--on_surface, #e0e0e0) !important; box-shadow: none !important;',
        '  }',
        '  input[type="checkbox"], input[type="radio"], input[type="range"], progress {',
        '    accent-color: var(--primary_container, #8ab4f8) !important;',
        '  }',
        '  input::placeholder, textarea::placeholder {',
        '    color: var(--on_surface_variant, rgba(255,255,255,.5)) !important;',
        '  }',
        '  table th {',
        '    background-color: var(--surface_container_high, var(--surface, #2b2a33)) !important;',
        '    color: var(--on_surface, #fbfbfe) !important;',
        '  }',
        '  tbody tr:nth-child(even) {',
        '    background-color: var(--surface_container_low, var(--surface, #1e1d27)) !important;',
        '  }',
        '  img, video, canvas, iframe, embed, object, svg { background-color: transparent !important; }',
        '  ::backdrop { background-color: rgba(0,0,0,.7) !important; }',
        '  hr { border-color: var(--outline_variant, rgba(255,255,255,.12)) !important; }',
        '  ::selection {',
        '    background-color: var(--primary_container, var(--primary, #364765)) !important;',
        '    color: var(--on_primary_container, var(--on_primary, #fff)) !important;',
        '  }',
        '  /* scrollbar-color inherits: setting it on the root avoids a universal',
        '     selector match across every element of a 50k-node app. */',
        '  html { scrollbar-color: var(--outline, #42414d) var(--surface, #1c1b22); }',
        '}',
        ''
    ].join('\n');

    /** Author-declared per-site rules, concatenated LEAST specific first so the
     *  natural cascade resolves subdomain overrides without extra machinery. */
    function siteRulesFor(hostname, websites) {
        if (!hostname || !websites) return '';
        const hits = [];
        for (const key of Object.keys(websites)) {
            const score = matchScore(hostname, key, true);
            if (score > 0 && typeof websites[key] === 'string') hits.push({ key, score, css: websites[key] });
        }
        if (!hits.length) return '';
        hits.sort((a, b) => a.score - b.score || (a.key < b.key ? -1 : 1));
        let out = '';
        for (const h of hits) out += '/* dusky:' + h.key + ' */\n' + h.css.slice(0, LIMITS.SITE_CSS_BYTES) + '\n';
        return out;
    }

    /**
     * Resolve the complete stylesheet for one hostname.
     * @returns {{css:string,hash:string,scan:boolean}|null} null => roll back.
     */
    function resolveCss(hostname) {
        if (!state.theme || !hostname) return null;
        if (isSiteDisabled(hostname, state.theme.disabledSites)) return null;

        const forced = !!state.config.forceUnthemedWebsites;
        const cacheKey = state.rev + '|' + hostname + '|' + (forced ? '1' : '0');
        const hit = cssCache.get(cacheKey);
        if (hit !== undefined) return hit;

        const site = siteRulesFor(hostname, state.theme.websites);
        let body = '';
        let scan = false;

        if (site) {
            body = site;
        } else if (forced) {
            const fix = domainCache.get(hostname);
            // B4: negative entries (RPC timeout / recycled port) expire fast so a
            // healthy reconnect repopulates the authoritative fix within a minute.
            const ttl = fix && fix.neg ? LIMITS.DOMAIN_NEG_TTL_MS : LIMITS.DOMAIN_TTL_MS;
            const fresh = fix && (nowMs() - fix.at) < ttl;
            if (!fresh) {
                requestDomainFix(hostname);          // async; result re-broadcasts
                body = FALLBACK_CSS;
                scan = true;
                // Deliberately NOT cached: the authoritative answer lands in <6 s.
                const provisional = pack(state.theme.colors, body, scan);
                return provisional;
            }
            if (fix.isDarkSite) return null;         // site already dark - hands off
            body = fix.css ? FALLBACK_CSS + '\n' + fix.css : FALLBACK_CSS;
            scan = true;
        } else {
            return null;                              // no template, forcing off
        }

        const packed = pack(state.theme.colors, body, scan);
        cssCache.set(cacheKey, packed);
        return packed;
    }

    function pack(colors, body, scan) {
        const css = buildRootCss(colors) + body;
        return { css, hash: hash32(css), scan, rev: state.rev };
    }

    function requestDomainFix(hostname) {
        if (domainInflight.has(hostname)) return domainInflight.get(hostname);
        const p = request({ type: 'GET_DOMAIN_FIX', domain: hostname }, 'DOMAIN_FIX_RESPONSE', hostname)
            .then((msg) => { cacheDomainFix(msg); })
            .catch(() => {
                // Negative-cache with the SHORT TTL (B4) so a dead/legacy host is
                // not re-asked on every tab activation, yet recovery is fast.
                domainCache.set(hostname, { css: '', isDarkSite: false, neg: true, at: nowMs() });
            })
            .finally(() => { domainInflight.delete(hostname); });
        domainInflight.set(hostname, p);
        return p;
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 11. Broadcaster — one coalescing slot per tab (invariant I5)
     * ────────────────────────────────────────────────────────────────── */
    function queueTab(tabId, payload) {
        const slot = tabSlots.get(tabId);
        // Latest-wins into the SAME window: the in-flight timer reads entry.payload
        // at fire time, so we must not clear/re-arm it (that would strand the send)
        // and ordering stays total for this tab.
        if (slot) { slot.payload = payload; return; }
        const entry = { payload: payload, timer: 0 };
        entry.timer = setTimeout(() => {
            tabSlots.delete(tabId);
            browser.tabs.sendMessage(tabId, entry.payload).catch(swallow);
        }, T.TAB_DEBOUNCE_MS);
        tabSlots.set(tabId, entry);
    }

    function pushTab(tabId, url) {
        if (!isInjectable(url)) return;
        const host = hostOf(url);
        if (!state.config.webThemeEnabled || !state.enabled) {
            queueTab(tabId, { type: 'MATUGEN_ROLLBACK' });
            evictPaint(host);
            return;
        }
        const res = resolveCss(host);
        if (!res) {
            // Site is disabled / already dark / has no rules: the first-paint entry
            // must die with it or the next cold load flashes a stale stylesheet.
            queueTab(tabId, { type: 'MATUGEN_ROLLBACK' });
            evictPaint(host);
            return;
        }
        queueTab(tabId, { type: 'MATUGEN_UPDATE', data: res });
        cachePaint(host, res);
    }

    async function broadcast() {
        if (!state.theme) return;
        let tabs;
        try { tabs = await browser.tabs.query({}); } catch (e) { warn(e); return; }

        if (state.config.ecoMode) {
            // Only the focused tab of each window; the rest are refreshed lazily
            // by onActivated / onUpdated. Saves N-1 style recalcs on a 200-tab window.
            const perWindow = new Map();
            for (const t of tabs) if (t.active && !t.discarded) perWindow.set(t.windowId, t);
            for (const t of perWindow.values()) pushTab(t.id, t.url);
            return;
        }
        for (const t of tabs) if (!t.discarded) pushTab(t.id, t.url);
    }

    async function broadcastRollback() {
        let tabs;
        try { tabs = await browser.tabs.query({}); } catch (e) { warn(e); return; }
        for (const t of tabs) if (!t.discarded && isInjectable(t.url)) queueTab(t.id, { type: 'MATUGEN_ROLLBACK' });
        browser.storage.local.remove(K_PAINT).catch(swallow);
    }

    async function refreshTabsForHost(host) {
        cssCache.clear();
        let tabs;
        try { tabs = await browser.tabs.query({}); } catch (_) { return; }
        for (const t of tabs) {
            if (t.discarded || !t.url) continue;
            if (matchScore(hostOf(t.url), host, false) > 0) pushTab(t.id, t.url);
        }
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 12. First-paint cache
     *     content.js reads storage.local directly at document_start - that is a
     *     single parent-process IPC that does NOT require the event page to be
     *     resurrected, so the stylesheet lands before the first paint instead of
     *     after a background wake-up (which is what produces the white flash).
     * ────────────────────────────────────────────────────────────────── */
    let paintWriteTimer = null;
    const paintPending = new Map();

    function cachePaint(host, res) {
        if (!state.config.fastPaint || !host) return;
        if (res.css.length > LIMITS.PAINT_ENTRY_BYTES) return;
        paintPending.set(host, { h: res.hash, rev: res.rev, scan: res.scan, css: res.css, at: nowMs() });
        if (paintWriteTimer) return;
        paintWriteTimer = setTimeout(flushPaintCache, 750);
    }

    /** null tombstone: flushPaintCache() deletes the key instead of writing it. */
    function evictPaint(host) {
        if (!host) return;
        paintPending.set(host, null);
        if (!paintWriteTimer) paintWriteTimer = setTimeout(flushPaintCache, 750);
    }

    async function flushPaintCache() {
        paintWriteTimer = null;
        if (!paintPending.size) return;
        const batch = new Map(paintPending);
        paintPending.clear();
        try {
            const cur = (await browser.storage.local.get(K_PAINT))[K_PAINT] || {};
            let dirty = false;
            for (const [host, entry] of batch) {
                if (entry === null) { if (cur[host]) { delete cur[host]; dirty = true; } continue; }
                if (cur[host] && cur[host].h === entry.h) continue;
                cur[host] = entry; dirty = true;
            }
            if (!dirty) return;
            for (const host of Object.keys(cur)) if (cur[host].rev !== state.rev) delete cur[host];
            const keys = Object.keys(cur);
            if (keys.length > LIMITS.PAINT_CACHE) {
                keys.sort((a, b) => (cur[a].at || 0) - (cur[b].at || 0));
                for (const k of keys.slice(0, keys.length - LIMITS.PAINT_CACHE)) delete cur[k];
            }
            await browser.storage.local.set({ [K_PAINT]: cur });
        } catch (e) { swallow(e); }
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 13. Config persistence — serialized, coalesced, change-gated
     * ────────────────────────────────────────────────────────────────── */
    let writeChain = Promise.resolve();
    let lastConfigHash = null;

    function writeConfig(push) {
        const snapshot = JSON.parse(JSON.stringify(state.config));
        const h = hash32(JSON.stringify(snapshot));
        if (h === lastConfigHash && !push) return writeChain;
        lastConfigHash = h;
        writeChain = writeChain
            .then(() => browser.storage.local.set({ [K_CONFIG]: snapshot }))
            .then(() => {
                if (push !== false) { send({ type: 'SET_CONFIG', config: snapshot }); send({ type: 'FETCH_NOW' }); }
            })
            .catch((e) => fail('config write failed', e));
        return writeChain;
    }

    function notifyUI(msg) { browser.runtime.sendMessage(msg).catch(swallow); }

    function statusSnapshot() {
        return {
            connected: !!state.port && state.portReady,
            manuallyStopped: !state.enabled,
            lastSyncTime: state.theme ? state.theme.timestamp : null,
            isApplied: state.isApplied,
            lastError: state.lastError,
            nextAttemptIn: state.port ? 0 : Math.max(0, state.nextAttemptAt - nowMs()),
            rev: state.rev,
            wire: WIRE_VERSION
        };
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 14. Runtime message router
     * ────────────────────────────────────────────────────────────────── */
    const EXT_ORIGIN = browser.runtime.getURL('');

    /** Privileged commands (they make the daemon write to the profile directory)
     *  must originate from an extension page. A substring check on runtime.id is
     *  satisfied by https://evil.example/?dusky_sites@dusky.com - a real
     *  privilege-escalation path. Prefix-match the moz-extension origin AND
     *  require sender.tab to be absent (popup/options context). */
    const isPrivileged = (sender) =>
        typeof sender.url === 'string' && sender.url.startsWith(EXT_ORIGIN) && !sender.tab;

    const PRIVILEGED = new Set(['GET_PROFILE_PATHS', 'WRITE_USER_CHROME', 'WRITE_USER_CONTENT', 'SET_FONT_SIZE', 'HOST_RAW']);

    browser.runtime.onMessage.addListener((req, sender) => {
        if (!req || typeof req.type !== 'string') return false;
        if (sender.id !== browser.runtime.id) return false;

        switch (req.type) {
            case 'GET_THEME_DATA':
                return ready().then(() => handleGetThemeData(sender));

            case 'GET_STATUS':
                return ready().then(statusSnapshot);

            case 'GET_PALETTE':
                return ready().then(() => {
                    const colors = state.theme ? state.theme.colors : null;
                    return { palette: buildPalette(colors), colors, rev: state.rev };
                });

            case 'GET_CONFIG':
                return ready().then(() => ({ config: state.config, defaults: DEFAULT_CONFIG }));

            case 'UPDATE_CONFIG':
                if (!isPrivileged(sender)) return Promise.resolve({ ok: false, error: 'unauthorized' });
                return ready().then(() => handleUpdateConfig(req.partialUpdate));

            case 'SET_ENABLED':
                if (!isPrivileged(sender)) return Promise.resolve({ ok: false, error: 'unauthorized' });
                return ready().then(() => setEnabled(!!req.enabled)).then(() => ({ ok: true, status: statusSnapshot() }));

            case 'RECONNECT_NOW':
                return ready().then(() => { state.attempt = 0; if (state.enabled && !state.port) connect(); return { ok: true }; });

            case 'FORCE_REFRESH':
                return ready().then(() => { cssCache.clear(); domainCache.clear(); broadcast(); queueBrowserTheme(); return { ok: true }; });

            default:
                if (PRIVILEGED.has(req.type)) {
                    if (!isPrivileged(sender)) return Promise.resolve({ ok: false, error: 'unauthorized' });
                    return ready().then(() => ({ ok: send(req) }));
                }
                return false;   // not ours: leave the channel open for other listeners
        }
    });

    function handleGetThemeData(sender) {
        const status = statusSnapshot();
        if (!state.enabled || !state.config.webThemeEnabled) return { data: null, status };
        // Resolve against the TOP-LEVEL document so a third-party iframe inherits
        // the rules of the page that embeds it (and honours its disable switch).
        const url = (sender.tab && sender.tab.url) || sender.url;
        if (!isInjectable(url)) return { data: null, status };
        const host = hostOf(url);
        const res = resolveCss(host);
        if (!res) return { data: null, status };
        cachePaint(host, res);
        return { data: res, status };
    }

    async function handleUpdateConfig(partial) {
        if (!partial || typeof partial !== 'object') return { ok: false, error: 'bad payload' };
        const before = state.config;
        state.config = mergeConfig(before, partial);
        DEBUG = !!state.config.debug;

        const chromeToggled = before.browserThemeEnabled !== state.config.browserThemeEnabled;
        const webToggled = before.webThemeEnabled !== state.config.webThemeEnabled;
        const forceToggled = before.forceUnthemedWebsites !== state.config.forceUnthemedWebsites;
        const tmplChanged = JSON.stringify(before.paletteTemplate) !== JSON.stringify(state.config.paletteTemplate) ||
            JSON.stringify(before.browserTemplate) !== JSON.stringify(state.config.browserTemplate);

        await writeConfig(true);

        if (chromeToggled || tmplChanged) {
            if (state.config.browserThemeEnabled) { state.appliedThemeHash = null; queueBrowserTheme(); }
            else await resetBrowserTheme();
        }
        if (webToggled || forceToggled || tmplChanged) {
            cssCache.clear();
            if (state.config.webThemeEnabled) broadcast(); else broadcastRollback();
        }
        if (before.watchdogMinutes !== state.config.watchdogMinutes) await ensureWatchdog(true);
        return { ok: true, config: state.config };
    }

    async function setEnabled(on) {
        if (state.enabled === on) return;
        state.enabled = on;
        persistVolatile();
        if (on) {
            state.attempt = 0;
            connect();
            if (state.config.browserThemeEnabled) queueBrowserTheme();
            if (state.config.webThemeEnabled) broadcast();
        } else {
            clearTimer('retryTimer');
            teardown(state.port);
            outbox.length = 0;
            await broadcastRollback();
            await resetBrowserTheme();
        }
        notifyUI({ type: 'HOST_STATUS', connected: false, manuallyStopped: !on });
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 15. Event wiring  (all synchronous - invariant I1)
     * ────────────────────────────────────────────────────────────────── */

    // tabs.onUpdated is filtered at the platform level: without this the event
    // page is resurrected for favicon/title/audible churn on every tab.
    browser.tabs.onUpdated.addListener(
        (tabId, change, tab) => {
            ready().then(() => {
                if (!state.theme) return;
                if (change.discarded) { dropTab(tabId); return; }
                if (change.status === 'complete' || typeof change.url === 'string') pushTab(tabId, tab.url);
            });
        },
        { properties: ['status', 'url', 'discarded'] }
    );

    browser.tabs.onActivated.addListener((info) => {
        ready().then(async () => {
            if (!state.config.ecoMode || !state.theme) return;
            try { const t = await browser.tabs.get(info.tabId); pushTab(t.id, t.url); } catch (_) { }
        });
    });

    browser.windows.onFocusChanged.addListener((windowId) => {
        if (windowId === browser.windows.WINDOW_ID_NONE) return;
        ready().then(async () => {
            if (!state.config.ecoMode || !state.theme) return;
            try {
                const tabs = await browser.tabs.query({ active: true, windowId });
                if (tabs[0]) pushTab(tabs[0].id, tabs[0].url);
            } catch (_) { }
        });
    });

    function dropTab(tabId) {
        const slot = tabSlots.get(tabId);
        if (slot) { clearTimeout(slot.timer); tabSlots.delete(tabId); }
    }
    browser.tabs.onRemoved.addListener(dropTab);

    /** Defensive event binding. Several MV3 events exist in the Chromium schema
     *  but not in Gecko (and vice versa); touching .addListener on an undefined
     *  event throws during script evaluation and would take the ENTIRE
     *  background script down. This is API-surface probing, not a legacy shim. */
    function on(ns, event, handler) {
        try {
            const e = ns && ns[event];
            if (e && typeof e.addListener === 'function') { e.addListener(handler); return true; }
        } catch (_) { }
        warn('event unavailable:', event);
        return false;
    }

    browser.action.onClicked.addListener(() => {
        ready().then(async () => {
            if (!state.enabled) { await setEnabled(true); return; }
            state.attempt = 0;
            if (!state.port) connect();
            cssCache.clear();
            broadcast();
            state.appliedThemeHash = null;
            queueBrowserTheme();
        });
    });

    on(browser.permissions, 'onAdded', () => ready().then(() => broadcast()));
    on(browser.permissions, 'onRemoved', () => ready().then(() => broadcast()));

    // Another add-on (or the user) swapped the active theme: re-assert ours on
    // the next tick, but only if we are the owner - never fight in a loop.
    on(browser.theme, 'onUpdated', (info) => {
        ready().then(() => {
            if (!state.config.browserThemeEnabled || !state.theme) return;
            const t = info && info.theme;
            const isEmpty = !t || !t.colors;
            if (isEmpty && state.isApplied) { state.appliedThemeHash = null; queueBrowserTheme(); }
        });
    });

    browser.alarms.onAlarm.addListener((alarm) => {
        if (alarm.name !== ALARM_WATCHDOG) return;
        ready().then(() => {
            // Durable revival path: setTimeout does not survive event-page
            // suspension, alarms do. This is what actually guarantees the daemon
            // link comes back after the page is unloaded.
            if (state.enabled && !state.port && nowMs() >= state.nextAttemptAt) connect();
            if (paintPending.size) flushPaintCache();
        });
    });

    async function ensureWatchdog(force) {
        try {
            const existing = await browser.alarms.get(ALARM_WATCHDOG);
            const period = state.config.watchdogMinutes || 0.5;
            if (existing && !force && existing.periodInMinutes === period) return;
            await browser.alarms.create(ALARM_WATCHDOG, { periodInMinutes: period, delayInMinutes: period });
        } catch (e) { warn('alarm setup failed', e); }
    }

    on(browser.runtime, 'onStartup', () => { ready(); });
    on(browser.runtime, 'onInstalled', () => { ready(); });
    on(browser.runtime, 'onSuspend', () => {
        persistVolatile();
        flushPaintCache();
        teardown(state.port);   // clean EOF so the daemon can free its side
    });

    browser.storage.onChanged.addListener((changes, area) => {
        if (area !== 'local' || !changes[K_CONFIG]) return;
        // An options page wrote the config directly: adopt it without a reload.
        ready().then(() => {
            const next = changes[K_CONFIG].newValue;
            if (!next || hash32(JSON.stringify(next)) === lastConfigHash) return;
            state.config = mergeConfig(DEFAULT_CONFIG, next);
            DEBUG = !!state.config.debug;
            cssCache.clear();
            state.appliedThemeHash = null;
            if (state.config.browserThemeEnabled) queueBrowserTheme(); else resetBrowserTheme();
            if (state.config.webThemeEnabled) broadcast(); else broadcastRollback();
        });
    });

    // Cold start of the event page itself.
    ready();
})();
