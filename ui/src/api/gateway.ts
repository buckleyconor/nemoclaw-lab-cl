const BASE = import.meta.env.VITE_GATEWAY_URL ?? "";

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export const gateway = {
  getPack: () => json("/api/pack"),
  getAssets: () => json("/api/assets"),
  getNotifications: () => json("/api/notifications"),
  markRead: (id: string) => json(`/api/notifications/${id}/read`, { method: "POST" }),
  getFaults: () => json("/api/faults"),
  getFault: (id: string) => json(`/api/faults/${id}`),
  decide: (faultId: string, decision: "approved" | "denied") =>
    json(`/api/faults/${faultId}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
  updateFaultStatus: (faultId: string, status: string) =>
    json(`/api/faults/${faultId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  getActivity: () => json("/api/activity"),
  getTerminalEnabled: () => json("/api/terminal/enabled"),
  getLabHealth: () => json("/api/lab-health"),
  getAgentStatus: () => json("/api/agent/status"),
};
