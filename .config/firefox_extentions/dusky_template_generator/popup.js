/*
 * Dusky Template Generator — popup.js
 *
 * The popup is a view over exactly one file, ~/.config/dusky_sites/<domain>.css:
 *   open       → read the file from disk (disk is the only source of truth)
 *   Auto-map   → scan the page, splice the result into the file's "auto" region
 *   Pick       → start the in-page picker (it saves every pick itself) and close
 *   Save       → write the textarea verbatim (Ctrl+S); empty text removes the file
 *   Copy       → clipboard;  Delete → two-step, removes the file
 * Unsaved textarea edits are flushed before Auto-map / Pick, so nothing is ever lost.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const ui = {
  domain: $("domain"), css: $("css"), path: $("path"), status: $("status"),
  auto: $("auto"), pick: $("pick"), save: $("save"), copy: $("copy"), del: $("delete")
};
const ALL_BUTTONS = [ui.auto, ui.pick, ui.save, ui.copy, ui.del];
const view = { tab: null, domain: "", saved: "", exists: false, path: "", pickerOn: false, armedAt: 0, timer: 0 };

function domainOf(url) {
  try {
    const u = new URL(url);
    return /^https?:$/.test(u.protocol) ? u.hostname.replace(/^www\./, "") : "";
  } catch (_) {
    return "";
  }
}
const fileName = () => view.domain + ".css";
const shortPath = (p) => p.replace(/^\/home\/[^/]+(?=\/)/, "~");

function say(text, kind) {
  clearTimeout(view.timer);
  ui.status.textContent = text;
  ui.status.className = "status " + kind;
  if (kind === "ok") view.timer = setTimeout(() => { ui.status.textContent = ""; ui.status.className = "status"; }, 4000);
}

async function bg(msg) {
  const reply = await browser.runtime.sendMessage(msg);
  if (!reply || !reply.ok) throw new Error((reply && reply.error) || "No reply from the background script");
  return reply;
}
const pg = (msg) => bg({ type: "page", tabId: view.tab.id, msg });

function setDoc(css, exists, path) {
  view.saved = css || "";
  view.exists = !!exists;
  if (path) view.path = path;
  if (ui.css.value !== view.saved) ui.css.value = view.saved;
  ui.path.textContent = shortPath(view.path) + (view.exists ? "" : "  · not created yet");
  refresh();
}

function refresh() {
  const text = ui.css.value;
  const dirty = text !== view.saved;
  ui.save.disabled = !dirty;
  ui.save.classList.toggle("attention", dirty);
  ui.save.textContent = dirty ? "💾 Save changes" : "💾 Saved";
  ui.copy.disabled = !text.trim();
  ui.del.disabled = !view.exists;
  ui.pick.classList.toggle("on", view.pickerOn);
  ui.pick.querySelector("b").textContent = view.pickerOn ? "■ Stop picking" : "🎯 Pick elements";
  ui.pick.querySelector("small").textContent = view.pickerOn
    ? "picker is running on this page"
    : "click things on the page, assign a role";
}

function busy(on) {
  for (const b of ALL_BUTTONS) b.disabled = on;
  if (!on) refresh();
}

async function run(task) {
  busy(true);
  try {
    await task();
  } catch (err) {
    say(err.message || String(err), "err");
  } finally {
    busy(false);
  }
}

async function save() {
  const reply = await bg({ type: "write", domain: view.domain, css: ui.css.value });
  setDoc(reply.css, reply.exists, reply.path);
  say(reply.exists ? "Saved " + fileName() : "Template was empty — file removed", "ok");
}
async function flushEdits() {
  if (ui.css.value !== view.saved) await save();
}
async function reload() {
  const reply = await bg({ type: "read", domain: view.domain });
  setDoc(reply.css, reply.exists, reply.path);
}

ui.auto.addEventListener("click", () => run(async () => {
  await flushEdits();
  say("Scanning the page's colour variables…", "busy");
  const scan = await pg({ type: "scan" });
  if (!scan.mapped) {
    say("Found " + scan.found + " colour variable(s) but none matched a palette role — use Pick elements instead.", "warn");
    return;
  }
  const reply = await bg({ type: "splice", domain: view.domain, region: "auto", body: scan.body });
  setDoc(reply.css, reply.exists, reply.path);
  say("Mapped " + scan.mapped + " of " + scan.found + " variables → saved " + fileName(), "ok");
}));

ui.pick.addEventListener("click", () => run(async () => {
  if (view.pickerOn) {
    await pg({ type: "picker", enable: false });
    view.pickerOn = false;
    await reload();
    say("Picker stopped", "ok");
    return;
  }
  await flushEdits();
  await pg({ type: "picker", enable: true });
  window.close();
}));

ui.save.addEventListener("click", () => run(save));

ui.copy.addEventListener("click", () => run(async () => {
  await navigator.clipboard.writeText(ui.css.value);
  say("Copied to clipboard", "ok");
}));

ui.del.addEventListener("click", () => run(async () => {
  if (Date.now() - view.armedAt > 3000) {
    view.armedAt = Date.now();
    ui.del.textContent = "🗑 Confirm";
    say("Click again within 3 s to delete " + fileName() + " from disk.", "warn");
    setTimeout(() => { view.armedAt = 0; ui.del.textContent = "🗑 Delete"; }, 3000);
    return;
  }
  view.armedAt = 0;
  ui.del.textContent = "🗑 Delete";
  const reply = await bg({ type: "delete", domain: view.domain });
  ui.css.value = "";
  setDoc("", false, reply.path);
  browser.tabs.sendMessage(view.tab.id, { type: "reset" }).catch(() => {});
  say("Deleted " + fileName(), "ok");
}));

ui.css.addEventListener("input", refresh);
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    if (!ui.save.disabled) ui.save.click();
  }
});

(async function init() {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  view.tab = tab;
  view.domain = domainOf(tab && tab.url);
  if (!view.domain) {
    ui.domain.textContent = "not a website";
    ui.css.placeholder = "Open a normal http(s) website to theme it.\nFirefox keeps extensions out of about:, file: and add-on pages.";
    for (const b of ALL_BUTTONS) b.disabled = true;
    return;
  }
  ui.domain.textContent = view.domain;
  ui.css.placeholder = "No template for " + view.domain + " yet.\n\n⚡ Auto-map fills this from the site's colour variables,\n🎯 Pick elements lets you click parts of the page,\nor paste CSS here and Save.";
  try {
    const reply = await bg({ type: "read", domain: view.domain });
    view.domain = reply.domain;
    ui.domain.textContent = reply.domain;
    setDoc(reply.css, reply.exists, reply.path);
  } catch (err) {
    say(err.message, "err");
    refresh();
  }
  const state = await browser.tabs.sendMessage(tab.id, { type: "ping" }).catch(() => null);
  view.pickerOn = !!(state && state.active);
  refresh();
})();
