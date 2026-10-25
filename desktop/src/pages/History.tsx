import { useEffect, useState } from "react";
import { get, del, fmtTime, fmtDuration, API } from "../api";

interface EventRow {
  id: number;
  url: string;
  domain: string;
  title: string | null;
  visited_at: string;
  time_spent_sec: number;
  session_id: string | null;
  location: string | null;
}

interface ShotRow {
  id: string;
  captured_at: string;
  width: number | null;
  height: number | null;
  source_app: string | null;
  size_bytes: number | null;
  caption: string | null;
  topics: string[] | null;
  processed: boolean;
}

interface SimilarRow {
  id: string;
  captured_at: string;
  caption: string | null;
  sim: number;
}

function ShotModal({ shot, onClose }: { shot: ShotRow; onClose: () => void }) {
  const [similar, setSimilar] = useState<SimilarRow[] | null>(null);

  useEffect(() => {
    get<{ similar: SimilarRow[] }>(`/api/screenshots/${shot.id}/similar`)
      .then((r) => setSimilar(r.similar))
      .catch(() => setSimilar([]));
  }, [shot.id]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-8" onClick={onClose}>
      <div
        className="max-h-full w-full max-w-4xl overflow-y-auto rounded-xl border border-white/10 bg-[#12151d] p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-medium">{fmtTime(shot.captured_at)}</div>
            <div className="text-xs text-slate-500">{shot.source_app || "unknown app"}</div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => del(`/api/screenshots/${shot.id}`).then(onClose)}
              className="rounded-lg border border-red-500/30 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/10"
            >
              Delete
            </button>
            <button onClick={onClose} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs hover:bg-white/5">
              Close
            </button>
          </div>
        </div>

        <img
          src={`${API}/api/screenshots/${shot.id}/image`}
          alt="screenshot"
          className="w-full rounded-lg border border-white/10"
        />

        <div className="mt-4 rounded-lg bg-white/4 p-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">
            Observation {shot.processed ? "" : "· pending processing"}
          </div>
          <div className="mt-1 text-sm leading-snug text-slate-300">
            {shot.caption || "—"}
          </div>
          {shot.topics && shot.topics.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {shot.topics.map((t) => (
                <span key={t} className="rounded-full bg-blue-500/12 px-2 py-0.5 text-[11px] text-blue-300">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="mt-4">
          <div className="mb-2 text-[10px] uppercase tracking-wider text-slate-500">
            Visually similar screenshots (cross-modal vector search)
          </div>
          {similar === null ? (
            <div className="text-xs text-slate-600">…</div>
          ) : similar.length === 0 ? (
            <div className="text-xs text-slate-600">No embedded screenshots yet</div>
          ) : (
            <div className="grid grid-cols-6 gap-2">
              {similar.map((s) => (
                <div key={s.id} className="overflow-hidden rounded-lg border border-white/10" title={s.caption || ""}>
                  <img src={`${API}/api/screenshots/${s.id}/image`} alt="" className="h-16 w-full object-cover opacity-80" />
                  <div className="bg-black/40 px-1 py-0.5 text-center text-[10px] text-slate-400">
                    {(s.sim * 100).toFixed(0)}%
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function History() {
  const [tab, setTab] = useState<"browsing" | "screenshots">("browsing");
  const [events, setEvents] = useState<EventRow[]>([]);
  const [shots, setShots] = useState<ShotRow[]>([]);
  const [selected, setSelected] = useState<ShotRow | null>(null);
  const [domainFilter, setDomainFilter] = useState("");

  useEffect(() => {
    const load = () => {
      const q = domainFilter ? `?domain=${encodeURIComponent(domainFilter)}&limit=200` : "?limit=200";
      get<{ events: EventRow[] }>(`/api/events${q}`).then((r) => setEvents(r.events)).catch(() => {});
      get<{ screenshots: ShotRow[] }>("/api/screenshots?limit=120")
        .then((r) => setShots(r.screenshots))
        .catch(() => {});
    };
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [domainFilter]);

  const domains = [...new Set(events.map((e) => e.domain))].slice(0, 50);

  return (
    <div className="mx-auto max-w-5xl p-8">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">History</h1>
        <div className="flex rounded-lg border border-white/10 p-0.5 text-xs">
          {(["browsing", "screenshots"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-md px-3 py-1.5 capitalize ${tab === t ? "bg-blue-500/15 text-blue-300" : "text-slate-400 hover:text-slate-200"}`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {tab === "browsing" && (
        <>
          <select
            value={domainFilter}
            onChange={(e) => setDomainFilter(e.target.value)}
            className="mb-3 rounded-lg border border-white/10 bg-[#12151d] px-3 py-1.5 text-xs text-slate-300"
          >
            <option value="">All domains</option>
            {domains.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
          <div className="overflow-hidden rounded-xl border border-white/8 bg-[#12151d]">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/8 text-left text-[10px] uppercase tracking-wider text-slate-500">
                  <th className="px-4 py-2.5">When</th>
                  <th className="px-4 py-2.5">Domain</th>
                  <th className="px-4 py-2.5">Page</th>
                  <th className="px-4 py-2.5 text-right">Time</th>
                  <th className="px-4 py-2.5 text-right">Location</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {events.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-6 text-center text-slate-600">No browsing events — is the Chrome extension enabled?</td></tr>
                )}
                {events.map((e) => (
                  <tr key={e.id} className="hover:bg-white/3">
                    <td className="px-4 py-2 text-slate-500">{fmtTime(e.visited_at)}</td>
                    <td className="px-4 py-2 font-medium text-slate-300">{e.domain}</td>
                    <td className="max-w-md truncate px-4 py-2 text-slate-400">
                      <a href={e.url} target="_blank" className="hover:text-blue-300">{e.title || e.url}</a>
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-slate-400">{fmtDuration(e.time_spent_sec)}</td>
                    <td className="px-4 py-2 text-right font-mono text-[10px] text-slate-600">{e.location || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "screenshots" && (
        <>
          <p className="mb-3 text-[11px] text-slate-500">
            Images are AES-256-GCM encrypted on disk and decrypted on demand. Hover to reveal.
          </p>
          {shots.length === 0 ? (
            <div className="rounded-xl border border-white/8 bg-[#12151d] p-8 text-center text-xs text-slate-600">
              No screenshots yet — enable capture in Settings (Windows app takes them).
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-3 md:grid-cols-4">
              {shots.map((s) => (
                <button
                  key={s.id}
                  onClick={() => setSelected(s)}
                  className="group overflow-hidden rounded-lg border border-white/8 bg-[#12151d] text-left transition hover:border-blue-500/40"
                >
                  <img
                    src={`${API}/api/screenshots/${s.id}/image`}
                    alt=""
                    className="h-28 w-full object-cover blur-md transition group-hover:blur-0"
                  />
                  <div className="p-2">
                    <div className="line-clamp-2 h-8 text-[10px] leading-4 text-slate-400">
                      {s.caption || (s.processed ? "—" : "processing…")}
                    </div>
                    <div className="mt-1 flex items-center justify-between text-[10px] text-slate-600">
                      <span>{fmtTime(s.captured_at)}</span>
                      <span className={`h-1.5 w-1.5 rounded-full ${s.processed ? "bg-emerald-400" : "bg-amber-400"}`} />
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {selected && <ShotModal shot={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
