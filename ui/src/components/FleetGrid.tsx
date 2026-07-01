import type { AssetRecord, PackInfo } from "../types";

interface Props {
  assets: AssetRecord[];
  pack: PackInfo | null;
  onSelectAsset: (id: string) => void;
}

export function FleetGrid({ assets, pack, onSelectAsset }: Props) {
  return (
    <section>
      <h2>{pack?.fleet_label ?? "Fleet Health"}</h2>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {assets.map((a) => (
          <button
            key={a.id}
            onClick={() => onSelectAsset(a.id)}
            style={{
              padding: "12px 20px",
              borderRadius: 8,
              border: "2px solid",
              cursor: "pointer",
              background: a.state === "faulted" ? "#fee2e2" : "#dcfce7",
              borderColor: a.state === "faulted" ? "#ef4444" : "#22c55e",
              fontWeight: 600,
              minWidth: 160,
            }}
          >
            <div style={{ fontSize: 12, color: "#6b7280", textTransform: "capitalize" }}>
              {pack?.asset_noun.singular ?? "asset"}
            </div>
            <div style={{ fontSize: 16 }}>{a.id}</div>
            <div
              style={{
                marginTop: 4,
                fontSize: 12,
                fontWeight: 700,
                color: a.state === "faulted" ? "#dc2626" : "#16a34a",
                textTransform: "uppercase",
              }}
            >
              {a.state === "faulted" ? "● FAULT" : "● HEALTHY"}
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}
