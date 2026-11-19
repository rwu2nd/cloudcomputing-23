// Load Balancing, Scalability and Fault Tolerance pages of the admin
// "System tests". Each page shows live numbers from the backend
// (/api/admin/demo/*, which reads Prometheus and the Kubernetes API), the
// matching Grafana panels, and buttons that run the demo.
//
// The buttons that change the cluster only work on Kubernetes after
// `make k8s-demo-on`; otherwise they are shown disabled with the reason.

import { useCallback, useEffect, useRef, useState } from "react";

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
    run(`Start a ${hold}-minute load test with ${users} users browsing and signing in?`,
      () => request("/demo/stress", { method: "POST", body: JSON.stringify({ scenarios: ["browse"], minutes: hold, browse_users: users }) }),
      (r) => { reload(); return `Started ${r.started}. Watch the backend pods and CPU below. (For bookings and chaos too, use the Stress Test page.)`; });
  const stop = () =>
    run("Stop the running load test?", () => request("/demo/stress", { method: "DELETE" }),
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
          Minutes
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



// ------------------------------------------------------------ run report

const fmtVal = (v, unit) => (v === null || v === undefined ? "–" : `${v}${unit === "%" ? "%" : unit ? ` ${unit}` : ""}`);

function reportMarkdown(r) {
  const c = r.config || {}, k = r.client || {}, sc = r.scaling || {}, j = r.journeys || {};
  const lines = [
    `# Stress test report: ${r.run}`, "",
    `- Started: ${r.started}  `, `- Duration: ${r.duration_seconds} s  `,
    `- Load: ${(c.scenarios || []).join(", ") || "browse"} (${c.intensity || "custom"})  `,
    `- Chaos: ${(c.chaos || []).join(", ") || "none"}`, "",
    `## SLO scorecard (${r.summary.slo_passed}/${r.summary.slo_total} passed)`, "",
    "| Objective | Target | Measured | Result |", "|---|---|---|---|",
    ...r.slo.map((x) => `| ${x.name} | ${x.target} | ${fmtVal(x.measured, x.unit)} | ${x.pass === null ? "n/a" : x.pass ? "PASS" : "FAIL"} |`),
    "", "## Client view (k6)", "",
    `- Requests: ${k.requests} (${k.requests_per_second}/s), failed ${k.failed} (${k.failed_percent}%)`,
    `- Availability: ${k.availability_percent}%`,
    `- Latency: p50 ${k.p50_ms} ms, p95 ${k.p95_ms} ms, p99 ${k.p99_ms} ms, max ${k.max_ms} ms`,
    `- Peak server throughput: ${r.peak_requests_per_second} requests/s`, "",
    "## Phases (server view, Prometheus)", "",
    "| Phase | Window | Requests | 5xx | Availability | p95 |", "|---|---|---|---|---|---|",
    ...r.phases.map((p) => `| ${p.name} | ${p.from_seconds}-${p.to_seconds} s | ${p.requests ?? "–"} | ${p.errors_5xx} | ${fmtVal(p.availability_percent, "%")} | ${fmtVal(p.p95_ms, "ms")} |`),
    "", "## Incidents", "",
    ...(r.incidents.length ? r.incidents.map((i) =>
      `- **${i.label}** at +${i.at_seconds} s: ${i.failures ?? 0} failed operations` +
      (i.outage_seconds !== undefined ? `, user-visible outage ${i.outage_seconds} s` : "") +
      (i.recovered_after_restore_seconds != null ? `, working again ${i.recovered_after_restore_seconds} s after the controller came back` : "") +
      (i.kind === "failover" && i.recovered_after_seconds != null ? `, recovered ${i.recovered_after_seconds} s after the kill` : "") +
      (i.injected_faults != null ? `, ${i.injected_faults} faults injected, ${i.unlocks_failed_for_users} unlock failures seen by users` : "") +
      (i.errors_by_route && Object.keys(i.errors_by_route).length ? `; 5xx by route: ${Object.entries(i.errors_by_route).map(([a, b]) => `${a} ${b}`).join(", ")}` : "")) : ["- none"]),
    "", "## Elasticity", "",
    sc.initial_pods != null ? `- Backend pods ${sc.initial_pods} -> ${sc.peak_pods}; first scale-out at +${sc.first_scale_out_seconds ?? "–"} s, peak at +${sc.peak_reached_seconds ?? "–"} s` : "- no data",
    "", "## Correctness and durability", "",
    `- Compartment holds checked: ${r.correctness.holds_checked}, held by two bookings at once: ${r.correctness.double_allocations}`,
    `- Parcels collected: ${j.parcels_collected_seen_by_clients} seen by clients, ${j.parcels_collected_in_database} in the database`,
    `- Luggage collected: ${j.luggage_collected_seen_by_clients} seen by clients, ${j.luggage_collected_in_database} in the database`,
    `- Unlocks retried: ${j.unlock_retries}, recovered by retrying: ${j.unlocks_recovered_by_retry}, given up: ${j.unlocks_given_up}`,
    `- Real-station bookings during the run: ${r.correctness.real_bookings} (${r.correctness.real_bookings_ok} allocated or completed)`,
    "",
  ];
  return lines.join("\n");
}

function reportCsv(r) {
  const rows = [["section", "metric", "value"]];
  const add = (sec, obj) => Object.entries(obj || {}).forEach(([k, v]) => typeof v !== "object" && rows.push([sec, k, v]));
  add("run", { run: r.run, started: r.started, duration_seconds: r.duration_seconds,
    scenarios: (r.config?.scenarios || []).join(" "), chaos: (r.config?.chaos || []).join(" "), intensity: r.config?.intensity });
  add("client", r.client); add("scaling", r.scaling); add("journeys", r.journeys); add("correctness", r.correctness);
  r.phases.forEach((p) => add(`phase:${p.name}`, p));
  r.incidents.forEach((i) => add(`incident:${i.kind}`, i));
  r.slo.forEach((x) => rows.push(["slo", x.name, `${x.measured ?? ""} (target ${x.target}) ${x.pass === null ? "n/a" : x.pass ? "PASS" : "FAIL"}`]));
  return rows.map((row) => row.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

function RunReport({ request, runName, finished }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState([]);
  const [selected, setSelected] = useState(null);

  const load = useCallback(async (name, refresh = false) => {
    setBusy(true); setError("");
    try {
      setReport(await request(`/demo/stress/report?${new URLSearchParams({ ...(name ? { run: name } : {}), ...(refresh ? { refresh: "true" } : {}) })}`));
      setHistory(await request("/demo/stress/reports"));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }, [request]);

  useEffect(() => { if (finished && runName) { setSelected(runName); load(runName); } }, [finished, runName, load]);
  useEffect(() => { request("/demo/stress/reports").then(setHistory).catch(() => {}); }, [request]);

  const cell = { padding: "4px 8px", borderBottom: "1px solid #edf1f7", fontSize: 12.5, textAlign: "left" };
  const r = report;
  return (
    <div style={{ marginTop: 14, borderTop: "1px solid #e1e7f0", paddingTop: 12 }}>
      <div className="admin-row">
        <small style={{ fontWeight: 800, color: "#475d77" }}>RUN REPORT{r ? ` · ${r.run}` : ""}</small>
        {r && (
          <div style={{ display: "flex", gap: 6 }}>
            <button onClick={() => download(`${r.run}.md`, reportMarkdown(r), "text/markdown")}>Markdown</button>
            <button onClick={() => download(`${r.run}.csv`, reportCsv(r), "text/csv")}>CSV</button>
            <button disabled={busy} onClick={() => load(r.run, true)} title="Recalculate from the logs and metrics">Rebuild</button>
          </div>
        )}
      </div>
      {busy && <p className="muted">Building the report from k6, Prometheus and the database…</p>}
      {error && <div className="error">{error}</div>}
      {!r && !busy && !error && <p className="muted">A report appears here when a run finishes.</p>}
      {r && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 6, marginTop: 8 }}>
            {[
              ["SLOs passed", `${r.summary.slo_passed} / ${r.summary.slo_total}`],
              ["Availability", fmtVal(r.client.availability_percent, "%")],
              ["Requests", r.client.requests],
              ["p95 / p99", r.client.p95_ms != null ? `${Math.round(r.client.p95_ms)} / ${Math.round(r.client.p99_ms)} ms` : "–"],
              ["Peak req/s", r.peak_requests_per_second],
              ["Pods", r.scaling ? `${r.scaling.initial_pods} → ${r.scaling.peak_pods}` : "–"],
            ].map(([k, v]) => (
              <div key={k} style={{ background: "#edf3fa", borderRadius: 9, padding: "7px 10px" }}>
                <small style={{ color: "#66788d", fontSize: 11 }}>{k}</small>
                <div style={{ fontSize: 16, fontWeight: 800 }}>{v ?? "–"}</div>
              </div>
            ))}
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 10 }}>
            <thead><tr>{["Objective", "Target", "Measured", ""].map((h) => <th key={h} style={{ ...cell, color: "#66788d" }}>{h}</th>)}</tr></thead>
            <tbody>
              {r.slo.map((x) => (
                <tr key={x.name}>
                  <td style={cell}>{x.name}</td><td style={cell}>{x.target}</td><td style={cell}>{fmtVal(x.measured, x.unit)}</td>
                  <td style={cell}>{x.pass === null ? <span className="pill">n/a</span> : <span className={`pill ${x.pass ? "good" : "bad"}`}>{x.pass ? "PASS" : "FAIL"}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <details style={{ marginTop: 8 }}>
            <summary style={{ cursor: "pointer", fontWeight: 700, color: "#2254ab", fontSize: 13 }}>Incidents, phases and correctness</summary>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, background: "#f6f8fc", padding: 10, borderRadius: 8 }}>
              {reportMarkdown(r).split("## Incidents")[1]?.replace(/\*\*/g, "")}
            </pre>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>{["Phase", "Window", "Requests", "5xx", "Availability", "p95"].map((h) => <th key={h} style={{ ...cell, color: "#66788d" }}>{h}</th>)}</tr></thead>
              <tbody>{r.phases.map((p) => (
                <tr key={p.name}><td style={cell}>{p.name}</td><td style={cell}>{p.from_seconds}–{p.to_seconds} s</td><td style={cell}>{p.requests ?? "–"}</td>
                  <td style={cell}>{p.errors_5xx}</td><td style={cell}>{fmtVal(p.availability_percent, "%")}</td><td style={cell}>{fmtVal(p.p95_ms, "ms")}</td></tr>
              ))}</tbody>
            </table>
          </details>
        </>
      )}
      {history.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary style={{ cursor: "pointer", fontWeight: 700, color: "#2254ab", fontSize: 13 }}>Past runs ({history.length})</summary>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <tbody>{history.map((h) => (
              <tr key={h.run_name} onClick={() => { setSelected(h.run_name); load(h.run_name); }}
                style={{ cursor: "pointer", background: selected === h.run_name ? "#eaf3ff" : undefined }}>
                <td style={cell}><code>{h.run_name}</code></td>
                <td style={cell}>{new Date(h.started_at).toLocaleString()}</td>
                <td style={cell}>{(h.config?.scenarios || []).join(", ")}{h.config?.chaos?.length ? ` + ${h.config.chaos.join(", ")}` : ""}</td>
                <td style={cell}>{fmtVal(h.summary?.availability_percent, "%")}</td>
                <td style={cell}>{h.summary ? `${h.summary.slo_passed}/${h.summary.slo_total} SLOs` : ""}</td>
              </tr>
            ))}</tbody>
          </table>
        </details>
      )}
    </div>
  );
}

// ------------------------------------------------------------ stress test

const LOAD_OPTIONS = [
  ["browse", "Browse & sign in", "CPU-heavy, drives autoscaling"],
  ["parcel", "Parcel journeys", "book → allocate → arrive → unlock over gRPC"],
  ["luggage", "Luggage sessions", "store → pay → unlock → collect"],
  ["churn", "Book & cancel churn", "hammers the allocation queue"],
];
const CHAOS_OPTIONS = [
  ["faults", "40% controller faults", "from 15% to 40% of the run"],
  ["controller", "Controller outage", "stopped at 45% for 10-30 s"],
  ["failover", "Kill DB primary", "at 70% of the run"],
];
const CHAOS_AT = { faults: 0.15, controller: 0.45, failover: 0.7 };
const LOCKER_COLORS = { free: "#d8f5e5", booked: "#ffe9b3", parcel: "#f7c3c3", luggage: "#cfe0ff" };

function Chip({ checked, onChange, title, hint, disabled }) {
  return (
    <label title={hint} style={{
      display: "flex", alignItems: "center", gap: 7, padding: "7px 10px", borderRadius: 9, cursor: "pointer",
      border: `1px solid ${checked ? "#3386d6" : "#d7e0ed"}`, background: checked ? "#eaf3ff" : "#fff",
      fontSize: 13, fontWeight: 650, color: "#163153", flexDirection: "row",
    }}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} style={{ margin: 0 }} />
      <span>{title}<br /><small style={{ fontWeight: 500, color: "#66788d" }}>{hint}</small></span>
    </label>
  );
}

const mmss = (sec) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;

function Stress({ request, status }) {
  const [view, , reload] = usePoll(request, "/demo/stress", 2000);
  const [load, setLoad] = useState({ browse: true, parcel: true, luggage: true, churn: false });
  const [chaos, setChaos] = useState({ faults: false, controller: false, failover: false });
  const [intensity, setIntensity] = useState("medium");
  const [minutes, setMinutes] = useState(2);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, run] = useAction(setMessage, setError);
  const consoleRef = useRef(null);
  const stick = useRef(true);

  const runInfo = view?.run;
  const running = runInfo && ["running", "starting"].includes(runInfo.state);
  const config = runInfo?.config || {};
  const total = (config.minutes || minutes) * 60;
  const elapsed = Math.min(view?.elapsed_seconds ?? 0, total);
  const m = view?.metrics || {};
  const res = view?.lockers?.reservations || {};
  const lug = view?.lockers?.luggage || {};
  const canAct = status?.demo_controls && !busy;
  const selected = Object.keys(load).filter((k) => load[k]);

  useEffect(() => {
    const el = consoleRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [view?.console]);

  const start = () =>
    run(null, () => request("/demo/stress", { method: "POST", body: JSON.stringify({
      scenarios: selected, chaos: Object.keys(chaos).filter((k) => chaos[k]), intensity, minutes,
    }) }), (r) => { reload(); return `Started ${r.started}.`; });
  const stop = () =>
    run("Stop the stress test now? Chaos is undone and the test lockers are freed.",
      () => request("/demo/stress", { method: "DELETE" }), (r) => { reload(); return `Stopped. Released ${r.released} test lockers.`; });
  const reset = () =>
    run(null, () => request("/demo/stress/reset", { method: "POST" }), (r) => { reload(); return `Released ${r.released} test lockers.`; });

  const tile = (label, value) => (
    <div style={{ background: "#edf3fa", borderRadius: 9, padding: "8px 10px", minWidth: 0 }}>
      <small style={{ color: "#66788d", fontSize: 11 }}>{label}</small>
      <div style={{ fontSize: 18, fontWeight: 800 }}>{value ?? "–"}</div>
    </div>
  );
  const outcome = (label, parts) => (
    <p style={{ margin: "5px 0", fontSize: 13 }}>
      <strong>{label}:</strong>{" "}
      {parts.filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(" · ") || <span className="muted">nothing yet</span>}
    </p>
  );

  return (
    <div className="panel">
      <p className="muted" style={{ marginTop: 0 }}>
        Runs real bookings, door unlocks and luggage sessions on 14 test lockers in{" "}
        <strong>Stress Test Bay</strong>. Users never see them and they do not count towards the real lockers.
        Everything ticked runs at the same time, inside the cluster.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 14 }}>
        <div>
          <small style={{ fontWeight: 800, color: "#475d77" }}>LOAD</small>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 6 }}>
            {LOAD_OPTIONS.map(([id, title, hint]) => (
              <Chip key={id} title={title} hint={hint} checked={load[id]} disabled={running}
                onChange={(v) => setLoad((o) => ({ ...o, [id]: v }))} />
            ))}
          </div>
        </div>
        <div>
          <small style={{ fontWeight: 800, color: "#475d77" }}>CHAOS DURING THE RUN</small>
          <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
            {CHAOS_OPTIONS.map(([id, title, hint]) => (
              <Chip key={id} title={title} hint={hint} checked={chaos[id]} disabled={running}
                onChange={(v) => setChaos((o) => ({ ...o, [id]: v }))} />
            ))}
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 12 }}>
        <select value={intensity} disabled={running} onChange={(e) => setIntensity(e.target.value)} style={{ padding: 8, borderRadius: 8 }}>
          <option value="low">Low intensity</option>
          <option value="medium">Medium intensity</option>
          <option value="high">High intensity</option>
        </select>
        <select value={minutes} disabled={running} onChange={(e) => setMinutes(Number(e.target.value))} style={{ padding: 8, borderRadius: 8 }}>
          {[1, 2, 3, 5].map((n) => <option key={n} value={n}>{n} min</option>)}
        </select>
        <button className="primary" style={{ padding: "9px 16px" }} disabled={!canAct || running || !selected.length} onClick={start}>
          {running ? "Running…" : "Start stress test"}
        </button>
        <button disabled={!canAct || !running} onClick={stop}>Stop</button>
        <button disabled={busy || running} onClick={reset} title="Free every test locker">Reset test lockers</button>
      </div>
      <ControlsNotice status={status} />
      {message && <div className="notice">{message}</div>}
      {error && <div className="error">{error}</div>}

      {runInfo && (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#4b6483" }}>
            <span><strong>{runInfo.name}</strong> · {runInfo.state} · {(config.scenarios || []).join(", ")}
              {(config.chaos || []).length ? ` · chaos: ${config.chaos.join(", ")}` : ""}</span>
            <span>{mmss(elapsed)} / {mmss(total)}</span>
          </div>
          <div style={{ position: "relative", height: 8, background: "#e3e9f2", borderRadius: 5, marginTop: 4 }}>
            <div style={{ width: `${(100 * elapsed) / total}%`, height: "100%", background: running ? "#3386d6" : "#6ab28a", borderRadius: 5, transition: "width 1s linear" }} />
            {(config.chaos || []).map((c) => (
              <div key={c} title={c} style={{ position: "absolute", left: `${CHAOS_AT[c] * 100}%`, top: -3, width: 3, height: 14, background: "#ae3535", borderRadius: 2 }} />
            ))}
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 6, marginTop: 12 }}>
        {tile("Backend pods", m.backend_pods != null ? (m.backend_pods_desired !== m.backend_pods ? `${m.backend_pods} → ${m.backend_pods_desired}` : m.backend_pods) : null)}
        {tile("Requests / s", m.requests_per_second != null ? m.requests_per_second.toFixed(0) : null)}
        {tile("5xx errors", m.error_rate_percent != null ? `${m.error_rate_percent.toFixed(1)}%` : null)}
        {tile("p95 latency", m.p95_latency_seconds != null ? `${(m.p95_latency_seconds * 1000).toFixed(0)} ms` : null)}
        {tile("Doors opened", m.doors_opened != null ? Math.round(m.doors_opened) : null)}
        {tile("Parcels collected", res.FULFILLED ?? (runInfo ? 0 : null))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 14, marginTop: 12 }}>
        <div>
          <small style={{ fontWeight: 800, color: "#475d77" }}>TEST LOCKERS</small>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
            {(view?.lockers?.lockers || []).map((l) => (
              <div key={l.id} title={`${l.label}: ${l.state}`} style={{
                width: 44, height: 30, borderRadius: 6, fontSize: 10, fontWeight: 700, display: "grid", placeItems: "center",
                background: LOCKER_COLORS[l.state], border: "1px solid #c9d5e3", transition: "background .3s",
              }}>{l.label.replace("T-", "")}</div>
            ))}
            {!view?.lockers && <span className="muted">Created on the first run.</span>}
          </div>
          <div style={{ display: "flex", gap: 10, fontSize: 11, color: "#66788d", marginTop: 6, flexWrap: "wrap" }}>
            {Object.entries({ free: "free", booked: "booked", parcel: "parcel inside", luggage: "luggage inside" }).map(([k, v]) => (
              <span key={k}><span style={{ display: "inline-block", width: 9, height: 9, borderRadius: 2, background: LOCKER_COLORS[k], border: "1px solid #c9d5e3" }} /> {v}</span>
            ))}
          </div>
        </div>
        <div>
          <small style={{ fontWeight: 800, color: "#475d77" }}>WHAT HAPPENED THIS RUN</small>
          {outcome("Parcels", [["collected", res.FULFILLED], ["in a locker", res.ARRIVED], ["allocated", res.ALLOCATED],
            ["waiting for a locker", res.CONDITIONAL], ["cancelled", res.CANCELLED]])}
          {outcome("Luggage", [["collected", lug.RETRIEVED], ["stored", (lug.STORED || 0) + (lug.PAYMENT_PENDING || 0) + (lug.PAID_AWAITING_PICKUP || 0)],
            ["cancelled", lug.CANCELLED]])}
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        <small style={{ fontWeight: 800, color: "#475d77" }}>CONSOLE</small>
        <div ref={consoleRef}
          onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 30; }}
          style={{ marginTop: 6, height: 190, overflowY: "auto", background: "#0f1b2d", color: "#cfe3ff", borderRadius: 10,
            padding: "8px 10px", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12, lineHeight: 1.5 }}>
          {(view?.console || []).length === 0 && <div style={{ color: "#7f93ad" }}>Output of the running test appears here.</div>}
          {(view?.console || []).map((line, i) => (
            <div key={i} style={{
              whiteSpace: "pre-wrap",
              color: /FAILED|ERROR/.test(line) ? "#ff9b9b" : line.includes("[chaos]") ? "#ffd27a" : undefined,
            }}>{line}</div>
          ))}
        </div>
      </div>

      {status?.kubernetes && (
        <RunReport request={request} runName={runInfo?.name}
          finished={Boolean(runInfo && ["succeeded", "failed"].includes(runInfo.state))} />
      )}

      <details style={{ marginTop: 12 }}>
        <summary style={{ cursor: "pointer", fontWeight: 700, color: "#2254ab" }}>Graphs</summary>
        <GrafanaPanel id={24} title="Availability" available={status?.prometheus} />
        <GrafanaPanel id={9} title="Requests / s per backend pod" available={status?.prometheus} />
        <GrafanaPanel id={14} title="Unlock calls by result" available={status?.prometheus} />
        <GrafanaPanel id={20} title="PostgreSQL instances" available={status?.prometheus} />
      </details>
    </div>
  );
}

export default function CloudDemo({ page, request }) {
  const [status] = usePoll(request, "/demo/status", 10000);
  const Page = { stress: Stress, balancing: Balancing, scalability: Scalability, fault: Fault }[page];
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
