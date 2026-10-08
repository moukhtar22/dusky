/*
 * Dusky Template Generator — background.js (MV3 ES-module event page, Gecko 156+)
 *
 * The only privileged broker.
 *   1. ONE long-lived native host process (runtime.connectNative), id-correlated,
 *      per-request AbortSignal.timeout, idle disconnect, two-strike port reset.
 *   2. content.js injected on demand, TOP FRAME ONLY, under the activeTab grant.
 *   3. Alt+Shift+P toggles the picker, degrading to "open the popup" when the
 *      command carries no host permission (a command is NOT an activeTab grant).
 *   4. For tab-originated messages the domain is ALWAYS derived from sender.url
 *      and the sender MUST be the top frame, so no page (and no embedded frame)
 *      can write another site's template.
 *   5. Host calls are serialised PER DOMAIN; a stuck write on one site can never
 *      block a read on another.
 *   6. Every successful mutation is announced with the originator's nonce, so a
 *      view can tell "someone else changed the file" from "that was me".
 */
"use strict";

const HOST = "dusky_template_generator";
const HOST_OPS = new Set(["ping", "read", "write", "splice", "delete"]);
const MUTATIONS = new Set(["write", "splice", "delete"]);
const REGIONS = new Set(["auto", "picks"]);
const HOST_TIMEOUT_MS = 8_000;
const IDLE_DISCONNECT_MS = 30_000;
/* Firefox refuses native-messaging frames over 1 MiB in either direction. */
const MAX_PAYLOAD = 768 * 1024;

/* ── Native port ─────────────────────────────────────────────────────── */
let port = null;
let nextId = 1;
let idleTimer = 0;
let strikes = 0;
const pending = new Map();

function rejectAll(reason) {
  for (const entry of pending.values()) entry.reject(new Error(reason));
  pending.clear();
}

function dropPort(reason) {
  const dead = port;
  port = null;
  strikes = 0;
  rejectAll(reason);
  try { dead?.disconnect(); } catch { /* already gone */ }
}

function armIdle() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => { if (!pending.size) dropPort("idle"); }, IDLE_DISCONNECT_MS);
}

function getPort() {
  if (port) return port;
  port = browser.runtime.connectNative(HOST);
  port.onMessage.addListener((reply) => {
    const entry = pending.get(reply?.__id);
    if (!entry) return;                              /* late reply after timeout */
    pending.delete(reply.__id);
    entry.settle(reply);
    strikes = 0;
    armIdle();
  });
  port.onDisconnect.addListener((dead) => {
    port = null;
    rejectAll(dead.error?.message ?? "native host disconnected");
  });
  return port;
}

function postToHost(request) {
  const { promise, resolve, reject } = Promise.withResolvers();
  let live;
  try { live = getPort(); } catch (err) { return Promise.reject(err); }

  const id = nextId++;
  const signal = AbortSignal.timeout(HOST_TIMEOUT_MS);
  const onAbort = () => {
    pending.delete(id);
    /* A single slow answer is not proof of a dead host; two in a row is. */
    if (++strikes >= 2) dropPort(`native host timed out after ${HOST_TIMEOUT_MS} ms`);
    reject(new Error("Native host timed out — check: python3 host/dusky_template_host.py --selftest"));
  };
  signal.addEventListener("abort", onAbort, { once: true });

  pending.set(id, {
    reject,
    settle(reply) {
      signal.removeEventListener("abort", onAbort);
      const { __id, ...clean } = reply;
      void __id;
      resolve(clean);
    },
  });

  try {
    live.postMessage({ ...request, __id: id });
  } catch (err) {
    pending.delete(id);
    signal.removeEventListener("abort", onAbort);
    dropPort("post failed");
    reject(err);
  }
  armIdle();
  return promise;
}

/* Per-domain serialisation: read-modify-write on ONE file must not interleave,
 * but two different files have no reason to wait for each other. */
const lanes = new Map();

function hostCall(request) {
  const lane = String(request.domain ?? "\u0000global");
  const prev = lanes.get(lane) ?? Promise.resolve();
  const run = () => postToHost(request);
  const job = prev.then(run, run);
  const tail = job.then(() => undefined, () => undefined).then(() => {
    if (lanes.get(lane) === tail) lanes.delete(lane);
  });
  lanes.set(lane, tail);
  return job;
}

/* ── Helpers ─────────────────────────────────────────────────────────── */
function siteOf(url) {
  const u = URL.parse(url ?? "");
  if (!u || (u.protocol !== "https:" && u.protocol !== "http:")) return "";
  return u.hostname.replace(/^www\./, "");
}

function explain(err) {
  const m = String(err?.message ?? err);
  if (/No such native application|not found|Attempt to postMessage on disconnected/i.test(m)) {
    return "Native host not registered — run  python3 setup.py  in the extension folder, then reload the extension.";
  }
  if (/timed out/i.test(m)) return m;
  if (/disconnected|exited|unexpected error/i.test(m)) {
    return "Native host crashed — run  python3 host/dusky_template_host.py --selftest";
  }
  if (/Missing host permission|not allowed on this page|restricted|cannot access/i.test(m)) {
    return "Firefox does not allow extensions here (about:, addons.mozilla.org, PDF viewer, view-source:).";
  }
  if (/Receiving end does not exist|Could not establish connection/i.test(m)) {
    return "The page reloaded — reopen the popup.";
  }
  return m;
}

/* Talk to the page, injecting the content script (top frame only) on demand. */
async function page(tabId, msg) {
  const opts = { frameId: 0 };
  try {
    return await browser.tabs.sendMessage(tabId, msg, opts);
  } catch {
    await browser.scripting.executeScript({
      target: { tabId, allFrames: false },
      files: ["content.js"],
      injectImmediately: true,
    });
    return browser.tabs.sendMessage(tabId, msg, opts);
  }
}

/* Announce a committed change so every open view converges on disk. */
function announce(reply, { tabId, fromTab, origin }) {
  const evt = {
    type: "dusky:changed",
    domain: reply.domain ?? "",
    rev: reply.rev ?? 0,
    path: reply.path ?? "",
    exists: !!reply.exists,
    origin: origin ?? "",
  };
  browser.runtime.sendMessage(evt).catch(() => {});                  /* open popups */
  if (!fromTab && Number.isInteger(tabId)) {                         /* the popup's tab */
    browser.tabs
      .sendMessage(tabId, { type: "rehydrate", domain: evt.domain }, { frameId: 0 })
      .catch(() => {});
  }
}

function tooBig(request) {
  for (const field of ["css", "body"]) {
    const value = request[field];
    if (typeof value === "string" && value.length > MAX_PAYLOAD) {
      return `${field} is ${(value.length / 1024) | 0} KiB — the native messaging limit is 1 MiB. Split the template.`;
    }
  }
  return "";
}

/* ── Message router ──────────────────────────────────────────────────── */
browser.runtime.onMessage.addListener((msg, sender) => {
  if (typeof msg?.type !== "string" || msg.type === "dusky:changed") return false;

  /* popup → page relay (never reachable from a content script) */
  if (msg.type === "page" && !sender.tab) {
    if (!Number.isInteger(msg.tabId)) return Promise.resolve({ ok: false, error: "No tab." });
    return page(msg.tabId, msg.msg).then(
      (reply) => (reply && typeof reply === "object" ? reply : { ok: false, error: "The page did not answer." }),
      (err) => ({ ok: false, error: explain(err) }),
    );
  }
  if (!HOST_OPS.has(msg.type)) return false;

  const fromTab = !!sender.tab;
  const { tabId: askedTab, origin, ...request } = msg;
  const tabId = fromTab ? sender.tab.id : askedTab;

  if (fromTab) {
    /* A sub-frame is never allowed to speak for the tab. */
    if (sender.frameId !== 0) {
      return Promise.resolve({ ok: false, error: "Frames cannot write templates." });
    }
    const domain = siteOf(sender.url);
    if (!domain) return Promise.resolve({ ok: false, error: "This page has no themeable domain." });
    request.domain = domain;                          /* never trust the sender */
  } else if (typeof request.domain === "string") {
    request.domain = siteOf(`https://${request.domain}`) || request.domain;
  }

  if (msg.type === "splice" && !REGIONS.has(request.region)) {
    return Promise.resolve({ ok: false, error: `Unknown region: ${request.region}` });
  }
  const oversize = tooBig(request);
  if (oversize) return Promise.resolve({ ok: false, error: oversize });

  return hostCall(request).then(
    (reply) => {
      if (!reply || typeof reply !== "object") return { ok: false, error: "Empty reply from native host" };
      if (reply.ok && MUTATIONS.has(msg.type)) announce(reply, { tabId, fromTab, origin });
      return reply;
    },
    (err) => ({ ok: false, error: explain(err) }),
  );
});

/* ── Keyboard command ────────────────────────────────────────────────── */
browser.commands.onCommand.addListener(async (command) => {
  if (command !== "toggle-picker") return;
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  /* tab.url is only visible with a host permission; absence is not a refusal. */
  if (tab.url && !siteOf(tab.url)) return;

  try {
    await browser.tabs.sendMessage(tab.id, { type: "picker" }, { frameId: 0 });
    return;                                            /* already injected */
  } catch { /* not injected yet */ }

  try {
    /* Works only with a host permission (about:addons → Permissions, or the
     * optional_host_permissions prompt). A command alone is not activeTab. */
    await browser.scripting.executeScript({
      target: { tabId: tab.id, allFrames: false },
      files: ["content.js"],
      injectImmediately: true,
    });
    await browser.tabs.sendMessage(tab.id, { type: "picker" }, { frameId: 0 });
    return;
  } catch { /* no permission on this origin */ }

  await browser.action.openPopup().catch(() => {});
});