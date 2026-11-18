"use client";

import { dismissToast, useToast } from "@/lib/toast";

/** The one floating message (with Undo when an action has one). Fixed in place, so it never moves the page. */
export default function ToastHost() {
  const toast = useToast();
  if (!toast) return null;
  const { action } = toast;
  return (
    <div key={toast.id} className={`toast${toast.tone === "error" ? " error" : ""}`} role={toast.tone === "error" ? "alert" : "status"}>
      <span className="msg">{toast.message}</span>
      {action && <button className="btn" onClick={() => { dismissToast(); action.run(); }}>{action.label}</button>}
      {!action && <button className="btn ghost" onClick={dismissToast}>Dismiss</button>}
      {action && <i className="timer" style={{ animationDuration: `${toast.seconds}s` }} />}
    </div>
  );
}
