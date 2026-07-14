import { useMemo, useState } from "react";
import {
  callMinibridge,
  getHealth,
  listProviders,
  listReceipts,
  registerKey,
  registerProvider,
  verifyReceipt,
} from "./api";
import type { ProviderDescriptor, Receipt } from "./types";

const demoRequest = {
  request_id: "req-ui-001",
  provider_id: "mock",
  caller_id: "bob-agent",
  owner_id: "alice",
  key_id: "alice-key",
  model: "gpt-demo",
  messages: [{ role: "user", content: "prove this call" }],
  parameters: { temperature: 0 },
  metadata: { source: "ui" },
  nonce: "nonce-ui-001",
  expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
};

const demoProvider = {
  provider_id: "mock",
  provider_kind: "mock",
};

const demoKey = {
  owner_id: "alice",
  key_id: "alice-key",
  api_key: "sk-demo-secret",
  policy: {
    allowed_callers: ["bob-agent"],
    allowed_models: ["gpt-demo"],
    spend_limit_usd: "1.00",
    require_nonce: true,
    require_expiry: true,
  },
};

function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

export default function App() {
  const [baseUrl, setBaseUrl] = useState(import.meta.env.VITE_MINIBRIDGE_API_URL || "/api");
  const [health, setHealth] = useState<unknown>(null);
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [providerPayload, setProviderPayload] = useState(formatJson(demoProvider));
  const [keyPayload, setKeyPayload] = useState(formatJson(demoKey));
  const [requestPayload, setRequestPayload] = useState(formatJson(demoRequest));
  const [verifyPayload, setVerifyPayload] = useState(
    formatJson({
      receipt: null,
      request: demoRequest,
    })
  );
  const [output, setOutput] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);

  const summary = useMemo(
    () => ({
      providers: providers.length,
      receipts: receipts.length,
    }),
    [providers.length, receipts.length]
  );

  async function loadOverview() {
    setError(null);
    try {
      const [healthResult, providersResult, receiptsResult] = await Promise.all([
        getHealth(baseUrl),
        listProviders(baseUrl),
        listReceipts(baseUrl),
      ]);
      setHealth(healthResult);
      setProviders(providersResult.providers);
      setReceipts(receiptsResult.receipts);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleProviderAdd() {
    setError(null);
    try {
      const payload = JSON.parse(providerPayload);
      setOutput(await registerProvider(baseUrl, payload));
      await loadOverview();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleKeyAdd() {
    setError(null);
    try {
      const payload = JSON.parse(keyPayload);
      setOutput(await registerKey(baseUrl, payload));
      await loadOverview();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleCall() {
    setError(null);
    try {
      const payload = JSON.parse(requestPayload);
      const result = await callMinibridge(baseUrl, payload);
      setOutput(result);
      setVerifyPayload(
        formatJson({
          receipt: result.receipt,
          request: payload,
          response: result.response,
        })
      );
      await loadOverview();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleVerify() {
    setError(null);
    try {
      const payload = JSON.parse(verifyPayload);
      setOutput(await verifyReceipt(baseUrl, payload));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Minibridge web UI</p>
          <h1>Proof receipts, API keys, and provider calls in one place.</h1>
          <p className="lede">
            This UI is a separate frontend app boundary. It talks only to the Minibridge HTTP API.
          </p>
        </div>
        <div className="status-card">
          <label>
            API base URL
            <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
          </label>
          <button onClick={loadOverview}>Refresh</button>
          <div className="status-grid">
            <div>
              <span>Providers</span>
              <strong>{summary.providers}</strong>
            </div>
            <div>
              <span>Receipts</span>
              <strong>{summary.receipts}</strong>
            </div>
          </div>
        </div>
      </header>

      <main className="grid">
        <section className="panel">
          <h2>Service status</h2>
          <pre>{formatJson(health ?? { ok: false, note: "Click Refresh" })}</pre>
        </section>

        <section className="panel">
          <h2>Providers</h2>
          <pre>{formatJson(providers)}</pre>
        </section>

        <section className="panel">
          <h2>Receipts</h2>
          <pre>{formatJson(receipts)}</pre>
        </section>

        <section className="panel">
          <h2>Register provider</h2>
          <textarea value={providerPayload} onChange={(event) => setProviderPayload(event.target.value)} />
          <button onClick={handleProviderAdd}>Submit</button>
        </section>

        <section className="panel">
          <h2>Register key</h2>
          <textarea value={keyPayload} onChange={(event) => setKeyPayload(event.target.value)} />
          <button onClick={handleKeyAdd}>Submit</button>
        </section>

        <section className="panel">
          <h2>Call Minibridge</h2>
          <textarea value={requestPayload} onChange={(event) => setRequestPayload(event.target.value)} />
          <button onClick={handleCall}>Call</button>
        </section>

        <section className="panel span-2">
          <h2>Verify receipt</h2>
          <textarea value={verifyPayload} onChange={(event) => setVerifyPayload(event.target.value)} />
          <button onClick={handleVerify}>Verify</button>
        </section>

        <section className="panel span-2">
          <h2>Latest result</h2>
          <pre>{formatJson(output)}</pre>
        </section>
      </main>

      {error ? <div className="error-banner">Error: {error}</div> : null}
    </div>
  );
}
