import { useCallback, useEffect, useRef, useState } from "react";
import { gateway } from "../api/gateway";
import type { LabHealth } from "../types";

// Host-side dependency state (compose services, agent LLM route, wake hook,
// terminal daemon), probed by the Gateway container every POLL_MS. The
// "agent idle" failure class used to be invisible — a dead lab looked exactly
// like "no faults right now" (2026-08-28 incident).
const POLL_MS = 20_000;

export function LabHealthBadge() {
  const [health, setHealth] = useState<LabHealth | null>(null);
  const [endpointDown, setEndpointDown] = useState(false);
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setHealth((await gateway.getLabHealth()) as LabHealth);
      setEndpointDown(false);
    } catch {
      setEndpointDown(true);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  // Close the popover on outside click.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const badCount = health ? health.checks.filter((c) => c.status === "fail").length : 0;
  const state = endpointDown ? "unknown" : health && !health.healthy ? "degraded" : "ok";
  const label = endpointDown
    ? "Lab state unknown"
    : state === "degraded"
      ? `Lab degraded · ${badCount}`
      : "Lab healthy";

  return (
    <div className="lab-health" ref={boxRef}>
      <button
        className={`lab-health-chip ${state}`}
        onClick={() => setOpen((v) => !v)}
        title="Host-side dependency health, probed by the Gateway. Fixes: make doctor / make doctor-fix on the lab host."
      >
        <span className="lab-health-dot" />
        {label}
      </button>
      {open && (
        <div className="lab-health-pop">
          <div className="lab-health-pop-title">Lab health</div>
          {health ? (
            health.checks.map((c) => (
              <div key={c.id} className={`lab-health-row ${c.status}`}>
                <span className="lab-health-row-mark">
                  {c.status === "ok" ? "✓" : c.status === "fail" ? "✗" : "·"}
                </span>
                <span className="lab-health-row-name">{c.name}</span>
                <span className="lab-health-row-detail">{c.detail}</span>
              </div>
            ))
          ) : (
            <div className="lab-health-row skip">
              <span className="lab-health-row-name">
                {endpointDown ? "health endpoint unreachable" : "checking…"}
              </span>
            </div>
          )}
          <div className="lab-health-foot">
            Rechecks every {POLL_MS / 1000}s · fixes: <code>make doctor</code> /{" "}
            <code>make doctor-fix</code> on the host
          </div>
        </div>
      )}
    </div>
  );
}
