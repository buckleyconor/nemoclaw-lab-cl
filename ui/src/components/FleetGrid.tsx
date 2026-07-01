import type { AssetRecord, PackInfo } from "../types";

const SERVER_IMG =
  "https://i.dell.com/is/image/DellContent/content/dam/ss2/product-images/dell-enterprise-products/enterprise-systems/poweredge/xe9780l/media-gallery/server-dell-xe9780l-16xe1-c-gallery-2.psd?fmt=pjpg&pscan=auto&scl=1&hei=402&wid=1669&qlt=100,1&resMode=sharp2&size=1669,402&chrss=full";

interface Props {
  assets: AssetRecord[];
  pack: PackInfo | null;
  onSelectAsset: (id: string) => void;
}

export function FleetGrid({ assets, pack, onSelectAsset }: Props) {
  return (
    <>
      <style>{`
        @keyframes fault-pulse {
          0%,100% { box-shadow: 0 0 0 1px var(--critical), 0 4px 20px rgba(239,68,68,.18); }
          50%      { box-shadow: 0 0 0 2px var(--critical), 0 6px 28px rgba(239,68,68,.35); }
        }
      `}</style>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
        gap: 14,
      }}>
        {assets.map((a) => {
          const faulted = a.state === "faulted";
          return (
            <div
              key={a.id}
              onClick={() => faulted && onSelectAsset(a.id)}
              style={{
                background: "var(--bg-card)",
                border: `1px solid ${faulted ? "var(--critical)" : "var(--border)"}`,
                borderRadius: 10,
                overflow: "hidden",
                transition: "border-color .2s, box-shadow .2s",
                boxShadow: faulted ? "0 0 0 1px var(--critical), 0 4px 20px rgba(239,68,68,.18)" : "none",
                animation: faulted ? "fault-pulse 2.4s ease-in-out infinite" : "none",
                position: "relative" as const,
                cursor: faulted ? "pointer" : "default",
              }}
            >
              {/* Server image area */}
              <div style={{
                background: "#060a14",
                padding: "8px 10px 4px",
                borderBottom: `1px solid ${faulted ? "rgba(239,68,68,.25)" : "var(--border)"}`,
                position: "relative" as const,
              }}>
                <img
                  src={SERVER_IMG}
                  alt="Dell PowerEdge XE9780L"
                  loading="lazy"
                  style={{
                    width: "100%",
                    height: "auto",
                    display: "block",
                    borderRadius: 4,
                    filter: faulted ? "grayscale(20%) brightness(0.75)" : "brightness(1.05)",
                    transition: "filter .3s",
                  }}
                />
                {/* Health badge */}
                <div style={{
                  position: "absolute" as const,
                  top: 8, right: 8,
                  width: 24, height: 24,
                  borderRadius: "50%",
                  background: faulted ? "var(--critical)" : "var(--healthy)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 13,
                  fontWeight: 700,
                  color: "#fff",
                  boxShadow: faulted
                    ? "0 0 0 3px rgba(239,68,68,.3)"
                    : "0 0 0 3px rgba(16,185,129,.25)",
                }}>
                  {faulted ? "!" : "✓"}
                </div>
              </div>

              {/* Info row */}
              <div style={{ padding: "10px 12px 12px" }}>
                <div style={{
                  fontSize: 10,
                  fontFamily: "var(--mono)",
                  color: "var(--text-dim)",
                  letterSpacing: ".08em",
                  textTransform: "uppercase",
                  marginBottom: 3,
                }}>
                  {pack?.asset_noun.singular ?? "server"} · 8× NVIDIA B300
                </div>
                <div style={{ fontSize: 13, fontWeight: 600, color: "#fff", marginBottom: 6 }}>
                  {a.id}
                </div>
                <span style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  padding: "2px 8px",
                  borderRadius: 4,
                  background: faulted ? "rgba(239,68,68,.12)" : "rgba(16,185,129,.1)",
                  border: `1px solid ${faulted ? "rgba(239,68,68,.3)" : "rgba(16,185,129,.25)"}`,
                  fontSize: 10,
                  fontWeight: 700,
                  fontFamily: "var(--mono)",
                  letterSpacing: ".06em",
                  color: faulted ? "var(--critical)" : "var(--healthy)",
                  textTransform: "uppercase" as const,
                }}>
                  <span style={{
                    width: 5, height: 5, borderRadius: "50%",
                    background: faulted ? "var(--critical)" : "var(--healthy)",
                    flexShrink: 0,
                  }} />
                  {faulted ? "Fault Detected" : "Healthy"}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
