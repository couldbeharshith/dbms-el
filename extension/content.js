// DBMS-EL — chat-site content script.
//
// When the user sends a message on chatgpt.com / claude.ai / gemini.google.com,
// intercept the send, ask the local backend for relevant memories, append the
// retrieved context AFTER the user's text, then send. Any failure sends the
// original message unchanged — never block the user.

const API = "http://localhost:8000";
const HOST = location.hostname;

const SITES = {
  "chatgpt.com": {
    composer: () =>
      document.querySelector("#prompt-textarea") ||
      document.querySelector('div[contenteditable="true"]'),
    sendButton: () =>
      document.querySelector('button[data-testid="send-button"]') ||
      document.querySelector('button[aria-label="Send prompt"]') ||
      document.querySelector('button[aria-label="Send message"]') ||
      document.querySelector("form button[type='submit']"),
    kind: "prosemirror",
  },
  "claude.ai": {
    composer: () => document.querySelector('div[contenteditable="true"].ProseMirror'),
    sendButton: () =>
      document.querySelector('button[aria-label="Send message"]') ||
      document.querySelector('button[aria-label="Send Message"]'),
    kind: "prosemirror",
  },
  "gemini.google.com": {
    composer: () => document.querySelector("rich-textarea .ql-editor"),
    sendButton: () =>
      document.querySelector("button.send-button") ||
      document.querySelector("button[aria-label*='Send message' i]"),
    kind: "quill",
  },
};

const site = SITES[HOST];
let injecting = false; // guard against intercepting our own re-send

function composerText(el) {
  return (el?.innerText || el?.value || "").trim();
}

function fetchContext(query) {
  // Via the background service worker — content scripts on https pages can't
  // fetch localhost directly (PNA/CSP). 10s timeout, then a direct-attempt
  // fallback in case the SW is asleep.
  const viaBackground = new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type: "getContext", query, site: HOST }, (resp) => {
        if (chrome.runtime.lastError) return resolve(null);
        resolve(resp || null);
      });
    } catch {
      resolve(null);
    }
  });
  const direct = async () => {
    const resp = await fetch(`${API}/api/context`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, site: HOST }),
    });
    return resp.ok ? resp.json() : null;
  };
  return Promise.race([
    viaBackground,
    new Promise((r) => setTimeout(() => r(null), 10000)),
  ]).then((result) => result ?? direct());
}

function setComposerText(el, fullText, kind) {
  el.focus();
  // Caret to the end, then insert (works for ProseMirror and plain editors).
  const sel = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
  const ok = document.execCommand("insertText", false, fullText);
  if (!ok) {
    // Fallback: direct DOM write + input event (Quill and simpler editors).
    el.innerText = fullText;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true }));
  }
}

function toast(message, ok = true) {
  const host = document.createElement("div");
  host.style.cssText = `
    position: fixed; bottom: 20px; right: 20px; z-index: 2147483647;
    font: 13px/1.4 system-ui, sans-serif; color: #e5e7eb;
    background: ${ok ? "rgba(17,24,39,.95)" : "rgba(120,20,30,.95)"};
    border: 1px solid rgba(255,255,255,.12); border-radius: 10px;
    padding: 10px 14px; box-shadow: 0 8px 24px rgba(0,0,0,.35);
    max-width: 320px; pointer-events: none; transition: opacity .3s;`;
  host.textContent = message;
  document.documentElement.appendChild(host);
  setTimeout(() => { host.style.opacity = "0"; }, 2600);
  setTimeout(() => host.remove(), 3100);
}

async function handleSend(ev) {
  if (injecting || !site) return;
  const composer = site.composer();
  const sendBtn = site.sendButton();
  const fromKeyboard = ev.type === "keydown";
  const clickedSend = !fromKeyboard && sendBtn && (ev.target === sendBtn || sendBtn.contains(ev.target));
  const enterInComposer =
    fromKeyboard && composer && (ev.target === composer || composer.contains(ev.target)) &&
    ev.key === "Enter" && !ev.shiftKey && !ev.ctrlKey && !ev.altKey && !ev.metaKey;
  if (!clickedSend && !enterInComposer) return;

  const text = composerText(composer);
  if (!text || text.includes("[Personal memory context")) {
    return; // nothing to augment / already injected
  }

  injecting = true;
  ev.preventDefault();
  ev.stopImmediatePropagation();

  let resp = null;
  try {
    const { injectionEnabled } = await chrome.storage.local.get({ injectionEnabled: true });
    if (injectionEnabled) resp = await fetchContext(text);
  } catch {
    resp = null;
  }
  const context = (resp && resp.context) || "";

  if (!resp) {
    toast("🧠 DBMS-EL: backend unreachable", false);
  } else if (context) {
    try {
      setComposerText(composer, text + "\n\n" + context, site.kind);
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      toast(`🧠 DBMS-EL: injected ${resp.count || ""} memor${resp.count === 1 ? "y" : "ies"}`);
    } catch {
      toast("🧠 DBMS-EL: injection failed — sending as-is", false);
    }
  } else {
    toast("🧠 DBMS-EL: no relevant memories", false);
  }

  // Re-trigger the original send on the (possibly updated) composer.
  try {
    const btn = site.sendButton();
    if (btn && !btn.disabled) {
      btn.click();
    } else if (site.kind === "quill" && composer) {
      composer.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true })
      );
    }
  } catch {
    toast("🧠 DBMS-EL: press send again", false);
  }
  setTimeout(() => { injecting = false; }, 400);
}

// Capture phase so we see the event before the site's own handlers.
document.addEventListener("click", handleSend, true);
document.addEventListener("keydown", handleSend, true);
