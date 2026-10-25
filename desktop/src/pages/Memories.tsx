import { useEffect, useState } from "react";
import { get, patch, del, post, fmtAgo, fmtTime } from "../api";

interface MemoryRow {
  id: string;
  content: string;
  importance: number;
  hit_count: number;
  last_retrieved_at: string | null;
  created_at: string;
  updated_at: string;
  metadata: { topics?: string[]; source?: string };
  evidence_count: number;
}

interface EvidenceRow {
  observation_id: string;
  observation_kind: string;
  observation_caption: string | null;
  observed_at: string;
  screenshot_id: string | null;
  source_app: string | null;
  domain: string | null;
  url: string | null;
  page_title: string | null;
  time_spent_sec: number | null;
}

function Evidence({ memoryId }: { memoryId: string }) {
  const [rows, setRows] = useState<EvidenceRow[] | null>(null);
  useEffect(() => {
    get<{ evidence: EvidenceRow[] }>(`/api/memories/${memoryId}/evidence`)
      .then((r) => setRows(r.evidence))
      .catch(() => setRows([]));
  }, [memoryId]);

  if (rows === null) return <div className="px-3 py-2 text-xs text-slate-600">loading evidence…</div>;
  if (rows.length === 0) return <div className="px-3 py-2 text-xs text-slate-600">no linked observations</div>;
  return (
    <div className="space-y-1.5 px-3 py-2">
      {rows.map((e) => (
        <div key={e.observation_id} className="rounded-lg bg-black/25 p-2.5 text-[11px] leading-snug text-slate-400">
          <div className="mb-1 flex items-center gap-2">
            <span className={`rounded px-1.5 py-0.5 text-[9px] uppercase ${e.observation_kind === "screenshot" ? "bg-purple-500/15 text-purple-300" : "bg-sky-500/15 text-sky-300"}`}>
              {e.observation_kind}
            </span>
            <span className="text-slate-600">{fmtTime(e.observed_at)}</span>
            {e.screenshot_id && <span className="text-slate-600">· {e.source_app || "screenshot"}</span>}
            {e.domain && <span className="truncate text-slate-600">· {e.domain}{e.time_spent_sec ? ` (${e.time_spent_sec}s)` : ""}</span>}
          </div>
          {e.observation_caption}
        </div>
      ))}
    </div>
  );
}

function MemoryCard({ m, onDelete }: { m: MemoryRow; onDelete: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(m.content);
  const [importance, setImportance] = useState(m.importance);

  const save = async () => {
    await patch(`/api/memories/${m.id}`, { content: draft, importance });
    setEditing(false);
    m.content = draft;
    m.importance = importance;
  };

  return (
    <div className="rounded-xl border border-white/8 bg-[#12151d] p-4">
      {editing ? (
        <div>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            className="w-full rounded-lg border border-white/10 bg-black/30 p-2.5 text-[13px] text-slate-200 focus:border-blue-500/50 focus:outline-none"
          />
          <div className="mt-2 flex items-center gap-3">
            <label className="flex items-center gap-2 text-[11px] text-slate-500">
              importance
              <input
                type="range" min={0} max={1} step={0.05} value={importance}
                onChange={(e) => setImportance(Number(e.target.value))}
                className="accent-blue-500"
              />
              {(importance * 100).toFixed(0)}%
            </label>
            <div className="flex-1" />
            <button onClick={save} className="rounded-lg bg-blue-500/20 px-3 py-1.5 text-xs text-blue-300 hover:bg-blue-500/30">
              Save (re-embeds)
            </button>
            <button onClick={() => setEditing(false)} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs hover:bg-white/5">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="text-[13px] leading-relaxed text-slate-200">{m.content}</div>
      )}

      <div className="mt-3 flex items-center gap-3">
        <div className="h-1.5 w-32 overflow-hidden rounded-full bg-white/5">
          <div className="h-full rounded-full bg-gradient-to-r from-sky-500 to-blue-500" style={{ width: `${m.importance * 100}%` }} />
        </div>
        <span className="text-[10px] text-slate-500">importance {(m.importance * 100).toFixed(0)}%</span>
        {m.metadata.topics?.map((t) => (
          <span key={t} className="rounded-full bg-blue-500/12 px-2 py-0.5 text-[10px] text-blue-300">{t}</span>
        ))}
        <div className="flex-1" />
        <span className="text-[10px] text-slate-600">
          {m.evidence_count} evidence · used {m.hit_count}× · {fmtAgo(m.updated_at)}
        </span>
      </div>

      <div className="mt-2.5 flex items-center gap-2 text-[11px]">
        <button onClick={() => setOpen(!open)} className="rounded-md border border-white/10 px-2.5 py-1 text-slate-400 hover:bg-white/5">
          {open ? "Hide evidence" : `Evidence (${m.evidence_count})`}
        </button>
        <button onClick={() => setEditing(true)} className="rounded-md border border-white/10 px-2.5 py-1 text-slate-400 hover:bg-white/5">
          Edit
        </button>
        <button
          onClick={() => { if (confirm("Delete this memory?")) onDelete(m.id); }}
          className="rounded-md border border-red-500/25 px-2.5 py-1 text-red-300 hover:bg-red-500/10"
        >
          Delete
        </button>
      </div>

      {open && <Evidence memoryId={m.id} />}
    </div>
  );
}

export default function Memories() {
  const [memories, setMemories] = useState<MemoryRow[]>([]);
  const [busy, setBusy] = useState(false);

  const load = () =>
    get<{ memories: MemoryRow[] }>("/api/memories").then((r) => setMemories(r.memories)).catch(() => {});

  useEffect(() => {
    load();
  }, []);

  const remove = async (id: string) => {
    await del(`/api/memories/${id}`);
    load();
  };

  const consolidate = async () => {
    setBusy(true);
    try {
      await post("/api/consolidate");
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl p-8">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Memories</h1>
        <button
          onClick={consolidate}
          disabled={busy}
          className="rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 py-1.5 text-xs text-blue-300 hover:bg-blue-500/20 disabled:opacity-50"
        >
          {busy ? "Consolidating…" : "Consolidate now"}
        </button>
      </div>

      {memories.length === 0 && (
        <div className="rounded-xl border border-white/8 bg-[#12151d] p-8 text-center text-xs text-slate-600">
          No memories yet. Browse a topic for a while (or seed demo data), then click "Consolidate now".
        </div>
      )}

      <div className="space-y-3">
        {memories.map((m) => (
          <MemoryCard key={m.id} m={m} onDelete={remove} />
        ))}
      </div>
    </div>
  );
}
