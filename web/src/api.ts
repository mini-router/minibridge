import type {
  BundleManifestResponse,
  HostJobRecord,
  ProviderDescriptor,
  Receipt,
  ResponsePayload,
  RunnerRegistration,
} from "./types";

const jsonHeaders = {
  "Content-Type": "application/json",
};

function buildUrl(baseUrl: string, path: string): string {
  const normalizedBase = baseUrl.replace(/\/+$/, "");
  return `${normalizedBase}${path}`;
}

export async function requestJson<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(baseUrl, path), {
    ...init,
    headers: {
      ...jsonHeaders,
      ...(init?.headers || {}),
    },
  });
  const text = await response.text();
  let payload: unknown = {};
  if (text.trim()) {
    payload = JSON.parse(text);
  }
  if (!response.ok) {
    const message = typeof payload === "object" && payload && "error" in payload ? String((payload as ResponsePayload).error) : response.statusText;
    throw new Error(message || `HTTP ${response.status}`);
  }
  return payload as T;
}

export async function getHealth(baseUrl: string) {
  return requestJson<{ ok: boolean; service_id?: string }>(baseUrl, "/health");
}

export async function listProviders(baseUrl: string) {
  return requestJson<{ providers: ProviderDescriptor[] }>(baseUrl, "/providers");
}

export async function listReceipts(baseUrl: string) {
  return requestJson<{ receipts: Receipt[] }>(baseUrl, "/receipts");
}

export async function getBundleManifest(baseUrl: string) {
  return requestJson<BundleManifestResponse>(baseUrl, "/bundle/manifest");
}

export async function listRunners(baseUrl: string) {
  return requestJson<{ runners: RunnerRegistration[] }>(baseUrl, "/runners");
}

export async function listJobs(baseUrl: string) {
  return requestJson<{ jobs: HostJobRecord[] }>(baseUrl, "/jobs");
}

export async function registerProvider(baseUrl: string, payload: unknown) {
  return requestJson<{ ok: boolean; provider: ProviderDescriptor }>(baseUrl, "/register-provider", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function registerKey(baseUrl: string, payload: unknown) {
  return requestJson<{ ok: boolean; key: unknown }>(baseUrl, "/register-key", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function callMinibridge(baseUrl: string, payload: Record<string, unknown>) {
  const providerId = typeof payload.provider_id === "string" ? payload.provider_id : undefined;
  const path = providerId ? `/providers/${encodeURIComponent(providerId)}/call` : "/call";
  return requestJson<{ ok: boolean; response: unknown; receipt: Receipt; provider: ProviderDescriptor }>(baseUrl, path, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function verifyReceipt(baseUrl: string, payload: unknown) {
  return requestJson<{ ok: boolean; result: unknown }>(baseUrl, "/verify", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function registerRunner(baseUrl: string, payload: unknown) {
  return requestJson<{ ok: boolean; runner: RunnerRegistration }>(baseUrl, "/register-runner", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function submitJob(baseUrl: string, payload: unknown) {
  return requestJson<{ ok: boolean; job: HostJobRecord }>(baseUrl, "/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getJob(baseUrl: string, jobId: string) {
  return requestJson<{ ok: boolean; job: HostJobRecord }>(baseUrl, `/jobs/${encodeURIComponent(jobId)}`);
}

export async function getJobManifest(baseUrl: string, jobId: string) {
  return requestJson<{ ok: boolean; manifest: unknown; counts: unknown; attestation_verified: boolean }>(
    baseUrl,
    `/jobs/${encodeURIComponent(jobId)}/manifest`
  );
}

export async function getJobBundle(baseUrl: string, jobId: string) {
  return requestJson<{ ok: boolean; bundle: unknown }>(baseUrl, `/jobs/${encodeURIComponent(jobId)}/bundle`);
}

export async function verifyJob(baseUrl: string, jobId: string) {
  return requestJson<{ ok: boolean; result: unknown }>(baseUrl, `/jobs/${encodeURIComponent(jobId)}/verify`);
}
