import { useEffect } from "react";
import type { SSEEvent } from "../types";

const BASE = import.meta.env.VITE_GATEWAY_URL ?? "";

export function useSSE(onEvent: (evt: SSEEvent) => void): void {
  useEffect(() => {
    const source = new EventSource(`${BASE}/api/events`);

    source.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data) as SSEEvent;
        onEvent(evt);
      } catch {
        // malformed event — ignore
      }
    };

    source.onerror = () => {
      // browser will auto-reconnect; no action needed
    };

    return () => source.close();
  }, [onEvent]);
}
