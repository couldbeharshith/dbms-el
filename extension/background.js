// DBMS-EL — browsing activity tracker (MV3 service worker).
//
// Tracks dwell time per tab: navigation/tab-switch/idle events accumulate
// pending milliseconds; a 30s alarm flushes finished dwell periods to the
// backend as browsing_events rows.

const API = "http://localhost:8000";
const FLUSH_ALARM = "dbms-el-flush";
const MIN_FLUSH_MS = 3000; // ignore sub-3s glances

// tabId -> {url, domain, title, firstTs, pendingMs, sinceTs}
const tabs = new Map();
let activeTabId = null;

async function getSessionId() {
  const { sessionId } = await chrome.storage.session.get("sessionId");
  if (sessionId) return sessionId;
  const sid = crypto.randomUUID();
  await chrome.storage.session.set({ sessionId: sid });
  return sid;
}

function trackable(url) {
  return /^https?:\/\//.test(url) && !url.startsWith(`${API}/`);
}

function siteOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

function pauseTab(tabId) {
  const st = tabs.get(tabId);
  if (st && st.sinceTs) {
    st.pendingMs += Date.now() - st.sinceTs;
    st.sinceTs = null;
  }
}

function resumeTab(tabId) {
  const st = tabs.get(tabId);
  if (st && !st.sinceTs) st.sinceTs = Date.now();
}

function setTab(tabId, url, title) {
  if (!trackable(url)) {
    pauseTab(tabId);
    tabs.delete(tabId);
    return;
  }
  const existing = tabs.get(tabId);
  if (existing && existing.url === url) {
    if (title && title !== existing.title) existing.title = title;
    return;
  }
  pauseTab(tabId); // close out the previous page on this tab
  tabs.set(tabId, {
    url,
    domain: siteOf(url),
    title: title || "",
    firstTs: Date.now(),
    pendingMs: 0,
    sinceTs: tabId === activeTabId ? Date.now() : null,
  });
}

chrome.tabs.onActivated.addListener(({ tabId }) => {
  pauseTab(activeTabId);
  activeTabId = tabId;
  resumeTab(tabId);
});

chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.url || change.status === "complete") {
    setTab(tabId, tab.url || change.url, tab.title);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  pauseTab(tabId);
  tabs.delete(tabId);
  if (activeTabId === tabId) activeTabId = null;
});

chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) {
    pauseTab(activeTabId); // browser unfocused
  }
});

// Stop counting when the user goes idle; resume on activity.
chrome.idle.onStateChanged.addListener((state) => {
  if (state === "active") {
    resumeTab(activeTabId);
  } else {
    pauseTab(activeTabId);
  }
});
chrome.idle.setDetectionInterval(60);

async function flush() {
  const { trackingEnabled } = await chrome.storage.local.get({ trackingEnabled: true });
  if (!trackingEnabled) {
    for (const st of tabs.values()) { st.pendingMs = 0; }
    return;
  }
  const sessionId = await getSessionId();

  // Credit the still-active dwell first.
  pauseTab(activeTabId);

  const events = [];
  const now = Date.now();
  for (const [tabId, st] of tabs) {
    if (st.pendingMs >= MIN_FLUSH_MS) {
      events.push({
        url: st.url,
        domain: st.domain,
        title: st.title,
        visited_at: new Date(st.firstTs).toISOString(),
        time_spent_sec: Math.round(st.pendingMs / 1000),
        session_id: sessionId,
        metadata: { tab_id: tabId, client: "chrome-extension" },
      });
      st.pendingMs = 0;
    }
    // Drop stale tabs not touched for an hour.
    if (now - st.firstTs > 3600_000) tabs.delete(tabId);
  }
  resumeTab(activeTabId);

  if (events.length === 0) return;
  try {
    const resp = await fetch(`${API}/api/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events }),
    });
    if (!resp.ok) console.warn("dbms-el: events POST failed", resp.status);
  } catch (e) {
    console.warn("dbms-el: backend unreachable", e);
  }
}

chrome.alarms.create(FLUSH_ALARM, { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FLUSH_ALARM) flush();
});

// Content scripts on https:// pages cannot fetch http://localhost directly
// (Private Network Access + page CSP). The service worker has host_permissions
// for the backend, so it proxies /api/context for them.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "getContext") return;
  fetch(`${API}/api/context`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: msg.query, site: msg.site }),
  })
    .then((r) => r.json())
    .then(sendResponse)
    .catch((e) => sendResponse({ context: "", error: String(e) }));
  return true; // keep the message channel open for the async response
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.tabs.query({}, (all) => {
    for (const t of all) if (t.active && t.url) {
      activeTabId = t.id;
      setTab(t.id, t.url, t.title);
    }
  });
});
