/* =============================================================================
 * Dusky Sites — Content Runtime v5.1
 * Firefox 153+ / Gecko / injected at document_start into every frame.
 *
 * RESPONSIBILITIES (deliberately minimal - the page main thread is not ours)
 *   1. Paint the last-known stylesheet before the first frame is composited.
 *   2. Adopt the authoritative stylesheet pushed by background.js.
 *   3. Keep it attached against hostile/rewriting DOMs, with a circuit breaker.
 *   4. Optionally derive per-site overrides from the page's own CSSOM - budgeted,
 *      idle-scheduled, and WITHOUT a single forced style recalculation.
 *
 * NON-GOALS
 *   No colour maths on the palette (background.js ships a finished stylesheet).
 *   No string building per update (we replaceSync one pre-hashed blob).
 * ===========================================================================*/

'use strict';

(function duskySitesContent() {
    'use strict';

    // Xray expando: visible to this extension's content scripts only, never to
    // the page. Guards against double injection (all_frames + SPA re-injection).
    if (window.__duskySitesV5) return;
    window.__duskySitesV5 = true;

    const XHTML_NS = 'http://www.w3.org/1999/xhtml';
    const STYLE_ID = 'dusky-sites-theme';
    const PAINT_KEY = 'paintCache';
    const IS_TOP = (function () { try { return window.top === window; } catch (_) { return false; } })();

    /* ─────────────────────────────────────────────────────────────────────
     * 0. State
     * ────────────────────────────────────────────────────────────────── */
    let baseCss = '';        // authoritative sheet from background.js
    let derivedCss = '';     // CSSOM-derived per-site overrides (scan mode)
    let appliedHash = null;  // hash of what is actually attached
    let sheet = null;        // CSSStyleSheet (constructed) when adoption works
    let styleEl = null;      // <style> fallback
    let observer = null;
    let observedHead = null; // the <head> node the observer is currently bound to
    let scanState = null;
    let useAdopted = null;   // tri-state: null = not probed yet
    let disposed = false;

    /* ─────────────────────────────────────────────────────────────────────
     * 1. Primitives
     * ────────────────────────────────────────────────────────────────── */
    function hash32(str) {
        let h = 0x811c9dc5;
        for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
        return h.toString(36);
    }

    const idle = (fn, timeout) =>
        (typeof requestIdleCallback === 'function')
            ? requestIdleCallback(fn, { timeout: timeout || 2000 })
            : setTimeout(() => fn({ timeRemaining: () => 8, didTimeout: true }), 32);

    /* ─────────────────────────────────────────────────────────────────────
     * 2. Stylesheet transport
     *
     *    Constructed stylesheets are preferred: they are invisible to the page's
     *    DOM (so React/Vue reconcilers and "remove unknown <style>" guards can
     *    never strip them), they need no MutationObserver, and replaceSync on an
     *    already-adopted sheet is a single style-set invalidation instead of an
     *    element insertion + full stylesheet reparse.
     *
     *    Adoption from a content-script sandbox is feature-probed once at
     *    runtime rather than assumed from an 'adoptedStyleSheets' in Document
     *    check, because the constructor lives in a different global here.
     * ────────────────────────────────────────────────────────────────── */
    function probeAdopted() {
        if (useAdopted !== null) return useAdopted;
        useAdopted = false;
        try {
            if (!('adoptedStyleSheets' in Document.prototype)) return useAdopted;
            const probe = new CSSStyleSheet();
            probe.replaceSync(':root{--dusky-probe:1}');
            const before = document.adoptedStyleSheets || [];
            document.adoptedStyleSheets = [...before, probe];
            const ok = (document.adoptedStyleSheets || []).includes(probe);
            document.adoptedStyleSheets = (document.adoptedStyleSheets || []).filter((s) => s !== probe);
            useAdopted = ok;
        } catch (_) { useAdopted = false; }
        return useAdopted;
    }

    function attachAdopted(css) {
        try {
            if (!sheet) sheet = new CSSStyleSheet();
            sheet.replaceSync(css);
            const list = document.adoptedStyleSheets || [];
            if (!list.includes(sheet)) document.adoptedStyleSheets = [...list, sheet];
            return true;
        } catch (e) { return false; }
    }

    function attachElement(css) {
        // createElementNS: in an XML/SVG document createElement() produces an
        // element in the document's namespace, which is NOT an HTML <style> and
        // is therefore inert. Namespacing explicitly keeps XHTML/SVG documents working.
        if (!styleEl || !styleEl.isConnected) {
            styleEl = document.createElementNS(XHTML_NS, 'style');
            styleEl.id = STYLE_ID;
            styleEl.setAttribute('type', 'text/css');
        }
        if (styleEl.textContent !== css) styleEl.textContent = css;
        const host = document.head || document.documentElement;
        if (!host) return false;
        // Last child of head => wins every author-origin tie at equal specificity.
        if (styleEl.parentNode !== host || styleEl !== host.lastChild) host.appendChild(styleEl);
        startObserver();
        return true;
    }

    function render() {
        if (disposed) return;
        if (!baseCss) return;
        const css = derivedCss ? baseCss + '\n' + derivedCss : baseCss;
        const h = hash32(css);
        if (h === appliedHash && isAttached()) return;   // idempotent
        appliedHash = h;
        if (probeAdopted() && attachAdopted(css)) { stopObserver(); return; }
        attachElement(css);
    }

    function isAttached() {
        if (useAdopted && sheet) { try { return (document.adoptedStyleSheets || []).includes(sheet); } catch (_) { return false; } }
        return !!(styleEl && styleEl.isConnected);
    }

    function clearTheme() {
        stopObserver();
        cancelScan();
        appliedHash = null;
        baseCss = '';
        derivedCss = '';
        if (sheet) {
            try { document.adoptedStyleSheets = (document.adoptedStyleSheets || []).filter((s) => s !== sheet); } catch (_) { }
            sheet = null;
        }
        if (styleEl) { try { styleEl.remove(); } catch (_) { } styleEl = null; }
        // Belt and braces: an earlier build's node may still be in the DOM.
        try {
            const stale = document.querySelectorAll('#' + STYLE_ID + ', #mf-theme');
            for (const n of stale) n.remove();
        } catch (_) { }
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 3. Re-attachment guard (only needed on the <style> path)
     *
     *    v5.1: childList-only, multi-target. subtree observation of the whole
     *    document made Gecko queue a mutation record batch for EVERY DOM change
     *    on the page; on virtual-DOM apps that is thousands of no-op callback
     *    entries per second. Only two things can hurt us and both are direct
     *    childList events:
     *      - our <style> is removed from its parent  -> <head> observer fires
     *      - the whole <head> is swapped out          -> <html> observer fires
     *    Bounded: a page that fights us wins after MAX_FIGHTS instead of
     *    burning a core in an infinite mutation loop.
     * ────────────────────────────────────────────────────────────────── */
    const MAX_FIGHTS = 24;
    let fights = 0;
    let fightWindow = 0;
    let repairScheduled = false;

    function startObserver() {
        if (useAdopted || disposed) return;
        if (!observer) observer = new MutationObserver(onDomMutated);
        else observer.disconnect();
        // <html> childList catches <head> insertion/replacement; NOT subtree.
        if (document.documentElement) observer.observe(document.documentElement, { childList: true });
        observedHead = document.head || null;
        if (observedHead) observer.observe(observedHead, { childList: true });
    }

    function onDomMutated() {
        if (repairScheduled || disposed || !styleEl) return;
        const headNow = document.head || null;
        const headSwapped = headNow !== observedHead;
        const detached = !styleEl.isConnected;
        // Migrate into <head> the moment it materialises (style may have been
        // parked on documentElement at document_start on streamed documents).
        const misplaced = !!(headNow && styleEl.parentNode !== headNow);
        if (!headSwapped && !detached && !misplaced) return;
        repairScheduled = true;
        // Coalesce to one repair per frame; mutation callbacks fire in bursts.
        requestAnimationFrame(() => {
            repairScheduled = false;
            if (disposed || !styleEl) return;
            const now = Date.now();
            if (now - fightWindow > 5000) { fightWindow = now; fights = 0; }
            if (++fights > MAX_FIGHTS) { stopObserver(); return; }
            const host = document.head || document.documentElement;
            if (host && (!styleEl.isConnected || styleEl.parentNode !== host)) host.appendChild(styleEl);
            startObserver();   // re-arm against the (possibly brand-new) head
        });
    }

    function stopObserver() {
        if (observer) { observer.disconnect(); observer = null; }
        observedHead = null;
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 4. CSSOM-derived overrides (scan mode)
     *
     *    Pure arithmetic colour parsing, no DOM node, no computed style,
     *    time-sliced against the idle deadline, hard-capped, and each
     *    stylesheet is visited at most once (WeakSet) so SPA re-scans are
     *    incremental. Zero forced layout flushes by construction: nothing in
     *    this section reads a layout- or style-dependent property.
     * ────────────────────────────────────────────────────────────────── */
    const NAMED = { white: 255, black: 0, silver: 192, gray: 128, grey: 128 };
    const CAPS = { sheets: 60, rules: 6000, out: 120000, depth: 6, slice: 6 };
    const SKIP_SEL = /(^|[\s,>+~])(input|textarea|select)|\[type=|search|::(before|after|placeholder|selection|backdrop)|:root|(^|,)\s*html\b/i;

    function quickLuma(v) {
        // Returns 0..1 perceived luminance, or -1 when the token is not a static
        // colour (var(), gradients, currentColor, keywords we must not guess at).
        if (typeof v !== 'string') return -1;
        const s = v.trim().toLowerCase();
        if (!s || s.length > 48) return -1;
        if (s.charCodeAt(0) === 35) {
            const x = s.slice(1);
            if (!/^[0-9a-f]{3,8}$/.test(x)) return -1;
            let r, g, b;
            if (x.length === 3 || x.length === 4) {
                r = parseInt(x[0] + x[0], 16); g = parseInt(x[1] + x[1], 16); b = parseInt(x[2] + x[2], 16);
                if (x.length === 4 && parseInt(x[3] + x[3], 16) < 26) return -1;
            } else if (x.length === 6 || x.length === 8) {
                r = parseInt(x.slice(0, 2), 16); g = parseInt(x.slice(2, 4), 16); b = parseInt(x.slice(4, 6), 16);
                if (x.length === 8 && parseInt(x.slice(6, 8), 16) < 26) return -1;
            } else return -1;
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
        }
        const m = s.match(/^rgba?\s*\(([^()]*)\)$/);
        if (m) {
            const p = m[1].replace(/\//g, ' ').split(/[\s,]+/).filter(Boolean);
            if (p.length < 3) return -1;
            const conv = (t) => t.endsWith('%') ? parseFloat(t) * 2.55 : parseFloat(t);
            const r = conv(p[0]), g = conv(p[1]), b = conv(p[2]);
            if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b)) return -1;
            if (p.length > 3) {
                const a = p[3].endsWith('%') ? parseFloat(p[3]) / 100 : parseFloat(p[3]);
                if (Number.isFinite(a) && a < 0.1) return -1;     // effectively transparent
            }
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
        }
        const h = s.match(/^hsla?\s*\(([^()]*)\)$/);
        if (h) {
            const p = h[1].replace(/\//g, ' ').split(/[\s,]+/).filter(Boolean);
            const l = parseFloat(p[2]);
            return Number.isFinite(l) ? l / 100 : -1;              // L is a good enough proxy here
        }
        if (Object.prototype.hasOwnProperty.call(NAMED, s)) return NAMED[s] / 255;
        return -1;
    }

    function collectSheets() {
        const list = [];
        let sheets;
        try { sheets = document.styleSheets; } catch (_) { return list; }
        for (let i = 0; i < sheets.length && list.length < CAPS.sheets; i++) {
            const s = sheets[i];
            try {
                if (!s || s.disabled) continue;
                if (s.ownerNode && s.ownerNode.id === STYLE_ID) continue;   // never scan ourselves
                if (!s.cssRules) continue;                                   // throws on cross-origin
                if (scanState.seen.has(s)) continue;
                list.push(s);
            } catch (_) { /* SecurityError on opaque cross-origin sheet */ }
        }
        return list;
    }

    function walk(rules, depth, out, budget) {
        for (let i = 0; i < rules.length; i++) {
            if (budget.rules-- <= 0 || out.length > CAPS.out) return;
            const rule = rules[i];
            if (!rule) continue;

            // Duck-typing rather than CSSRule.type: the constants were frozen, so
            // every modern grouping rule (@layer, @container, @scope, @starting-style)
            // reports type 0. Anything exposing .cssRules is a grouping rule; anything
            // exposing .selectorText + .style is a style rule. CSS nesting means a
            // single rule can legitimately be both.
            const kids = rule.cssRules;
            if (kids && kids.length && depth < CAPS.depth && typeof rule.keyText !== 'string') {
                walk(kids, depth + 1, out, budget);
            }

            const sel = rule.selectorText;
            const st = rule.style;
            if (typeof sel !== 'string' || !st) continue;          // @font-face, @keyframes frames, @page ...
            if (!sel || sel.length > 400 || SKIP_SEL.test(sel)) continue;

            let body = '';
            const bg = quickLuma(st.backgroundColor);
            if (bg > 0.45) body += 'background-color:var(--background,var(--surface,#181a1b))!important;';
            const fg = quickLuma(st.color);
            if (fg >= 0 && fg < 0.5) body += 'color:var(--on_background,var(--on_surface,#e0e0e0))!important;';
            const bc = quickLuma(st.borderColor);
            if (bc > 0.6) body += 'border-color:var(--outline_variant,rgba(255,255,255,.10))!important;';
            if (body) out.push(sel + '{' + body + '}');
        }
    }

    function cancelScan() {
        if (scanState && scanState.handle) {
            if (typeof cancelIdleCallback === 'function') { try { cancelIdleCallback(scanState.handle); } catch (_) { } }
            clearTimeout(scanState.handle);
        }
        scanState = null;
    }

    function scheduleScan(reason) {
        if (disposed) return;
        if (!scanState) scanState = { seen: new WeakSet(), out: [], handle: 0, runs: 0 };
        if (scanState.handle) return;
        if (scanState.runs > 8) return;                     // SPA guard: bounded total work
        scanState.handle = idle((deadline) => {
            scanState.handle = 0;
            scanState.runs++;
            runScanSlice(deadline);
        }, reason === 'load' ? 1500 : 4000);
    }

    function runScanSlice(deadline) {
        if (disposed || !scanState) return;
        const sheets = collectSheets();
        if (!sheets.length) return;
        const budget = { rules: CAPS.rules };
        const start = performance.now();
        for (const s of sheets) {
            scanState.seen.add(s);
            try { walk(s.cssRules, 0, scanState.out, budget); } catch (_) { }
            const spent = performance.now() - start;
            const left = deadline && deadline.timeRemaining ? deadline.timeRemaining() : 0;
            if (budget.rules <= 0 || spent > CAPS.slice || (left <= 1 && !(deadline && deadline.didTimeout))) break;
        }
        if (scanState.out.length) {
            const next = '@media screen{' + scanState.out.join('') + '}';
            if (next !== derivedCss) { derivedCss = next; render(); }
        }
        // Any sheets still unvisited (lazy-loaded chunks) get the next slice.
        if (collectSheets().length) scheduleScan('continue');
    }

    /* ─────────────────────────────────────────────────────────────────────
     * 5. Transport with background.js
     * ────────────────────────────────────────────────────────────────── */
    function applyPayload(data) {
        if (!data || typeof data.css !== 'string' || !data.css) { clearTheme(); return; }
        if (data.hash && data.hash === appliedHash && isAttached() && !derivedCss) return;
        baseCss = data.css;
        render();
        if (data.scan) scheduleScan('payload'); else { cancelScan(); if (derivedCss) { derivedCss = ''; render(); } }
    }

    /** Pre-paint from the parent-process storage cache. This is a direct IPC that
     *  does NOT require the (possibly suspended) event page to be resurrected, so
     *  it typically resolves inside the same task as document_start - eliminating
     *  the white flash that a background round-trip cannot avoid. */
    function fastPaint() {
        if (!IS_TOP) return Promise.resolve();
        let host = '';
        try { host = location.hostname.toLowerCase(); } catch (_) { }
        if (!host) return Promise.resolve();
        return browser.storage.local.get(PAINT_KEY).then((res) => {
            if (baseCss) return;                     // authoritative payload already won the race
            const entry = res && res[PAINT_KEY] && res[PAINT_KEY][host];
            if (!entry || typeof entry.css !== 'string') return;
            baseCss = entry.css;
            render();
            if (entry.scan) scheduleScan('fastpaint');
        }).catch(() => { });
    }

    let syncAttempt = 0;
    function sync() {
        if (disposed) return;
        browser.runtime.sendMessage({ type: 'GET_THEME_DATA' }).then((res) => {
            syncAttempt = 0;
            if (!res) return;
            if (!res.data || (res.status && res.status.manuallyStopped)) clearTheme();
            else applyPayload(res.data);
        }).catch(() => {
            // The event page may be cold-starting; back off with jitter so N frames
            // of a heavy page do not stampede the same wake-up.
            if (++syncAttempt > 6) return;
            const delay = Math.min(250 * Math.pow(2, syncAttempt), 8000) * (0.6 + Math.random() * 0.8);
            setTimeout(sync, delay);
        });
    }

    browser.runtime.onMessage.addListener((msg, sender) => {
        if (!msg || sender.id !== browser.runtime.id) return;
        if (msg.type === 'MATUGEN_UPDATE') applyPayload(msg.data);
        else if (msg.type === 'MATUGEN_ROLLBACK') clearTheme();
        else if (msg.type === 'MATUGEN_RESCAN') { if (scanState) { scanState.runs = 0; } scheduleScan('force'); }
    });

    /* ─────────────────────────────────────────────────────────────────────
     * 6. Document lifecycle
     * ────────────────────────────────────────────────────────────────── */
    window.addEventListener('pageshow', (e) => {
        // bfcache restore: the DOM is intact but the palette may have moved on.
        if (e.persisted) { disposed = false; sync(); }
    }, true);

    window.addEventListener('pagehide', (e) => {
        // Stop all work immediately; if the page is going into bfcache we must
        // leave zero live observers/timers behind or Gecko evicts the entry.
        stopObserver();
        cancelScan();
        if (!e.persisted) disposed = true;
    }, true);

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && scanState && !scanState.handle && scanState.runs <= 8) scheduleScan('visible');
    }, true);

    // Late-loading CSS (route chunks, print sheets, third-party widgets) only
    // becomes visible in document.styleSheets after load.
    window.addEventListener('load', () => { if (derivedCss || (scanState && scanState.runs)) scheduleScan('load'); }, { once: true, capture: true });

    fastPaint().then(sync);
})();
