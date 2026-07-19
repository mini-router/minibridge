export type ProviderDescriptor = {
  provider_id: string;
  provider_kind: string;
  endpoint_url?: string | null;
  payload_style?: string | null;
  auth_header?: string | null;
  auth_scheme?: string | null;
  extra_headers?: Record<string, string>;
  timeout_seconds?: number | null;
  notes?: Record<string, unknown>;
};

export type Receipt = {
  receipt_id: string;
  service_id: string;
  pricing_table_id: string;
  request_id: string;
  provider_id: string;
  provider_kind: string;
  provider_endpoint_url?: string | null;
  caller_id: string;
  owner_id: string;
  key_id: string;
  model: string;
  request_nonce?: string | null;
  request_expires_at?: string | null;
  request_hash: string;
  response_hash: string;
  usage: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  };
  computed_cost_usd: string;
  issued_at: string;
  signature?: string | null;
};

export type BundleManifest = {
  version: string;
  generator_version: string;
  bundle_kind: string;
  service_id: string;
  service_public_key_fingerprint: string;
  service_public_key: string;
  tee_mode: string;
  raw_proof_count: number;
  proof_count: number;
  merkle_root: string;
  created_at: string;
  proof_ids: string[];
  attestation_hash?: string | null;
  attestation_verified: boolean;
  validation?: Record<string, unknown>;
};

export type BundleManifestResponse = {
  ok: boolean;
  manifest: BundleManifest;
  counts: {
    raw_proofs: number;
    verified_proofs: number;
    validation_rows: number;
  };
  attestation_verified: boolean;
};

export type RunnerRegistration = {
  runner_id: string;
  endpoint_url: string;
  service_id?: string | null;
  tee_mode?: string | null;
  attestation?: Record<string, unknown>;
  active: boolean;
  last_seen_at?: string | null;
  notes?: Record<string, unknown>;
};

export type HostJobRecord = {
  job_id: string;
  job_type: string;
  runner_id: string;
  request: Record<string, unknown>;
  status: string;
  submitted_at: string;
  completed_at?: string | null;
  response?: Record<string, unknown> | null;
  receipt?: Record<string, unknown> | null;
  proof?: Record<string, unknown> | null;
  bundle?: {
    manifest: BundleManifest;
    raw_proofs: unknown[];
    verified_proofs: unknown[];
    validation_report: unknown[];
    attestation?: Record<string, unknown> | null;
  } | null;
  verification?: Record<string, unknown> | null;
  error?: string | null;
};

export type ResponsePayload = {
  ok: boolean;
  error?: string;
  [key: string]: unknown;
};
