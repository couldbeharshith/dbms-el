export const API = "http://localhost:8000";

async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
  return r.json() as Promise<T>;
}

export const get = <T,>(path: string) => fetch(`${API}${path}`).then((r) => handle<T>(r));

export async function post<T = unknown>(path: string, body?: unknown): Promise<T> {
  return fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => handle<T>(r));
}

export async function patch<T = unknown>(path: string, body: unknown): Promise<T> {
  return fetch(`${API}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => handle<T>(r));
}

export async function del<T = unknown>(path: string): Promise<T> {
  return fetch(`${API}${path}`, { method: "DELETE" }).then((r) => handle<T>(r));
}

export const fmtTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

export const fmtAgo = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

export const fmtDuration = (sec: number | null | undefined) => {
  if (!sec) return "0s";
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  return m < 60 ? `${m}m ${sec % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
};
