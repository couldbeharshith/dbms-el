import { useEffect, useState } from "react";
import { del, get, post } from "../api";
import Icon from "./Icon";

interface Item { id: string; phrase: string }

const errText = (e: unknown) => {
  const raw = String((e as Error).message);
  try { return JSON.parse(raw.replace(/^\d+\s*/, "")).detail ?? raw; } catch { return raw; }
};

async function put(path: string, body: unknown) {
  const r = await fetch(`http://localhost:8000${path}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
}

function Row({ item, onChanged, onError }: { item: Item; onChanged: () => void; onError: (m: string) => void }) {
  const [val, setVal] = useState(item.phrase);
  useEffect(() => setVal(item.phrase), [item.phrase]);
  const dirty = val.trim().toLowerCase() !== item.phrase;

  const save = async () => {
    if (!dirty) return;
    try { await put(`/api/blacklist/${item.id}`, { phrase: val }); onError(""); onChanged(); }
    catch (e) { onError(errText(e)); setVal(item.phrase); }
  };

  return (
    <div className="group flex items-center gap-2 rounded-xl border border-white/8 bg-black/20 px-3 py-1.5 focus-within:border-blue-400/50">
      <Icon name="ban" className="h-4 w-4 shrink-0 text-red-400/80" />
      <input
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && save()}
        className="min-w-0 flex-1 bg-transparent py-1 text-[13px] outline-none"
      />
      {dirty && (
        <button onClick={save} title="Save" className="rounded-lg p-1.5 text-emerald-400 hover:bg-emerald-500/10">
          <Icon name="check" />
        </button>
      )}
      <button
        onClick={() => del(`/api/blacklist/${item.id}`).then(onChanged)}
        title="Remove"
        className="rounded-lg p-1.5 text-slate-500 hover:bg-red-500/10 hover:text-red-400"
      >
        <Icon name="trash" />
      </button>
    </div>
  );
}

export default function Blacklist() {
  const [items, setItems] = useState<Item[]>([]);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState("");

  const load = () => get<{ items: Item[] }>("/api/blacklist").then((r) => setItems(r.items)).catch(() => {});
  useEffect(() => { load(); }, []);

  const add = async () => {
    if (!draft.trim()) return;
    try { await post("/api/blacklist", { phrase: draft }); setDraft(""); setErr(""); load(); }
    catch (e) { setErr(errText(e)); }
  };

  return (
    <div className="surface rounded-2xl p-5">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <Icon name="ban" className="h-4 w-4 text-red-400" /> Screenshot blacklist
      </div>
      <p className="mb-3 mt-1 text-[11px] text-slate-500">
        No screenshot is taken while the active window title contains any of these (case-insensitive).
        Try “bank”, “youtube”, “gmail” or a game name.
      </p>

      <div className="mb-3 flex gap-2">
        <input
          className="input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
          placeholder="Add a word or phrase…"
        />
        <button onClick={add} className="btn-accent flex shrink-0 items-center gap-1.5 rounded-xl px-4 text-sm">
          <Icon name="plus" /> Add
        </button>
      </div>
      {err && <div className="mb-2 text-xs text-red-400">{err}</div>}

      <div className="space-y-2">
        {items.map((i) => <Row key={i.id} item={i} onChanged={load} onError={setErr} />)}
        {items.length === 0 && <div className="py-3 text-center text-xs text-slate-600">Nothing blacklisted yet.</div>}
      </div>
    </div>
  );
}
