import { useCallback, useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { gateway } from "../api/gateway";

// Embedded operator terminal (ADR-012, docs/SPEC-EMBEDDED-TERMINAL.md).
// Wire protocol: binary WS frames = raw terminal bytes; text frames = JSON
// control ({type:"resize"} out, {type:"exit"} in). The Gateway proxies to the
// host terminal daemon and injects the bearer token server-side.

const BASE = import.meta.env.VITE_GATEWAY_URL ?? "";

function wsUrl(): string {
  if (BASE) return `${BASE.replace(/^http/, "ws")}/api/terminal/ws`;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/terminal/ws`;
}

// xterm palette derived from the UI design tokens (spec §3.2).
const THEME = {
  background: "#0f1624",          // --bg-panel
  foreground: "#e5e7eb",          // --text
  cursor: "#76b900",              // --nv-green
  cursorAccent: "#0f1624",
  selectionBackground: "#3b82f659", // --accent @ 35%
  black: "#0a0e1a",
  red: "#ef4444",
  green: "#10b981",
  yellow: "#f59e0b",
  blue: "#3b82f6",
  magenta: "#8b5cf6",
  cyan: "#38bdf8",
  white: "#e5e7eb",
  brightBlack: "#6b7280",
  brightRed: "#f87171",
  brightGreen: "#34d399",
  brightYellow: "#fbbf24",
  brightBlue: "#60a5fa",
  brightMagenta: "#a78bfa",
  brightCyan: "#7dd3fc",
  brightWhite: "#f9fafb",
};

const encoder = new TextEncoder();

type Status = "connecting" | "connected" | "disconnected" | "ended";

const STATUS_META: Record<Status, { color: string; label: string }> = {
  connecting:   { color: "#f59e0b",         label: "CONNECTING" },
  connected:    { color: "var(--healthy)",  label: "CONNECTED" },
  disconnected: { color: "var(--critical)", label: "DISCONNECTED" },
  ended:        { color: "var(--critical)", label: "SESSION ENDED" },
};

export function TerminalPanel() {
  const [enabled, setEnabled] = useState(false);
  const [status, setStatus] = useState<Status>("connecting");
  const bodyRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    gateway
      .getTerminalEnabled()
      .then((d) => setEnabled(Boolean((d as { enabled: boolean }).enabled)))
      .catch(() => setEnabled(false));
  }, []);

  const connect = useCallback(() => {
    const term = termRef.current;
    if (!term) return;
    setStatus("connecting");
    const ws = new WebSocket(wsUrl());
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      fitRef.current?.fit();
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      term.focus();
    };
    ws.onmessage = (evt) => {
      if (typeof evt.data === "string") {
        try {
          if (JSON.parse(evt.data).type === "exit") setStatus("ended");
        } catch { /* ignore malformed control frames */ }
      } else {
        term.write(new Uint8Array(evt.data as ArrayBuffer));
      }
    };
    ws.onclose = () => {
      if (wsRef.current === ws) wsRef.current = null;
      setStatus((s) => (s === "ended" ? s : "disconnected"));
    };
  }, []);

  // Create the terminal once the feature check passes; one xterm instance,
  // one WS/PTY per connection (Reconnect starts a fresh shell).
  useEffect(() => {
    if (!enabled || !bodyRef.current) return;

    const term = new Terminal({
      theme: THEME,
      fontFamily: '"JetBrains Mono", "SF Mono", "Menlo", ui-monospace, monospace',
      fontSize: 12,
      scrollback: 5000,
      cursorBlink: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(bodyRef.current);
    fit.fit();
    term.onData((data) => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) ws.send(encoder.encode(data));
    });
    termRef.current = term;
    fitRef.current = fit;

    // Keep the PTY winsize in lockstep with the rendered cols/rows so
    // full-screen editors reflow correctly (spec §3.3).
    const observer = new ResizeObserver(() => {
      fit.fit();
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    });
    observer.observe(bodyRef.current);

    connect();

    return () => {
      observer.disconnect();
      wsRef.current?.close();
      wsRef.current = null;
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [enabled, connect]);

  if (!enabled) return null;

  const meta = STATUS_META[status];

  function handleReconnect() {
    wsRef.current?.close();
    termRef.current?.reset();
    connect();
  }

  return (
    <div>
      <div className="section-heading">Agent Configuration Terminal</div>
      <section style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        height: 420,
      }}>
        {/* Header */}
        <div style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--border)",
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: ".12em",
          textTransform: "uppercase",
          color: "var(--text-dim)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexShrink: 0,
        }}>
          <span style={{ display: "block", width: 3, height: 12, background: "var(--nv-green)", borderRadius: 2 }} />
          Lab Host Shell
          <span style={{ flex: 1 }} />
          <span style={{ color: meta.color, fontSize: 9, letterSpacing: ".1em", display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 8 }}>●</span>
            {meta.label}
          </span>
          {(status === "disconnected" || status === "ended") && (
            <button
              onClick={handleReconnect}
              title="Start a fresh shell session"
              style={{
                padding: "4px 10px",
                background: "rgba(96,165,250,.1)",
                border: "1px solid rgba(96,165,250,.35)",
                borderRadius: 5, color: "#60a5fa",
                fontWeight: 600, fontSize: 10, cursor: "pointer",
                fontFamily: "var(--mono)", letterSpacing: ".06em",
              }}
            >
              Reconnect
            </button>
          )}
        </div>

        {/* Terminal well. The padding lives on this outer wrapper, not on the
            div xterm attaches to (bodyRef) — FitAddon measures padding on the
            terminal's own root element, not its parent, so padding set on
            bodyRef itself is invisible to its row/col math and clips the
            last row by exactly that many pixels. */}
        <div style={{ flex: 1, minHeight: 0, background: "#0f1624", padding: "8px 0 0 10px" }}>
          <div ref={bodyRef} style={{ width: "100%", height: "100%" }} />
        </div>
      </section>
    </div>
  );
}
