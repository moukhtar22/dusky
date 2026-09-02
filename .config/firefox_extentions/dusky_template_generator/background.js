/*
 * Dusky Template Generator — background.js (event page)
 *
 * The only privileged broker in the extension:
 *   1. every native-host call goes through one promise chain, so file writes from the
 *      popup and from the picker can never interleave (one host process at a time);
 *   2. content.js is injected on demand with the activeTab grant — nothing runs on a
 *      page until the user asks;
 *   3. the Alt+Shift+P command toggles the picker on the current tab.
 * The popup never touches tabs.executeScript, and content scripts never name a domain:
 * it is derived from sender.url, so a tab can only ever write its own template.
 */
"use strict";

const HOST = "dusky_template_generator";
const HOST_OPS = new Set(["ping", "read", "write", "splice", "delete"]);
let queue = Promise.resolve();

function siteOf(url) {
  try {
    const u = new URL(url);
    return /^https?:$/.test(u.protocol) ? u.hostname.replace(/^www\./, "") : "";
  } catch (_) {
    return "";
  }
}

function explain(err) {
  const m = String((err && err.message) || err);
  if (/No such native application/i.test(m)) {
    return "Native host not installed — run  python3 setup.py  in the extension folder, then reload the extension.";
  }
  if (/unexpected error|disconnected|exited/i.test(m)) {
    return "Native host failed to start or crashed — run  python3 host/dusky_template_host.py --selftest";
  }
  if (/Missing host permission|not allowed on this page|restricted/i.test(m)) {
    return "Firefox does not allow extensions on this page (about:, addons.mozilla.org, PDF viewer, …).";
  }
  if (/Receiving end does not exist|Could not establish connection/i.test(m)) {
    return "The page was reloaded — open the popup again.";
  }
  return m;
}

function host(request) {
  const run = () => browser.runtime.sendNativeMessage(HOST, request);
  const job = queue.then(run, run);
  queue = job.catch(() => {});
  return job;
}

async function page(tabId, msg) {
  try {
    return await browser.tabs.sendMessage(tabId, msg);
  } catch (_) {
    await browser.tabs.executeScript(tabId, { file: "content.js", runAt: "document_idle" });
    return browser.tabs.sendMessage(tabId, msg);
  }
}

browser.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || typeof msg.type !== "string") return;
  let job;
  if (msg.type === "page" && !sender.tab) {
    job = page(msg.tabId, msg.msg);                                   // popup → tab, injecting if needed
  } else if (HOST_OPS.has(msg.type)) {
    job = host(sender.tab ? { ...msg, domain: siteOf(sender.url) } : msg);
  } else {
    return;
  }
  return job.then(
    (reply) => (reply && typeof reply === "object" ? reply : { ok: false, error: "Empty reply" }),
    (err) => ({ ok: false, error: explain(err) })
  );
});

browser.commands.onCommand.addListener(async (command) => {
  if (command !== "toggle-picker") return;
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  if (tab && siteOf(tab.url)) page(tab.id, { type: "picker" }).catch(() => {});
});
