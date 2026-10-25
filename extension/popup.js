const API = "http://localhost:8000";

async function refresh() {
  try {
    const resp = await fetch(`${API}/api/stats`);
    const data = await resp.json();
    document.getElementById("eventCount").textContent =
      data.counts ? data.counts.events_today : 0;
    document.getElementById("err").textContent = "";
  } catch {
    document.getElementById("eventCount").textContent = "–";
    document.getElementById("err").textContent = "backend offline";
  }
}

function bindToggle(id, key) {
  const el = document.getElementById(id);
  const render = (on) => {
    el.textContent = on ? "ON" : "OFF";
    el.className = "toggle " + (on ? "on" : "off");
  };
  chrome.storage.local.get({ [key]: true }).then(({ [key]: on }) => render(on));
  el.onclick = async () => {
    const { [key]: on } = await chrome.storage.local.get({ [key]: true });
    await chrome.storage.local.set({ [key]: !on });
    render(!on);
  };
}

bindToggle("trackingToggle", "trackingEnabled");
bindToggle("injectionToggle", "injectionEnabled");
refresh();

document.getElementById("openApp").onclick = () => {
  // Custom protocol registered by the Electron app.
  location.href = "dbms-el://open";
};
