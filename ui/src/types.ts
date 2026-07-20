export interface PackInfo {
  id: string;
  name: string;
  asset_noun: { singular: string; plural: string };
  fleet_label: string;
  theme: string;
  sentinel_name: string;
  asset_image_url: string | null;
  asset_spec_label: string | null;
  fleet_layout: "grid" | "list";
  asset_display_names: Record<string, string>;
  asset_image_urls: Record<string, string>;
}

export interface AssetRecord {
  id: string;
  type: string;
  state: "healthy" | "faulted";
  active_fault_event_id: string | null;
}

export interface Notification {
  id: string;
  fault_event_id: string;
  title: string;
  body: string;
  read: boolean;
  ts: string;
}

export interface ScenarioImpact {
  summary: string;
  workload_impact: string;
  service_risk: string;
  estimated_duration: string;
}

export interface FaultEvent {
  id: string;
  scenario_id: string;
  asset_id: string;
  detected_at: string;
  status:
    | "detected"
    | "diagnosing"
    | "awaiting_approval"
    | "remediating"
    | "resolved"
    | "denied";
  kb_article_id: string | null;
  log_extract: string | null;
  remediation_step_labels: string[];
  // Diagnosis fields — written by the agent via PATCH /api/faults/:id/diagnosis
  error_signature: string | null;
  analysis: string | null;
  kb_title: string | null;
  kb_score: number | null;
  impact: ScenarioImpact | null;
}

export interface ActivityEvent {
  id: string;
  fault_event_id: string;
  step: string;
  message: string;
  ts: string;
}

export type SSEEvent =
  | { type: "asset"; data: AssetRecord }
  | { type: "fault"; data: FaultEvent }
  | { type: "notification"; data: Notification }
  | { type: "activity"; data: ActivityEvent }
  | { type: "decision"; data: { fault_event_id: string; decision: string } }
  | { type: "reset"; data: Record<string, never> };
