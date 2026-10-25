import { useEffect, useRef, useState } from "react";
import { get, fmtTime } from "../api";
import ShotModal, { ShotRow } from "../components/ShotModal";

interface RecallResult {
  observation_id: string;
  screenshot_id: string | null;
  caption: string | null;
  topics: string[] | null;
  observed_at: string;
  url: string | null;
  source_app: string | null;
  score: number;
  text_sim: number;
  image_sim: number;
}

export default function Recall() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<RecallResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<ShotRow | null>(null);
  const timer = useRef<number | null>(null);
  const lastQuery = useRef("");

  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    const query = q.trim();
    if (!query) {
      setResults(null);
      return;
    }
    timer.current = window.setTimeout(async () => {
      setSearching(true);
      try {
        const r = await get<{ results: RecallResult[] }>(
          `/api/recall?q=${encodeURIComponent(query)}&limit=36`
        );
        setResults(r.results);
        lastQuery.current = query;
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 350);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [q]);

  return (
    <div className="mx-auto max-w-5xl p-8">
      <h1 className="text-lg font-semibold">Recall</h1>
      <p className="mb-4 text-xs text-slate-500">
        Semantic search across every screenshot, caption and OCR text — describe what you remember
        ("code", "laptop prices", "python project") and open the screenshot.
      </p>

      <input
        autoFocus
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search your screen history…"
        className="w-full rounded-xl border border-white/10 bg-[#12151d] px-4 py-3 text-sm text-slate-200 placeholder-slate-600 focus:border-blue-500/50 focus:outline-none"
      />

      {results === null && !searching && (
        <div className="mt-10 text-center text-xs text-slate-600">Type above to search.</div>
      )}
      {searching && results === null && (
        <div className="mt-10 text-center text-xs text-slate-500">Searching…</div>
      )}
      {results !== null && results.length === 0 && !searching && (
        <div className="mt-10 text-center text-xs text-slate-600">
          Nothing matched “{lastQuery.current}”.
        </div>
      )}

      {results !== null && results.length > 0 && (
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3">
          {results.map((r) => (
            <button
              key={r.observation_id}
              onClick={() =>
                r.screenshot_id &&
                setSelected({
                  id: r.screenshot_id,
                  captured_at: r.observed_at,
                  source_app: r.source_app,
                  caption: r.caption,
                  topics: r.topics,
                  processed: true,
                })
              }
              className="group overflow-hidden rounded-xl border border-white/8 bg-[#12151d] text-left transition hover:border-blue-500/40"
            >
              {r.screenshot_id ? (
                <img
                  src={`http://localhost:8000/api/screenshots/${r.screenshot_id}/image`}
                  alt=""
                  className="h-32 w-full object-cover"
                />
              ) : (
                <div className="flex h-32 items-center justify-center text-2xl text-slate-700">🗒</div>
              )}
              <div className="p-2.5">
                <div className="line-clamp-2 text-[11px] leading-4 text-slate-300">{r.caption || "—"}</div>
                <div className="mt-1.5 flex items-center justify-between text-[10px] text-slate-600">
                  <span>{fmtTime(r.observed_at)}</span>
                  <span className="rounded bg-blue-500/12 px-1.5 py-0.5 text-blue-300">
                    {(r.score * 100).toFixed(0)}%
                  </span>
                </div>
                {r.url && (
                  <div className="mt-1 truncate text-[10px] text-blue-400/70">{r.url}</div>
                )}
              </div>
            </button>
          ))}
        </div>
      )}

      {selected && <ShotModal shot={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
