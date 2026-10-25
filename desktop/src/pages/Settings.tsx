import { useEffect, useState } from "react";
import { get, patch, post, del } from "../api";

interface Settings {
  tracking_enabled: boolean;
  screenshots_enabled: boolean;
  screenshot_interval_sec: number;
  location_enabled: boolean;
  injection_enabled: boolean;
  retention_days: number;
}

function Toggle({ label, hint, value, onChange }: { label: string; hint?: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between rounded-xl border border-white/8 bg-[#12151d] px-4 py-3.5">
      <div>
        <div className="text-[13px] text-slate-200">{label}</div>
        {hint && <div className="text-[11px] text-slate-500">{hint}</div>}
      </div>
      <button
        onClick={() => onChange(!value)}
        className={`relative h-6 w-11 rounded-full transition ${value ? "bg-blue-500" : "bg-white/10"}`}
      >
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${value ? "left-[22px]" : "left-0.5"}`} />
      </button>
    </div>
  );
}

export default function Settings() {
  const [s, setS] = useState<Settings | null>(null);
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    get<{ settings: Settings }>("/api/settings").then((r) => setS(r.settings)).catch(() => setMsg("backend offline"));
    get<Record<string, unknown>>("/api/status").then(setStatus).catch(() => {});
  }, []);

  if (!s) return <div className="p-8 text-sm text-slate-500">{msg || "Loading…"}</div>;

  const update = async (patchBody: Partial<Settings>) => {
    setS({ ...s, ...patchBody });
    const r = await patch<{ settings: Settings }>("/api/settings", patchBody);
    setS(r.settings);
  };

  const clear = async (scope: string, label: string) => {
    if (!confirm(`Delete ALL ${label}? This cannot be undone.`)) return;
    await del(`/api/data/${scope}`);
    setMsg(`Deleted ${label}.`);
  };

  return (
    <div className="mx-auto max-w-2xl p-8">
      <h1 className="mb-6 text-lg font-semibold">Settings</h1>

      <div className="space-y-3">
        <Toggle
          label="Browsing tracking"
          hint="Chrome extension records page visits + dwell time"
          value={s.tracking_enabled}
          onChange={(v) => update({ tracking_enabled: v })}
        />
        <Toggle
          label="Screenshot capture"
          hint="Windows app takes periodic encrypted screenshots"
          value={s.screenshots_enabled}
          onChange={(v) => update({ screenshots_enabled: v })}
        />
        <Toggle
          label="Context injection"
          hint="Append retrieved memories to chatbot messages (chatgpt.com, claude.ai, gemini)"
          value={s.injection_enabled}
          onChange={(v) => update({ injection_enabled: v })}
        />
        <Toggle
          label="Attach coarse location (GeoIP)"
          hint="Off by default. When on, browsing events store an approximate PostGIS point."
          value={s.location_enabled}
          onChange={(v) => update({ location_enabled: v })}
        />

        <div className="rounded-xl border border-white/8 bg-[#12151d] px-4 py-3.5">
          <div className="flex items-center justify-between">
            <div className="text-[13px] text-slate-200">Screenshot interval</div>
            <div className="text-xs tabular-nums text-blue-300">{s.screenshot_interval_sec}s</div>
          </div>
          <input
            type="range" min={10} max={300} step={5}
            value={s.screenshot_interval_sec}
            onChange={(e) => setS({ ...s, screenshot_interval_sec: Number(e.target.value) })}
            onMouseUp={() => update({ screenshot_interval_sec: s.screenshot_interval_sec })}
            onTouchEnd={() => update({ screenshot_interval_sec: s.screenshot_interval_sec })}
            className="mt-2 w-full accent-blue-500"
          />
          <div className="text-[10px] text-slate-600">10s – 300s</div>
        </div>

        <div className="rounded-xl border border-white/8 bg-[#12151d] px-4 py-3.5">
          <div className="flex items-center justify-between">
            <div className="text-[13px] text-slate-200">Retention</div>
            <div className="text-xs tabular-nums text-blue-300">{s.retention_days} days</div>
          </div>
          <input
            type="range" min={1} max={90}
            value={s.retention_days}
            onChange={(e) => setS({ ...s, retention_days: Number(e.target.value) })}
            onMouseUp={() => update({ retention_days: s.retention_days })}
            onTouchEnd={() => update({ retention_days: s.retention_days })}
            className="mt-2 w-full accent-blue-500"
          />
          <div className="text-[10px] text-slate-600">older browsing events / screenshots / queries auto-deleted</div>
        </div>

        <div className="flex items-center justify-between rounded-xl border border-white/8 bg-[#12151d] px-4 py-3.5">
          <div className="text-[13px] text-slate-200">Capture a screenshot now</div>
          <button
            onClick={() => window.desktop?.captureNow().then(() => setMsg("Captured."))}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs hover:bg-white/5"
          >
            Capture
          </button>
        </div>
      </div>

      <h2 className="mb-3 mt-8 text-sm font-semibold text-slate-300">Privacy / data controls</h2>
      <div className="grid grid-cols-2 gap-3 text-xs">
        <button onClick={() => clear("browsing", "browsing history")} className="rounded-xl border border-white/10 bg-[#12151d] px-4 py-3 hover:border-red-500/30 hover:text-red-300">
          Clear browsing history
        </button>
        <button onClick={() => clear("screenshots", "screenshots + observations")} className="rounded-xl border border-white/10 bg-[#12151d] px-4 py-3 hover:border-red-500/30 hover:text-red-300">
          Clear screenshots
        </button>
        <button onClick={() => clear("memories", "memories")} className="rounded-xl border border-white/10 bg-[#12151d] px-4 py-3 hover:border-red-500/30 hover:text-red-300">
          Clear memories
        </button>
        <button onClick={() => clear("all", "data (events, screenshots, observations, memories, queries)")} className="rounded-xl border border-red-500/30 bg-red-500/5 px-4 py-3 text-red-300 hover:bg-red-500/10">
          Delete everything
        </button>
      </div>

      <h2 className="mb-3 mt-8 text-sm font-semibold text-slate-300">System</h2>
      <pre className="overflow-x-auto rounded-xl border border-white/8 bg-[#12151d] p-4 text-[11px] leading-relaxed text-slate-400">
        {JSON.stringify(status, null, 2)}
      </pre>

      {msg && <div className="mt-4 text-center text-xs text-emerald-400">{msg}</div>}
    </div>
  );
}
