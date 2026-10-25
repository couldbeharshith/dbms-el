const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktop", {
  getSettings: () => ipcRenderer.invoke("settings:get"),
  setSettings: (patch) => ipcRenderer.invoke("settings:set", patch),
  captureNow: () => ipcRenderer.invoke("capture:now"),
});
