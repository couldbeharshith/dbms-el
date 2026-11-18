import { useEffect, useRef, useState } from "react";
import Dashboard from "./pages/Dashboard";
import History from "./pages/History";
import Memories from "./pages/Memories";
import Recall from "./pages/Recall";
import Settings from "./pages/Settings";
import Icon from "./components/Icon";
import LockScreen, { fetchAccountCount } from "./components/LockScreen";
import { get } from "./api";

type Page = "dashboard" | "history" | "recall" | "memories" | "settings";

const NAV: { id: Page; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "history", label: "History" },
  { id: "recall", label: "Recall" },
  { id: "memories", label: "Memories" },
  { id: "settings", label: "Settings" },
];

const IDLE_LOCK_MS = 5 * 60 * 1000; // lock after 5 min without input
const LAST_USER_KEY = "dbmsel.lastUser";

interface Status {
  db: boolean;
  embed_ready: boolean;
  embed_device: string;
  openai_configured: boolean;
  model: string;
}

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [status, setStatus] = useState<Status | null>(null);
  const [online, setOnline] = useState(false);

  // ---- lock state: app starts locked, relocks after 5 idle minutes --------
  const [locked, setLocked] = useState(true);
  const [accounts, setAccounts] = useState<number | null>(null);
  const [user, setUser] = useState(localStorage.getItem(LAST_USER_KEY) || "");
  const lastActivity = useRef(Date.now());

  useEffect(() => {
    let stop = false;
    const load = () =>
      fetchAccountCount()
        .then((n) => !stop && setAccounts(n))
        .catch(() => !stop && setTimeout(load, 2000)); // backend not up yet
    load();
    return () => { stop = true; };
  }, []);

  useEffect(() => {
    const bump = () => { lastActivity.current = Date.now(); };
    const evs = ["mousemove", "mousedown", "keydown", "wheel", "touchstart"];
    evs.forEach((e) => window.addEventListener(e, bump, { passive: true }));
    const t = setInterval(() => {
      if (Date.now() - lastActivity.current >= IDLE_LOCK_MS) setLocked(true);
    }, 5000);
    return () => {
      evs.forEach((e) => window.removeEventListener(e, bump));
      clearInterval(t);
    };
  }, []);

  const unlock = (username: string) => {
    localStorage.setItem(LAST_USER_KEY, username);
    setUser(username);
    setAccounts((n) => Math.max(n ?? 0, 1));
    lastActivity.current = Date.now();
    setLocked(false);
  };

  useEffect(() => {
    const poll = () =>
      get<Status>("/api/status")
        .then((s) => {
          setStatus(s);
          setOnline(true);
        })
        .catch(() => setOnline(false));
    poll();
    const t = setInterval(poll, 10000);
    return () => clearInterval(t);
  }, []);

  return (
    <>
      <div className={`flex h-screen w-screen transition ${locked ? "pointer-events-none select-none blur-md" : ""}`}>
        {/* sidebar */}
        <aside className="flex w-60 shrink-0 flex-col border-r-2 border-white/10 bg-[#0a0d12]">
          <div className="flex items-center gap-3 px-5 pb-5 pt-6">
            <div className="btn-accent flex h-9 w-9 items-center justify-center rounded-xl">
              <Icon name="brain" className="h-5 w-5" />
            </div>
            <div>
              <div className="text-base font-bold tracking-tight">DBMS-EL</div>
              <div className="text-[11px] text-slate-500">Personal memory layer</div>
            </div>
          </div>
          <nav className="flex-1 space-y-1 px-3">
            {NAV.map((n) => (
              <button
                key={n.id}
                onClick={() => setPage(n.id)}
                className={`relative flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-[13px] font-medium transition ${
                  page === n.id
                    ? "nav-active"
                    : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
                }`}
              >
                
                <Icon name={n.id} className={`h-[18px] w-[18px] `} />
                {n.label}
              </button>
            ))}
          </nav>

          <div className="space-y-3 border-t border-white/8 px-4 py-4">
            <div className="surface flex items-center gap-3 rounded-xl px-3 py-2.5">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#d8f35a] text-xs font-bold uppercase text-[#0e1218]">
                {user.slice(0, 1) || "?"}
              </div>
              <div className="min-w-0 flex-1 truncate text-[13px] font-medium">{user || "—"}</div>
              <button
                onClick={() => setLocked(true)}
                title="Lock now"
                className="rounded-lg p-1.5 text-slate-400 hover:bg-white/10 hover:text-white"
              >
                <Icon name="lock" className="h-4 w-4" />
              </button>
            </div>
            <div className="text-[11px] leading-relaxed text-slate-500">
              <div className="flex items-center gap-2">
                <span className={`inline-block h-2 w-2 rounded-full ${online ? "bg-emerald-400" : "bg-red-400"}`} />
                backend {online ? "online" : "offline"}
              </div>
              {status && (
                <>
                  <div className="mt-1 flex items-center gap-2">
                    <span className={`inline-block h-2 w-2 rounded-full ${status.embed_ready ? "bg-emerald-400" : "bg-amber-400"}`} />
                    embed · {status.embed_device}
                  </div>
                  <div className="mt-1 truncate">{status.model}</div>
                </>
              )}
            </div>
          </div>
        </aside>

        {/* content */}
        <main className="flex-1 overflow-y-auto">
          {page === "dashboard" && <Dashboard />}
          {page === "history" && <History />}
          {page === "recall" && <Recall />}
          {page === "memories" && <Memories />}
          {page === "settings" && <Settings />}
        </main>
      </div>

      {locked &&
        (accounts === null ? (
          <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 text-sm text-slate-400 backdrop-blur-2xl">
            Connecting to backend…
          </div>
        ) : (
          <LockScreen hasAccounts={accounts > 0} lastUser={user} onUnlock={unlock} />
        ))}
    </>
  );
}
