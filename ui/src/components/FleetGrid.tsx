import { useState } from "react";
import type { AssetRecord, PackInfo } from "../types";

interface Props {
  assets: AssetRecord[];
  pack: PackInfo | null;
  onSelectAsset: (id: string) => void;
  idle: boolean;
}

// Isolated so image-load-failure state doesn't need to live in a keyed array
// of hooks — falls back to a plain icon tile if asset_image_url 404s (e.g.
// a pack references a product photo that hasn't been dropped into place yet)
// instead of showing the browser's broken-image glyph. Also the fallback for
// `src === null` (no asset_image_url configured at all) — deliberately never
// defaults to another pack's product photo, since that reads as wrong
// equipment rather than a generic placeholder.
function AssetImage({
  src, alt, faulted, size,
}: { src: string | null; alt: string; faulted: boolean; size: "tile" | "thumb" }) {
  const [failed, setFailed] = useState(false);
  const dims = size === "thumb"
    ? { width: 56, height: 56, borderRadius: 8 }
    : { width: "100%" as const, aspectRatio: "1669 / 402", borderRadius: 4 };
  if (!src || failed) {
    return (
      <div style={{
        ...dims,
        display: "flex", alignItems: "center", justifyContent: "center",
        background: "linear-gradient(135deg, rgba(0,118,206,.12), rgba(118,185,0,.08))",
        fontSize: size === "thumb" ? 20 : 26,
        flexShrink: 0,
      }}>
        🖥️
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setFailed(true)}
      style={{
        ...dims,
        objectFit: size === "thumb" ? "cover" : undefined,
        display: "block",
        filter: faulted ? "grayscale(20%) brightness(0.75)" : "brightness(1.05)",
        transition: "filter .3s",
        flexShrink: 0,
      }}
    />
  );
}

// Shared between both layouts so the "healthy vs faulted" visual language
// (color, dot, label) never drifts between the grid tiles and the list rows.
function StatusBadge({ faulted }: { faulted: boolean }) {
  return (
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
      whiteSpace: "nowrap" as const,
      flexShrink: 0,
    }}>
      <span style={{
        width: 5, height: 5, borderRadius: "50%",
        background: faulted ? "var(--critical)" : "var(--healthy)",
        flexShrink: 0,
      }} />
      {faulted ? "Fault Detected" : "Healthy"}
    </span>
  );
}

interface AssetLayoutProps {
  asset: AssetRecord;
  pack: PackInfo | null;
  imgSrc: string | null;
  specLabel: string;
  onSelectAsset: (id: string) => void;
}

// Image-forward tile — one photo per asset. Reads fine for a small fleet
// (e.g. 4 servers) but eats a lot of vertical space once a fleet grows past
// a handful of assets, hence the "list" layout alternative below.
function FleetTile({ asset, pack, imgSrc, specLabel, onSelectAsset }: AssetLayoutProps) {
  const faulted = asset.state === "faulted";
  const displayName = pack?.asset_display_names?.[asset.id] ?? asset.id;
  return (
    <div
      onClick={() => faulted && onSelectAsset(asset.id)}
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
      {/* Asset image area */}
      <div style={{
        background: "#060a14",
        padding: "8px 10px 4px",
        borderBottom: `1px solid ${faulted ? "rgba(239,68,68,.25)" : "var(--border)"}`,
        position: "relative" as const,
      }}>
        <AssetImage src={imgSrc} alt={pack?.name ?? "Asset"} faulted={faulted} size="tile" />
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
          {pack?.asset_noun.singular ?? "server"} · {specLabel}
        </div>
        <div style={{ fontSize: 13, fontWeight: 600, color: "#fff", marginBottom: 6 }}>
          {displayName}
        </div>
        <StatusBadge faulted={faulted} />
      </div>
    </div>
  );
}

// Compact stacked row — a small thumbnail on the far left, two lines of text
// (name, then spec line), and a status badge on the right. For fleets where
// the image-forward grid above would push everything else below the fold.
function FleetListRow({ asset, pack, imgSrc, specLabel, onSelectAsset }: AssetLayoutProps) {
  const faulted = asset.state === "faulted";
  const displayName = pack?.asset_display_names?.[asset.id] ?? asset.id;
  return (
    <div
      onClick={() => faulted && onSelectAsset(asset.id)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "8px 12px",
        background: "var(--bg-card)",
        border: `1px solid ${faulted ? "var(--critical)" : "var(--border)"}`,
        borderRadius: 8,
        boxShadow: faulted ? "0 0 0 1px var(--critical), 0 4px 20px rgba(239,68,68,.18)" : "none",
        animation: faulted ? "fault-pulse 2.4s ease-in-out infinite" : "none",
        cursor: faulted ? "pointer" : "default",
        transition: "border-color .2s, box-shadow .2s",
      }}
    >
      <AssetImage src={imgSrc} alt={pack?.name ?? "Asset"} faulted={faulted} size="thumb" />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13, fontWeight: 600, color: "#fff", marginBottom: 2,
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}>
          Name: {displayName}
        </div>
        <div style={{
          fontSize: 11, color: "var(--text-dim)", fontFamily: "var(--mono)",
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}>
          Model: {specLabel}
        </div>
      </div>
      <StatusBadge faulted={faulted} />
    </div>
  );
}

export function FleetGrid({ assets, pack, onSelectAsset, idle }: Props) {
  const specLabel = pack?.asset_spec_label ?? "8× NVIDIA B300";
  const layout = pack?.fleet_layout ?? "grid";
  // Per-asset image wins (packs like oil-rigs/healthcare mix distinct
  // equipment types), falling back to the pack-wide photo (laptop-fleet,
  // finance-atm-fleet, telco-edge-5g-masts, datacenter-xe9680, all of which
  // repeat one equipment type across the whole fleet).
  const imgSrcFor = (assetId: string) =>
    pack?.asset_image_urls?.[assetId] ?? pack?.asset_image_url ?? null;
  return (
    <>
      <style>{`
        @keyframes fault-pulse {
          0%,100% { box-shadow: 0 0 0 1px var(--critical), 0 4px 20px rgba(239,68,68,.18); }
          50%      { box-shadow: 0 0 0 2px var(--critical), 0 6px 28px rgba(239,68,68,.35); }
        }
      `}</style>
      <div style={{
        padding: 12,
        borderRadius: 12,
        border: "1px solid var(--border)",
        animation: idle ? "idle-glow 3.6s ease-in-out infinite" : "none",
        transition: "box-shadow .3s",
      }}>
        {layout === "list" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {assets.map((a) => (
              <FleetListRow key={a.id} asset={a} pack={pack} specLabel={specLabel}
                imgSrc={imgSrcFor(a.id)} onSelectAsset={onSelectAsset} />
            ))}
          </div>
        ) : (
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: 14,
          }}>
            {assets.map((a) => (
              <FleetTile key={a.id} asset={a} pack={pack} specLabel={specLabel}
                imgSrc={imgSrcFor(a.id)} onSelectAsset={onSelectAsset} />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
