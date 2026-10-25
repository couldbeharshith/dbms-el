import { useEffect, useState } from "react";
import { get, del, fmtTime, API } from "../api";

export interface ShotRow {
  id: string;
  captured_at: string;
  width?: number | null;
  height?: number | null;
  source_app?: string | null;
  size_bytes?: number | null;
  caption?: string | null;
  topics?: string[] | null;
  processed?: boolean;
  url?: string | null;
  ocr_text?: string | null;
}

interface Detail extends ShotRow {}

interface SimilarRow {
  id: string;
  captured_at: string;
  caption: string | null;
  sim: number;
}

export default function ShotModal({ shot, onClose }: { shot: ShotRow; onClose: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [similar, setSimilar] = useState<SimilarRow[] | null>(null);

  useEffect(() => {
    get<Detail>(`/api/screenshots/${shot.id}`).then(setDetail).catch(() => setDetail(null));
    get<{ similar: SimilarRow[] }>(`/api/screenshots/${shot.id}/similar`)
      .then((r) => setSimilar(r.similar))
      .catch(() => setSimilar([]));
  }, [shot.id]);

  const d = detail ?? shot;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-8" onClick={onClose}>
      <div
        className="max-h-full w-full max-w-4xl overflow-y-auto rounded-xl border border-white/10 bg-[#12151d] p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-medium">{fmtTime(d.captured_at)}</div>
            <div className="text-xs text-slate-500">{d.source_app || "unknown app"}</div>
            {d.url && (
              <a
                href={d.url}
                target="_blank"
                className="mt-0.5 block max-w-xl truncate text-xs text-blue-400 hover:text-blue-300"
              >
                {d.url}
              </a>
            )}
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
            Observation {d.processed === false ? "· pending processing" : ""}
          </div>
          <div className="mt-1 text-sm leading-snug text-slate-300">{d.caption || "—"}</div>
          {d.ocr_text && (
            <div className="mt-2 border-t border-white/8 pt-2">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">OCR (decrypted on view)</div>
              <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-slate-400">
                {d.ocr_text}
              </pre>
            </div>
          )}
          {d.topics && d.topics.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {d.topics.map((t) => (
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
