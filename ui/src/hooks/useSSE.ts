import { useEffect, useRef, useState } from "react";
import type { SSEEvent } from "../types";

const BASE = import.meta.env.VITE_GATEWAY_URL ?? "";

/**
 * Subscribe to the Gateway's SSE stream.
 *
 * Returns whether the stream is currently connected, and — because SSE only
 * carries *deltas* — invokes `onReconnect` each time the stream comes back
 * after a drop, so the caller can refetch the REST snapshot it missed events
 * against. Without that, a backend restart (e.g. an automated pack switch)
 * leaves the dashboard silently rendering the previous process's state.
 */
export function useSSE(
  onEvent: (evt: SSEEvent) => void,
  onReconnect?: () => void
): boolean {
  const [connected, setConnected] = useState(false);
  // Distinguishes the first successful open (initial snapshot already in
  // flight from App's mount effect) from a re-open after a drop.
  const hadDropRef = useRef(false);

  useEffect(() => {
    const source = new EventSource(`${BASE}/api/events`);

    source.onopen = () => {
      setConnected(true);
      if (hadDropRef.current) {
        hadDropRef.current = false;
        onReconnect?.();
      }
    };

    source.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data) as SSEEvent;
        onEvent(evt);
      } catch {
        // malformed event — ignore
      }
    };

    source.onerror = () => {
      // The browser auto-reconnects; onopen above handles the resync.
      hadDropRef.current = true;
      setConnected(false);
    };

    return () => source.close();
  }, [onEvent, onReconnect]);

  return connected;
}
