// DBMS-EL desktop app — Electron main process.
// Owns the window, the periodic screenshot capture, foreground-window title
// detection (PowerShell), the dbms-el:// protocol, and settings IPC.

const { app, BrowserWindow, desktopCapturer, ipcMain, protocol } = require("electron");
const { spawn } = require("child_process");
const path = require("path");

const API = "http://localhost:8000";

let mainWindow = null;
let captureTimer = null;
let capturing = false;
let fgBusy = false;

// ---------------------------------------------------------------- window ---
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#0b0d12",
    title: "DBMS-EL — Memory Layer",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  const startUrl = process.env.ELECTRON_START_URL;
  if (startUrl) {
    mainWindow.loadURL(startUrl).catch((e) => console.error("loadURL failed:", e));
  } else {
    mainWindow
      .loadFile(path.join(__dirname, "../dist/index.html"))
      .catch((e) => console.error("loadFile failed:", e));
  }
  mainWindow.webContents.on("did-fail-load", (_e, code, desc, url) =>
    console.error(`did-fail-load ${code} ${desc} ${url}`)
  );
  mainWindow.webContents.on("console-message", (_e, level, message) => {
    if (level >= 2) console.error("renderer console:", message);
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// dbms-el://open — used by the Chrome extension popup to focus this app.
protocol.registerSchemesAsPrivileged([
  { scheme: "dbms-el", privileges: { standard: true, secure: true } },
]);

app.whenReady().then(() => {
  protocol.handle("dbms-el", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    } else {
      createWindow();
    }
    return new Response("", { status: 200 });
  });
  createWindow();
  scheduleNextCapture();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// ------------------------------------------------------------- settings ---
async function fetchSettings() {
  try {
    const r = await fetch(`${API}/api/settings`);
    if (!r.ok) throw new Error(String(r.status));
    return (await r.json()).settings || {};
  } catch {
    return null;
  }
}

// ------------------------------------------------------------ screenshots ---
function foregroundTitle() {
  if (fgBusy) return Promise.resolve(null);
  fgBusy = true;
  const ps = [
    "Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;",
    "public class W{[DllImport(\"user32.dll\")]public static extern IntPtr GetForegroundWindow();",
    "[DllImport(\"user32.dll\")]public static extern int GetWindowText(IntPtr h,System.Text.StringBuilder s,int n);}'",
    ";$b=New-Object System.Text.StringBuilder(512);",
    "[W]::GetWindowText([W]::GetForegroundWindow(),$b,512)|Out-Null;$b.ToString()",
  ].join("");
  return new Promise((resolve) => {
    const child = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", ps], {
      windowsHide: true,
    });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.on("error", () => { fgBusy = false; resolve(null); });
    child.on("close", () => { fgBusy = false; resolve(out.trim() || null); });
    setTimeout(() => { try { child.kill(); } catch {} fgBusy = false; resolve(null); }, 2500);
  });
}

async function captureOnce() {
  if (capturing) return;
  capturing = true;
  try {
    const settings = await fetchSettings();
    if (!settings) return; // backend down — skip this tick
    if (!settings.screenshots_enabled) return;

    const sources = await desktopCapturer.getSources({
      types: ["screen"],
      thumbnailSize: { width: 1920, height: 1080 },
    });
    if (!sources.length) return;
    const primary = sources[0];
    const png = primary.thumbnail.toPNG();
    const size = primary.thumbnail.getSize();
    const title = await foregroundTitle();

    await fetch(`${API}/api/screenshots`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_b64: png.toString("base64"),
        width: size.width,
        height: size.height,
        source_app: title,
        captured_at: new Date().toISOString(),
        metadata: { display: primary.display_id },
      }),
    });
  } catch (e) {
    console.warn("capture failed:", e?.message || e);
  } finally {
    capturing = false;
  }
}

async function scheduleNextCapture() {
  clearTimeout(captureTimer);
  await captureOnce();
  const settings = await fetchSettings();
  const interval = Math.max(10, settings?.screenshot_interval_sec || 30);
  captureTimer = setTimeout(scheduleNextCapture, interval * 1000);
}

// ------------------------------------------------------------------- IPC ---
ipcMain.handle("settings:get", async () => (await fetch(`${API}/api/settings`).then((r) => r.json())).settings);
ipcMain.handle("settings:set", async (_ev, patch) => {
  const r = await fetch(`${API}/api/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return r.json();
});
ipcMain.handle("capture:now", async () => {
  await captureOnce();
  return true;
});
