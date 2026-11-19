// Load Balancing, Scalability and Fault Tolerance pages of the admin
// "System tests". Each page shows live numbers from the backend
// (/api/admin/demo/*, which reads Prometheus and the Kubernetes API), the
// matching Grafana panels, and buttons that run the demo.
//
// The buttons that change the cluster only work on Kubernetes after
// `make k8s-demo-on`; otherwise they are shown disabled with the reason.

import { useCallback, useEffect, useState } from "react";

const DASHBOARD = "/grafana/d-solo/smart-locker/smart-locker-cloud-overview";

function usePoll(request, path, intervalMs = 3000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setData(await request(path));
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }, [request, path]);
  useEffect(() => {
    load();
    const timer = setInterval(load, intervalMs);
    return () => clearInterval(timer);
  }, [load, intervalMs]);
  return [data, error, load];
}

function GrafanaPanel({ id, title, available }) {
  if (!available)
    return (
      <div className="notice">
        Live graph "{title}" needs Prometheus and Grafana (Kubernetes, or{" "}
        <code>docker compose --profile monitoring up -d</code>).
      </div>
    );
  return (
    <iframe
      title={title}
      src={`${DASHBOARD}?orgId=1&panelId=${id}&refresh=5s&from=now-10m&to=now&theme=light`}
      style={{ width: "100%", height: 260, border: 0, borderRadius: 12, marginTop: 12, background: "#fff" }}
    />
  );
}

function Metrics({ items }) {
  return (
    <div className="admin-metrics">
      {items.map(([label, value]) => (
        <div key={label}>
          <small>{label}</small>
          <strong>{value ?? "–"}</strong>
        </div>
      ))}
    </div>
  );
}

function Counts({ title, counts }) {
  const entries = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  return (
    <div style={{ marginTop: 14 }}>
      <h3>{title}</h3>
      {entries.length === 0 && <p className="muted">No data yet.</p>}
      {entries.map(([name, n]) => (
        <div key={name} style={{ display: "flex", alignItems: "center", gap: 10, margin: "6px 0" }}>
          <code style={{ width: 300, overflow: "hidden", textOverflow: "ellipsis" }}>{name}</code>
          <div style={{ flex: 1, background: "#edf3fa", borderRadius: 6 }}>
            <div style={{ width: `${(100 * n) / max}%`, background: "#3386d6", color: "#fff", borderRadius: 6, padding: "3px 8px", fontSize: 12, fontWeight: 700 }}>
              {n}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Pods({ title, pods }) {
  if (!pods) return null;
  return (
    <div style={{ marginTop: 14 }}>
      <h3>{title}</h3>
      <div className="admin-scroll">
        {pods.map((p) => (
          <p key={p.name} style={{ margin: "6px 0" }}>
            <span className={`pill ${p.ready ? "good" : "bad"}`}>{p.ready ? "ready" : p.phase}</span>{" "}
            <code>{p.name}</code>
            {p.role && <strong> · {p.role}</strong>}
            {p.restarts > 0 && <span className="muted"> · {p.restarts} restarts</span>}
          </p>
        ))}
      </div>
    </div>
  );
}

function ControlsNotice({ status }) {
  if (!status) return null;
  if (!status.kubernetes)
    return (
      <div className="notice">
        Running on Docker Compose: the buttons that change the cluster work on Kubernetes only.
      </div>
    );
  if (!status.demo_controls)
    return (
      <div className="notice">
        Demo actions are switched off. Turn them on with <code>make k8s-demo-on</code>{" "}
        (and off again with <code>make k8s-demo-off</code>).
      </div>
    );
  return null;
}

const fmt = (v, digits = 1, suffix = "") =>
  v === null || v === undefined ? null : `${Number(v).toFixed(digits)}${suffix}`;

function useAction(setMessage, setError) {
  const [busy, setBusy] = useState(false);
  const run = async (confirmText, fn, done) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    setError("");
    try {
      const result = await fn();
      setMessage(done(result));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return [busy, run];
}

// ------------------------------------------------------------ load balancing

function Balancing({ request, status }) {
  const [view] = usePoll(request, "/demo/balancing");
  const [http, setHttp] = useState(null);
  const [grpc, setGrpc] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, run] = useAction(setMessage, setError);

  const httpBurst = () =>
    run(null, async () => {
      const replies = await Promise.all(Array.from({ length: 30 }, () => request("/demo/whoami")));
      const counts = {};
      replies.forEach((r) => (counts[r.pod] = (counts[r.pod] || 0) + 1));
      setHttp(counts);
      return counts;
    }, (c) => `30 HTTP requests were answered by ${Object.keys(c).length} backend pod(s).`);
  const grpcBurst = () =>
    run(null, async () => {
      const r = await request("/demo/balancing/grpc", { method: "POST", body: JSON.stringify({ calls: 30 }) });
      setGrpc(r.per_replica);
      return r;
    }, (r) => `30 gRPC calls were answered by ${Object.keys(r.per_replica).length} controller pod(s) in ${r.elapsed_ms} ms.`);

  return (
    <div className="panel">
      <p>
        <strong>HTTP:</strong> the browser's requests go through nginx to the backend Service, which
        Kubernetes spreads over all backend pods. <strong>gRPC:</strong> the backend calls the
        locker-controller over long-lived HTTP/2 connections, so it balances the calls itself
        (round robin over a headless Service) instead of relying on Kubernetes.
      </p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button className="primary" disabled={busy} onClick={httpBurst}>Send 30 HTTP requests</button>
        <button className="primary" disabled={busy} onClick={grpcBurst}>Send 30 gRPC calls</button>
      </div>
      {message && <div className="notice">{message}</div>}
      {error && <div className="error">{error}</div>}
      {http && <Counts title="Which backend pod answered (HTTP)" counts={http} />}
      {grpc && <Counts title="Which controller pod answered (gRPC)" counts={grpc} />}
      <Pods title="Backend pods" pods={view?.backend_pods} />
      <Pods title="Locker-controller pods" pods={view?.controller_pods} />
      <GrafanaPanel id={9} title="Requests / s per backend pod" available={status?.prometheus} />
      <GrafanaPanel id={15} title="Doors opened per controller pod" available={status?.prometheus} />
    </div>
  );
}

// ------------------------------------------------------------ scalability

function Scalability({ request, status }) {
  const [view, , reload] = usePoll(request, "/demo/scalability");
  const [users, setUsers] = useState(40);
  const [hold, setHold] = useState(2);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, run] = useAction(setMessage, setError);
  const hpa = view?.hpa;
  const test = view?.load_test;
  const running = test && ["running", "starting"].includes(test.state);
  const canAct = status?.demo_controls;

  const start = () =>
    run(`Start a k6 load test with ${users} users for about ${hold + 2} minutes?`,
      () => request("/demo/scalability/loadtest", { method: "POST", body: JSON.stringify({ peak_users: users, hold_minutes: hold }) }),
      (r) => { reload(); return `Started ${r.started}. Watch the backend pods and CPU below.`; });
  const stop = () =>
    run("Stop the running load test?", () => request("/demo/scalability/loadtest", { method: "DELETE" }),
      () => { reload(); return "Load test stopped. The autoscaler scales back down after about a minute."; });

  return (
    <div className="panel">
      <p>
        A k6 load test runs <em>inside</em> the cluster and simulates users browsing lockers and
        signing in. When the backend pods' average CPU goes above the target, the
        HorizontalPodAutoscaler adds pods (up to {hpa?.max ?? 6}); when the load stops it removes them again.
      </p>
      <Metrics items={[
        ["Backend pods", hpa ? `${hpa.current} → ${hpa.desired}` : view?.backend_pods?.length],
        ["CPU vs target", hpa ? `${hpa.cpu_percent ?? "?"}% / ${hpa.target_percent}%` : null],
        ["Requests / s", fmt(view?.requests_per_second)],
        ["p95 latency", fmt(view?.p95_latency_seconds && view.p95_latency_seconds * 1000, 0, " ms")],
        ["Errors (5xx)", fmt(view?.error_rate_percent, 1, "%")],
        ["Load test", test ? test.state : "not run"],
      ]} />
      <ControlsNotice status={status} />
      <div className="admin-fields">
        <label>
          Peak users
          <select value={users} onChange={(e) => setUsers(Number(e.target.value))}>
            {[20, 40, 60, 80].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label>
          Minutes at peak
          <select value={hold} onChange={(e) => setHold(Number(e.target.value))}>
            {[1, 2, 3, 5].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>
      <div style={{ display: "flex", gap: 10 }}>
        <button className="primary" disabled={busy || !canAct || running} onClick={start}>
          {running ? "Load test running…" : "Start load test"}
        </button>
        <button disabled={busy || !canAct || !running} onClick={stop}>Stop</button>
      </div>
      {message && <div className="notice">{message}</div>}
      {error && <div className="error">{error}</div>}
      <Pods title="Backend pods" pods={view?.backend_pods} />
      <GrafanaPanel id={9} title="Requests / s per backend pod" available={status?.prometheus} />
      <GrafanaPanel id={10} title="CPU per backend pod" available={status?.prometheus} />
      <GrafanaPanel id={11} title="Latency percentiles" available={status?.prometheus} />
    </div>
  );
}

// ------------------------------------------------------------ fault tolerance

function Fault({ request, status }) {
  const [view, , reload] = usePoll(request, "/demo/fault", 2000);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, run] = useAction(setMessage, setError);
  const db = view?.database;
  const ctl = view?.controller;
  const canAct = status?.demo_controls && !busy;

  const failover = () =>
    run(`Delete the database primary (${db?.primary})? A replica should take over within seconds.`,
      () => request("/demo/fault/failover", { method: "POST" }),
      (r) => { reload(); return `Deleted ${r.deleted}. Watch the roles below change.`; });
  const scale = (n) =>
    run(n === 0 ? "Stop every locker-controller pod? Door unlocks will fail until it is back." : null,
      () => request("/demo/fault/controller", { method: "POST", body: JSON.stringify({ replicas: n }) }),
      () => { reload(); return n === 0 ? "Controller stopped. Try unlocking a locker in the app: it fails with 503, booking still works." : "Controller starting again."; });
  const faults = (rate) =>
    run(null, () => request("/demo/fault/faults", { method: "POST", body: JSON.stringify({ failure_rate: rate }) }),
      () => { reload(); return rate ? `Controller now fails ${rate * 100}% of calls on purpose. Unlocks should still work thanks to gRPC retries (the pods restart first).` : "Fault injection off."; });

  return (
    <div className="panel">
      <p>
        PostgreSQL runs as a CloudNativePG cluster: one primary and two streaming replicas. Kill the
        primary and a replica is promoted automatically. The door controller is a separate gRPC
        service: if it fails, only unlocking stops working, and short failures are hidden by retries.
      </p>
      <Metrics items={[
        ["Database", db ? db.phase : null],
        ["Primary", db?.primary],
        ["Instances ready", db ? `${db.ready} / ${db.instances}` : null],
        ["Controller pods", ctl ? `${ctl.ready} / ${ctl.replicas}` : null],
        ["Injected faults", ctl ? `${Math.round(ctl.failure_rate * 100)}%` : null],
        ["Errors (5xx)", fmt(view?.error_rate_percent, 1, "%")],
      ]} />
      <ControlsNotice status={status} />
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
        <button className="primary" disabled={!canAct || !db?.primary} onClick={failover}>Kill database primary</button>
        {ctl?.replicas === 0
          ? <button className="primary" disabled={!canAct} onClick={() => scale(2)}>Start controller (2 pods)</button>
          : <button className="primary" disabled={!canAct} onClick={() => scale(0)}>Stop controller</button>}
        {ctl?.failure_rate > 0
          ? <button disabled={!canAct} onClick={() => faults(0)}>Clear faults</button>
          : <button disabled={!canAct} onClick={() => faults(0.4)}>Inject 40% faults</button>}
      </div>
      {message && <div className="notice">{message}</div>}
      {error && <div className="error">{error}</div>}
      <Pods title="Database instances" pods={view?.database_pods} />
      <Pods title="Locker-controller pods" pods={view?.controller_pods} />
      <GrafanaPanel id={20} title="PostgreSQL instances" available={status?.prometheus} />
      <GrafanaPanel id={14} title="Unlock calls by result" available={status?.prometheus} />
      <GrafanaPanel id={12} title="Responses by status code" available={status?.prometheus} />
    </div>
  );
}

export default function CloudDemo({ page, request }) {
  const [status] = usePoll(request, "/demo/status", 10000);
  const Page = { balancing: Balancing, scalability: Scalability, fault: Fault }[page];
  return (
    <>
      {status?.prometheus && (
        <p className="muted">
          Full Grafana dashboard:{" "}
          <a href="/grafana/d/smart-locker/smart-locker-cloud-overview" target="_blank" rel="noreferrer">
            open in a new tab
          </a>
        </p>
      )}
      {Page ? <Page request={request} status={status} /> : null}
    </>
  );
}
