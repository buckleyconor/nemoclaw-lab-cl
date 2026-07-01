import type { ActivityEvent } from "../types";

const STEP_ICONS: Record<string, string> = {
  detect: "🔍",
  diagnose: "📋",
  search_kb: "📚",
  present: "🖥️",
  remediate: "🔧",
  resolved: "✅",
  denied: "❌",
};

interface Props {
  events: ActivityEvent[];
}

export function ActivityFeed({ events }: Props) {
  return (
    <section>
      <h3>Agent Activity</h3>
      {events.length === 0 && (
        <div style={{ color: "#6b7280", fontSize: 13 }}>No activity yet</div>
      )}
      <ol style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {events.map((e) => (
          <li
            key={e.id}
            style={{
              display: "flex",
              gap: 8,
              alignItems: "flex-start",
              padding: "6px 0",
              borderBottom: "1px solid #f3f4f6",
            }}
          >
            <span style={{ fontSize: 16 }}>{STEP_ICONS[e.step] ?? "▸"}</span>
            <div>
              <div style={{ fontWeight: 500, fontSize: 13 }}>{e.message}</div>
              <div style={{ fontSize: 11, color: "#9ca3af" }}>
                {new Date(e.ts).toLocaleTimeString()}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
