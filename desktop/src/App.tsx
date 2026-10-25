import { useEffect, useState } from "react";
import Dashboard from "./pages/Dashboard";
import History from "./pages/History";
import Memories from "./pages/Memories";
import Settings from "./pages/Settings";
import { get } from "./api";

type Page = "dashboard" | "history" | "memories" | "settings";

const NAV: { id: Page; label: string; icon: string }[] = [
  { id: "dashboard", label: "Dashboard", icon: "◉" },
  { id: "history", label: "History", icon: "≡" },
  { id: "memories", label: "Memories", icon: "🧠" },
  { id: "settings", label: "Settings", icon: "⚙" },
];

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
    <div className="flex h-screen w-screen">
      {/* sidebar */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-white/8 bg-[#0e1118]">
        <div className="px-5 pt-6 pb-4">
          <div className="text-[15px] font-semibold tracking-tight">DBMS-EL</div>
          <div className="text-[11px] text-slate-500">Personal memory layer</div>
        </div>
        <nav className="flex-1 space-y-1 px-3">
          {NAV.map((n) => (
            <button
              key={n.id}
              onClick={() => setPage(n.id)}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-[13px] transition ${
                page === n.id
                  ? "bg-blue-500/15 text-blue-300"
                  : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
              }`}
            >
              <span className="w-4 text-center">{n.icon}</span>
              {n.label}
            </button>
          ))}
        </nav>
        <div className="border-t border-white/8 px-5 py-4 text-[11px] leading-relaxed text-slate-500">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-2 w-2 rounded-full ${online ? "bg-emerald-400" : "bg-red-400"}`}
            />
            backend {online ? "online" : "offline"}
          </div>
          {status && (
            <>
              <div className="mt-1 flex items-center gap-2">
                <span
                  className={`inline-block h-2 w-2 rounded-full ${status.embed_ready ? "bg-emerald-400" : "bg-amber-400"}`}
                />
                embed · {status.embed_device}
              </div>
              <div className="mt-1 truncate">{status.model}</div>
            </>
          )}
        </div>
      </aside>

      {/* content */}
      <main className="flex-1 overflow-y-auto">
        {page === "dashboard" && <Dashboard />}
        {page === "history" && <History />}
        {page === "memories" && <Memories />}
        {page === "settings" && <Settings />}
      </main>
    </div>
  );
}
