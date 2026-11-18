import { useEffect, useRef, useState } from "react";
import { get, post } from "../api";
import Icon from "./Icon";

type Mode = "signin" | "signup" | "forgot";

/** Full-window blur overlay + modal. Shown on launch and after 5 min idle. */
export default function LockScreen({
  hasAccounts,
  lastUser,
  onUnlock,
}: {
  hasAccounts: boolean;
  lastUser: string;
  onUnlock: (username: string) => void;
}) {
  const [mode, setMode] = useState<Mode>(hasAccounts ? "signin" : "signup");
  const [username, setUsername] = useState(lastUser);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  const pinRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    pinRef.current?.focus();
  }, [mode]);

  const submit = async (e?: React.FormEvent, pinValue: string = pin) => {
    e?.preventDefault();
    if (busy) return;
    setError("");
    setInfo("");
    setBusy(true);
    try {
      if (mode === "forgot") {
        const r = await post<{ message: string }>("/api/auth/forgot", { username });
        setInfo(r.message);
        setMode("signin");
      } else {
        await post(mode === "signup" ? "/api/auth/signup" : "/api/auth/login", { username, pin: pinValue });
        if (mode === "signup") await post("/api/auth/login", { username, pin: pinValue });
        onUnlock(username.trim());
      }
    } catch (err) {
      const raw = String((err as Error).message);
      let msg = raw;
      try {
        msg = JSON.parse(raw.replace(/^\d+\s*/, "")).detail ?? raw;
      } catch { /* keep raw */ }
      setError(typeof msg === "string" ? msg : "Something went wrong");
      setPin(""); // let the user retry
      pinRef.current?.focus();
    } finally {
      setBusy(false);
    }
  };

  const title = { signin: "Unlock Keepsake", signup: "Create your account", forgot: "Forgot PIN" }[mode];

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-[#0b0e13]/55 backdrop-blur-xl">
      <form onSubmit={submit} className="surface pop-in w-[380px] rounded-2xl p-7">
        <div className="mb-5 flex items-center gap-3">
          <div className="btn-accent flex h-11 w-11 items-center justify-center rounded-xl">
            <Icon name="lock" className="h-5 w-5" />
          </div>
          <div>
            <div className="text-xl font-bold tracking-tight">{title}</div>
            <div className="text-xs text-slate-500">
              {mode === "signup" ? "Username + 6-digit PIN" : mode === "forgot" ? "Resets your PIN to 000000" : "Enter your 6-digit PIN"}
            </div>
          </div>
        </div>

        <label className="mb-1 block text-xs font-semibold text-slate-500">Username</label>
        <input
          className="input mb-3"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          placeholder="your_username"
        />

        {mode !== "forgot" && (
          <>
            <label className="mb-1 block text-xs font-semibold text-slate-500">PIN</label>
            <input
              ref={pinRef}
              className="input mb-1 text-center text-xl tracking-[0.6em]"
              type="password"
              inputMode="numeric"
              maxLength={6}
              value={pin}
              onChange={(e) => {
                const v = e.target.value.replace(/\D/g, "");
                setPin(v);
                if (mode === "signin" && v.length === 6 && username.trim()) submit(undefined, v); // auto-unlock
              }}
              placeholder="••••••"
            />
          </>
        )}

        <div className="mt-2 min-h-[18px] text-xs">
          {error && <span className="text-red-400">{error}</span>}
          {!error && info && <span className="text-emerald-400">{info}</span>}
        </div>

        {mode !== "signin" && (
          <button
            disabled={busy || !username.trim() || (mode === "signup" && pin.length !== 6)}
            className="btn-accent mt-3 w-full rounded-xl py-2.5 text-sm"
          >
            {mode === "signup" ? "Create account" : "Reset PIN"}
          </button>
        )}

        <div className="mt-4 flex justify-between text-xs text-slate-500">
          {mode === "signin" ? (
            <>
              <button type="button" className="hover:text-slate-300" onClick={() => { setMode("forgot"); setError(""); }}>
                Forgot PIN?
              </button>
              <button type="button" className="hover:text-slate-300" onClick={() => { setMode("signup"); setError(""); setPin(""); }}>
                Create account
              </button>
            </>
          ) : (
            <button type="button" className="hover:text-slate-300" onClick={() => { setMode("signin"); setError(""); setPin(""); }}>
              {hasAccounts || mode === "forgot" ? "Back to sign in" : ""}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

export const fetchAccountCount = () => get<{ accounts: number }>("/api/auth/state").then((r) => r.accounts);
