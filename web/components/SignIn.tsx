"use client";

import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import { useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";

function nextPath(): string {
  if (typeof window === "undefined") return "/";
  const next = new URLSearchParams(window.location.search).get("next") ?? "/";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

export default function SignIn() {
  const [status, setStatus] = useState<{ has_passkeys: boolean; signed_in: boolean } | null>(null);
  const [setup, setSetup] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/auth/status").then((s) => {
      if (s.signed_in) window.location.replace(nextPath());
      setStatus(s);
      setSetup(!s.has_passkeys);
    }).catch(() => setError("The app is not reachable. Is the laptop on?"));
  }, []);

  const fail = (e: unknown) => {
    const name = (e as { name?: string })?.name;
    setError(e instanceof ApiError ? e.message : name === "NotAllowedError" ? "Cancelled. Try again when you're ready." : "That did not work. Try again.");
  };

  const signIn = async () => {
    setBusy(true); setError("");
    try {
      const options = await api("/api/auth/login/options", { body: {} });
      const credential = await startAuthentication({ optionsJSON: options });
      await api("/api/auth/login/verify", { body: { credential } });
      window.location.replace(nextPath());
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const register = async () => {
    setBusy(true); setError("");
    try {
      const setupCode = code.trim().toUpperCase();
      const options = await api("/api/auth/register/options", { body: { setup_code: setupCode } });
      const credential = await startRegistration({ optionsJSON: options });
      await api("/api/auth/register/verify", { body: { credential, setup_code: setupCode, device_name: navigator.platform || "device" } });
      window.location.replace(nextPath());
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  return (
    <div className="page" style={{ maxWidth: 440, margin: "0 auto", minHeight: "80dvh", alignContent: "center" }}>
      <span className="wordmark" style={{ fontSize: "2rem" }}>clipping</span>
      {!setup ? (
        <div className="card stack" style={{ gap: 14 }}>
          <h1 style={{ fontSize: "1.4rem" }}>Sign in</h1>
          <p className="muted">Use the passkey on this device: Face ID, fingerprint or Windows Hello.</p>
          <button className="btn primary block" disabled={busy || !status} onClick={signIn}>Sign in with passkey</button>
          <button className="btn ghost block" onClick={() => setSetup(true)}>Set up this device</button>
        </div>
      ) : (
        <div className="card stack" style={{ gap: 14 }}>
          <h1 style={{ fontSize: "1.4rem" }}>Set up this device</h1>
          <p className="muted">
            {status?.has_passkeys
              ? "On a device that is already signed in, open Settings › Add another device to get a one-time code."
              : "Enter the one-time code printed in the laptop window where the app started."}
          </p>
          <div className="field">
            <label htmlFor="setup-code">Setup code</label>
            <input id="setup-code" className="input" autoComplete="one-time-code" placeholder="XXXXX-XXXXX" value={code}
              onChange={(e) => setCode(e.target.value)} style={{ letterSpacing: "0.12em", textTransform: "uppercase" }} />
          </div>
          <button className="btn primary block" disabled={busy || code.trim().length < 10} onClick={register}>Create passkey</button>
          {status?.has_passkeys && <button className="btn ghost block" onClick={() => setSetup(false)}>I already have a passkey</button>}
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
