import { useEffect, useState } from "react";
import { get, fmtAgo, fmtDuration, API } from "../api";

interface Stats {
  counts: {
    events_today: number;
    screenshots_24h: number;
    observations: number;
    memories: number;
    queries: number;
    dwell_sec_24h: number;
  };
  top_domains: { domain: string; visits: number; total_sec: number }[];
  hourly: { hour: string; events: number; seconds: number }[];
  recent_memories: { id: string; content: string; importance: number; updated_at: string }[];
  recent_events: { id: number; domain: string; title: string; time_spent_sec: number; visited_at: string }[];
  topics: { topic: string; uses: number }[];
  pipeline: { last_screenshot: string | null; last_observation: string | null; last_memory: string | null; last_query: string | null };
}

function Card({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="rounded-xl border border-white/8 bg-[#12151d] p-4">
      <div className="text-[11px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    const load = () => get<Stats>("/api/stats").then(setStats).catch(() => {});
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);

  if (!stats)
    return <div className="p-8 text-sm text-slate-500">Loading dashboard… (backend at {API})</div>;

  const maxHourly = Math.max(1, ...stats.hourly.map((h) => h.seconds));

  return (
    <div className="mx-auto max-w-5xl p-8">
      <h1 className="text-lg font-semibold">Dashboard</h1>
      <p className="mb-6 text-xs text-slate-500">
        last screenshot {fmtAgo(stats.pipeline.last_screenshot)} · last observation{" "}
        {fmtAgo(stats.pipeline.last_observation)} · last memory {fmtAgo(stats.pipeline.last_memory)}
      </p>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card label="Events · 24h" value={stats.counts.events_today} sub={fmtDuration(stats.counts.dwell_sec_24h) + " dwell"} />
        <Card label="Screenshots · 24h" value={stats.counts.screenshots_24h} />
        <Card label="Observations" value={stats.counts.observations} />
        <Card label="Memories" value={stats.counts.memories} sub={`${stats.counts.queries} queries served`} />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-5">
        {/* activity chart */}
        <div className="rounded-xl border border-white/8 bg-[#12151d] p-4 lg:col-span-3">
          <div className="mb-3 text-[11px] uppercase tracking-wider text-slate-500">
            Browsing dwell · last 24 h (minutes)
          </div>
          {stats.hourly.length === 0 ? (
            <div className="py-8 text-center text-xs text-slate-600">No events yet</div>
          ) : (
            <div className="flex h-28 items-end gap-1">
              {stats.hourly.map((h) => (
                <div key={h.hour} className="group relative flex-1">
                  <div
                    className="w-full rounded-t bg-blue-500/60 transition group-hover:bg-blue-400"
                    style={{ height: `${Math.max(4, (h.seconds / maxHourly) * 100)}%` }}
                  />
                  <div className="pointer-events-none absolute -top-8 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded bg-black/80 px-2 py-1 text-[10px] group-hover:block">
                    {new Date(h.hour).toLocaleTimeString([], { hour: "2-digit" })} ·{" "}
                    {Math.round(h.seconds / 60)}m
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="mt-4 space-y-1.5">
            {stats.top_domains.map((d) => (
              <div key={d.domain} className="flex items-center gap-3 text-xs">
                <div className="w-40 truncate text-slate-300">{d.domain}</div>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/5">
                  <div
                    className="h-full rounded-full bg-sky-500/70"
                    style={{ width: `${(d.total_sec / Math.max(1, stats.top_domains[0].total_sec)) * 100}%` }}
                  />
                </div>
                <div className="w-16 text-right tabular-nums text-slate-500">{fmtDuration(d.total_sec)}</div>
              </div>
            ))}
          </div>
        </div>

        {/* topics + memories */}
        <div className="space-y-6 lg:col-span-2">
          <div className="rounded-xl border border-white/8 bg-[#12151d] p-4">
            <div className="mb-2 text-[11px] uppercase tracking-wider text-slate-500">Recent topics</div>
            <div className="flex flex-wrap gap-1.5">
              {stats.topics.length === 0 && <span className="text-xs text-slate-600">—</span>}
              {stats.topics.map((t) => (
                <span key={t.topic} className="rounded-full bg-blue-500/12 px-2.5 py-1 text-[11px] text-blue-300">
                  {t.topic} · {t.uses}
                </span>
              ))}
            </div>
          </div>
          <div className="rounded-xl border border-white/8 bg-[#12151d] p-4">
            <div className="mb-2 text-[11px] uppercase tracking-wider text-slate-500">Latest memories</div>
            <div className="space-y-2">
              {stats.recent_memories.length === 0 && (
                <div className="text-xs text-slate-600">No memories yet</div>
              )}
              {stats.recent_memories.map((m) => (
                <div key={m.id} className="rounded-lg bg-white/4 p-2.5">
                  <div className="text-xs leading-snug text-slate-300">{m.content}</div>
                  <div className="mt-1 flex items-center justify-between text-[10px] text-slate-500">
                    <span>importance {(m.importance * 100).toFixed(0)}%</span>
                    <span>{fmtAgo(m.updated_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* recent activity */}
      <div className="mt-6 rounded-xl border border-white/8 bg-[#12151d] p-4">
        <div className="mb-2 text-[11px] uppercase tracking-wider text-slate-500">Recent activity</div>
        <div className="divide-y divide-white/5">
          {stats.recent_events.length === 0 && <div className="py-3 text-xs text-slate-600">Nothing yet — start browsing with the extension enabled.</div>}
          {stats.recent_events.map((e) => (
            <div key={e.id} className="flex items-center gap-3 py-2 text-xs">
              <span className="w-32 shrink-0 truncate text-slate-500">{fmtAgo(e.visited_at)}</span>
              <span className="w-44 shrink-0 truncate font-medium text-slate-300">{e.domain}</span>
              <span className="flex-1 truncate text-slate-400">{e.title}</span>
              <span className="shrink-0 rounded bg-white/5 px-2 py-0.5 tabular-nums text-slate-400">
                {fmtDuration(e.time_spent_sec)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
