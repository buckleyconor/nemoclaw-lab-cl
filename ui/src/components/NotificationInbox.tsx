import { useState } from "react";
import type { Notification } from "../types";
import { gateway } from "../api/gateway";

interface Props {
  notifications: Notification[];
  unreadCount: number;
  onRead: (id: string) => void;
}

export function NotificationInbox({ notifications, unreadCount, onRead }: Props) {
  const [open, setOpen] = useState(false);

  function handleRead(id: string) {
    onRead(id); // optimistic — update UI immediately
    gateway.markRead(id).catch(() => {}); // fire-and-forget; 404s on reset are fine
  }

  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ""}`}
        style={{
          position: "relative",
          padding: "6px 10px",
          background: unreadCount > 0 ? "rgba(239,68,68,.1)" : "var(--bg-elevated)",
          border: `1px solid ${unreadCount > 0 ? "rgba(239,68,68,.35)" : "var(--border-strong)"}`,
          borderRadius: 6,
          cursor: "pointer",
          fontSize: 15,
          lineHeight: 1,
          transition: "background .15s",
        }}
      >
        🔔
        {unreadCount > 0 && (
          <span style={{
            position: "absolute",
            top: -4, right: -4,
            background: "var(--critical)",
            color: "#fff",
            borderRadius: "50%",
            fontSize: 9,
            width: 15, height: 15,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontFamily: "var(--mono)",
          }}>
            {unreadCount}
          </span>
        )}
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <div
            onClick={() => setOpen(false)}
            style={{ position: "fixed", inset: 0, zIndex: 99 }}
          />
          <div style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 8px)",
            width: 340,
            background: "var(--bg-card)",
            border: "1px solid var(--border-strong)",
            borderRadius: 10,
            boxShadow: "0 8px 32px rgba(0,0,0,.5)",
            zIndex: 100,
            maxHeight: 420,
            overflowY: "auto",
          }}>
            <div style={{
              padding: "10px 14px",
              borderBottom: "1px solid var(--border)",
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: ".12em",
              textTransform: "uppercase",
              color: "var(--text-dim)",
            }}>
              Notifications
            </div>
            {notifications.length === 0 ? (
              <div style={{ padding: "16px 14px", color: "var(--text-dim)", fontSize: 13 }}>
                No notifications
              </div>
            ) : (
              notifications.map((n) => (
                <div
                  key={n.id}
                  style={{
                    padding: "12px 14px",
                    borderBottom: "1px solid var(--border)",
                    background: n.read ? "transparent" : "rgba(59,130,246,.06)",
                    cursor: n.read ? "default" : "pointer",
                    borderLeft: n.read ? "3px solid transparent" : "3px solid var(--accent)",
                    transition: "background .15s",
                  }}
                  onClick={() => !n.read && handleRead(n.id)}
                >
                  <div style={{
                    fontWeight: n.read ? 400 : 600,
                    color: n.read ? "var(--text-muted)" : "#fff",
                    fontSize: 13,
                    marginBottom: 3,
                  }}>
                    {n.title}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 4, lineHeight: 1.4 }}>
                    {n.body}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text-dim)", fontFamily: "var(--mono)" }}>
                    {new Date(n.ts).toLocaleTimeString()}
                    {!n.read && (
                      <span style={{
                        marginLeft: 8,
                        color: "var(--accent)",
                        fontWeight: 600,
                        letterSpacing: ".04em",
                      }}>
                        · click to mark read
                      </span>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
