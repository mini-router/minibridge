import { useMemo, useState } from "react";
import {
  getBundleManifest,
  getHealth,
  getJob,
  getJobManifest,
  listJobs,
  listRunners,
  registerRunner,
  submitJob,
  verifyJob,
} from "./api";
import type { BundleManifest, HostJobRecord, RunnerRegistration } from "./types";

const demoRunner = {
  runner_id: "runner-1",
  endpoint_url: "http://127.0.0.1:8081",
  notes: {
    region: "local",
    role: "cpu-tee-runner",
  },
};

const demoRequest = {
  request_id: "req-ui-001",
  provider_id: "mock",
  caller_id: "minibridge-maintainer",
  owner_id: "alice",
  key_id: "alice-key",
  model: "gpt-demo",
  messages: [{ role: "user", content: "prove this call" }],
  parameters: { temperature: 0 },
  metadata: { source: "ui" },
  nonce: "nonce-ui-001",
  expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
};

const demoJob = {
  job_type: "prove",
  runner_id: "runner-1",
  request: demoRequest,
};

function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

export default function App() {
  const [baseUrl, setBaseUrl] = useState(import.meta.env.VITE_MINIBRIDGE_HOST_URL || "/api");
  const [health, setHealth] = useState<unknown>(null);
  const [runners, setRunners] = useState<RunnerRegistration[]>([]);
  const [jobs, setJobs] = useState<HostJobRecord[]>([]);
  const [bundleManifest, setBundleManifest] = useState<BundleManifest | null>(null);
  const [runnerPayload, setRunnerPayload] = useState(formatJson(demoRunner));
  const [jobPayload, setJobPayload] = useState(formatJson(demoJob));
  const [output, setOutput] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);

  const latestJob = useMemo(() => jobs[jobs.length - 1] ?? null, [jobs]);
  const summary = useMemo(
    () => ({
      runners: runners.length,
      jobs: jobs.length,
      completedJobs: jobs.filter((job) => job.status === "completed").length,
      proofs: bundleManifest?.proof_count ?? 0,
    }),
    [bundleManifest?.proof_count, jobs, runners.length]
  );

  async function loadOverview() {
    setError(null);
    try {
      const [healthResult, runnersResult, jobsResult] = await Promise.all([
        getHealth(baseUrl),
        listRunners(baseUrl),
        listJobs(baseUrl),
      ]);
      setHealth(healthResult);
      setRunners(runnersResult.runners);
      setJobs(jobsResult.jobs);

      const candidateJob = jobsResult.jobs.find((job) => job.bundle?.manifest) ?? jobsResult.jobs[jobsResult.jobs.length - 1] ?? null;
      if (candidateJob?.bundle?.manifest) {
        setBundleManifest(candidateJob.bundle.manifest);
      } else if (candidateJob?.status === "completed") {
        const manifestResult = await getJobManifest(baseUrl, candidateJob.job_id);
        setBundleManifest(manifestResult.manifest as BundleManifest);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleRunnerAdd() {
    setError(null);
    try {
      const payload = JSON.parse(runnerPayload);
      setOutput(await registerRunner(baseUrl, payload));
      await loadOverview();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleJobSubmit() {
    setError(null);
    try {
      const payload = JSON.parse(jobPayload);
      const result = await submitJob(baseUrl, payload);
      setOutput(result);
      if (result.job.bundle?.manifest) {
        setBundleManifest(result.job.bundle.manifest);
      }
      await loadOverview();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleVerifyLatestJob() {
    setError(null);
    try {
      if (!latestJob) {
        throw new Error("no jobs available");
      }
      setOutput(await verifyJob(baseUrl, latestJob.job_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleLoadLatestJob() {
    setError(null);
    try {
      if (!latestJob) {
        throw new Error("no jobs available");
      }
      setOutput(await getJob(baseUrl, latestJob.job_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Minibridge host control plane</p>
          <h1>Runners, jobs, and bundle verification in one place.</h1>
          <p className="lede">
            The host stays public for orchestration and storage. CPU-TEE runners hold keys and produce proof bundles.
          </p>
        </div>
        <div className="status-card">
          <label>
            Host API base URL
            <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
          </label>
          <button onClick={loadOverview}>Refresh</button>
          <div className="status-grid">
            <div>
              <span>Runners</span>
              <strong>{summary.runners}</strong>
            </div>
            <div>
              <span>Jobs</span>
              <strong>{summary.jobs}</strong>
            </div>
            <div>
              <span>Completed</span>
              <strong>{summary.completedJobs}</strong>
            </div>
            <div>
              <span>Bundle proofs</span>
              <strong>{summary.proofs}</strong>
            </div>
          </div>
        </div>
      </header>

      <main className="grid">
        <section className="panel">
          <h2>Host status</h2>
          <pre>{formatJson(health ?? { ok: false, note: "Click Refresh" })}</pre>
        </section>

        <section className="panel">
          <h2>Runners</h2>
          <pre>{formatJson(runners)}</pre>
        </section>

        <section className="panel span-2">
          <h2>Jobs</h2>
          <pre>{formatJson(jobs)}</pre>
        </section>

        <section className="panel">
          <h2>Register runner</h2>
          <textarea value={runnerPayload} onChange={(event) => setRunnerPayload(event.target.value)} />
          <button onClick={handleRunnerAdd}>Register</button>
        </section>

        <section className="panel">
          <h2>Submit job</h2>
          <textarea value={jobPayload} onChange={(event) => setJobPayload(event.target.value)} />
          <div className="button-row">
            <button onClick={handleJobSubmit}>Submit</button>
            <button onClick={handleVerifyLatestJob}>Verify latest</button>
          </div>
        </section>

        <section className="panel span-2">
          <h2>Bundle manifest</h2>
          <div className="status-grid">
            <div>
              <span>Verified proofs</span>
              <strong>{bundleManifest?.proof_count ?? 0}</strong>
            </div>
            <div>
              <span>Raw proofs</span>
              <strong>{bundleManifest?.raw_proof_count ?? 0}</strong>
            </div>
            <div>
              <span>Attested</span>
              <strong>
                {bundleManifest == null ? "n/a" : bundleManifest.attestation_verified ? "yes" : "no"}
              </strong>
            </div>
            <div>
              <span>Merkle root</span>
              <strong>{bundleManifest?.merkle_root ? "set" : "n/a"}</strong>
            </div>
          </div>
          <pre>{formatJson(bundleManifest ?? { note: "No bundle loaded yet" })}</pre>
        </section>

        <section className="panel span-2">
          <h2>Latest result</h2>
          <div className="button-row">
            <button onClick={handleLoadLatestJob}>Load latest job</button>
          </div>
          <pre>{formatJson(output)}</pre>
        </section>
      </main>

      {error ? <div className="error-banner">Error: {error}</div> : null}
    </div>
  );
}
