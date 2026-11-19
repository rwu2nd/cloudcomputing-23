import { useCallback, useEffect, useState } from "react";
import AssignParcels from "./AssignParcels.jsx";

const API = import.meta.env.VITE_API_BASE_URL ?? "/api";
async function adminFetch(path, token, opts = {}) {
  const res = await fetch(`${API}/admin${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(opts.headers || {}),
    },
  });
  const data = await res.json().catch(() => null);
  if (!res.ok)
    throw new Error(
      typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`,
    );
  return data;
}
const emptyGroup = () => ({
  kind: "PARCEL_COLLECT",
  station_id: "",
  size: "M",
  count: 20,
});
const kinds = [
  ["PARCEL_COLLECT", "Parcel collection"],
  ["PARCEL_RETURN", "Parcel return"],
  ["LUGGAGE", "Luggage"],
];
const scenarios = [
  [
    "concurrency",
    "Concurrency Test",
    "Race real arrivals and verify allocations",
  ],
  ["balancing", "Load Balancing", "Awaiting pod instrumentation"],
  ["scalability", "Scalability", "Awaiting scaling metrics"],
  ["fault", "Fault Tolerance", "Awaiting Kubernetes test hooks"],
];

function GroupEditor({ value, onChange, locations }) {
  return (
    <div className="admin-fields">
      <label>
        Request type
        <select
          value={value.kind}
          onChange={(e) => {
            onChange("kind", e.target.value);
            onChange("size", e.target.value === "LUGGAGE" ? "XL" : "M");
          }}
        >
          {kinds.map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <label>
        Location
        <select
          value={value.station_id}
          onChange={(e) => onChange("station_id", e.target.value)}
        >
          <option value="">Choose A, B or C</option>
          {locations.map((l) => (
            <option key={l.key} value={l.station_id}>
              Location {l.key}
            </option>
          ))}
        </select>
      </label>
      <label>
        Size
        <select
          value={value.size}
          onChange={(e) => onChange("size", e.target.value)}
        >
          {["S", "M", "L", "XL"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </label>
      <label>
        Requests (1–100)
        <input
          type="number"
          min="1"
          max="100"
          value={value.count}
          onChange={(e) => onChange("count", e.target.value)}
        />
      </label>
    </div>
  );
}

export default function AdminPanel({ accessToken, account, onLogout }) {
  const [view, setView] = useState("testing");
  const [page, setPage] = useState("dashboard");
  const [snapshot, setSnapshot] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [group, setGroup] = useState(emptyGroup);
  const [specials, setSpecials] = useState([]);
  const [report, setReport] = useState(null);
  const [selected, setSelected] = useState(null);
  const refresh = useCallback(async () => {
    try {
      const data = await adminFetch("/lockers", accessToken);
      setSnapshot(data);
      setLastRefresh(new Date());
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [refresh]);
  const locations = snapshot?.locations ?? [];
  const adminRequest = useCallback(
    (path, opts) => adminFetch(path, accessToken, opts),
    [accessToken],
  );
  const setField = (field, value) =>
    setGroup((old) => ({ ...old, [field]: value }));
  function updateSpecial(i, field, value) {
    setSpecials((prev) =>
      prev.map((x, j) => (i === j ? { ...x, [field]: value } : x)),
    );
  }
  async function changeLocker(action, slot) {
    if (
      !window.confirm(
        action === "hold"
          ? `Reserve ${slot.label} as ADMIN-TEST-HOLD?`
          : `Force evict ${slot.customer_id || slot.reservation_id} from ${slot.label}? This cancels the reservation.`,
      )
    )
      return;
    setBusy(true);
    try {
      await adminFetch(
        action === "hold" ? "/hold" : `/lockers/${slot.id}/allocation`,
        accessToken,
        action === "hold"
          ? {
              method: "POST",
              body: JSON.stringify({ compartment_id: slot.id }),
            }
          : { method: "DELETE" },
      );
      setSelected(null);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function run() {
    const groups = [group, ...specials].map((x) => ({
      ...x,
      station_id: Number(x.station_id),
      count: Number(x.count),
    }));
    if (groups.some((x) => !x.station_id || x.count < 1)) {
      setError("Select a location and positive count for every request group.");
      return;
    }
    if (groups.reduce((n, x) => n + x.count, 0) > 100) {
      setError("Demo limit is 100 requests per test run.");
      return;
    }
    if (
      !window.confirm(
        "Send real booking and allocation requests to the shared database? Test allocations will remain until released.",
      )
    )
      return;
    setBusy(true);
    setReport(null);
    setError("");
    try {
      setReport(
        await adminFetch("/tests/concurrency", accessToken, {
          method: "POST",
          body: JSON.stringify({ groups }),
        }),
      );
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="main testing admin-main">
      <div className="admin-heading">
        <div>
          <div className="overline">DISTRIBUTED SYSTEM DEMONSTRATION</div>
          <h1>Admin dashboard</h1>
          <p className="muted">Signed in as {account?.username}</p>
        </div>
        <button className="secondary-action" onClick={onLogout}>
          Sign out
        </button>
      </div>
      <div className="admin-switch">
        <button
          className={view === "testing" ? "selected" : ""}
          onClick={() => setView("testing")}
        >
          Testing System
        </button>
        <button
          className={view === "lockers" ? "selected" : ""}
          onClick={() => setView("lockers")}
        >
          Locker Status
        </button>
        <button
          className={view === "parcels" ? "selected" : ""}
          onClick={() => setView("parcels")}
        >
          Assign Parcels
        </button>
      </div>
      <div className="panel" style={{ marginBottom: 20 }}>
        <small>
          Administrator controls are available only to accounts with the ADMIN
          database role.
        </small>
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
      </div>
      <section style={{ display: view === "testing" ? "block" : "none" }}>
        {page === "dashboard" ? (
          <>
            <h2>System tests</h2>
            <div className="admin-tiles">
              {scenarios.map(([id, name, desc]) => (
                <button
                  key={id}
                  className="admin-tile"
                  onClick={() => setPage(id)}
                >
                  <strong>{name}</strong>
                  <small>{desc}</small>
                  <span>Open →</span>
                </button>
              ))}
            </div>
          </>
        ) : (
          <>
            <button className="back" onClick={() => setPage("dashboard")}>
              ← All system tests
            </button>
            <h2>{scenarios.find((x) => x[0] === page)?.[1]}</h2>
            {page === "concurrency" ? (
              <div className="panel">
                <p>
                  Creates concurrent reservation and luggage-store operations,
                  then runs the real queue and simultaneous parcel-arrival
                  transitions against the shared database.
                </p>
                <h3>Main request group</h3>
                <GroupEditor
                  value={group}
                  locations={locations}
                  onChange={setField}
                />
                {specials.map((s, i) => (
                  <div className="admin-special" key={i}>
                    <div className="admin-row">
                      <h3>Special request group {i + 1}</h3>
                      <button
                        onClick={() =>
                          setSpecials((v) => v.filter((_, j) => j !== i))
                        }
                      >
                        Remove
                      </button>
                    </div>
                    <GroupEditor
                      value={s}
                      locations={locations}
                      onChange={(k, v) => updateSpecial(i, k, v)}
                    />
                  </div>
                ))}
                <button
                  onClick={() => setSpecials((v) => [...v, emptyGroup()])}
                  disabled={specials.length >= 3}
                >
                  + Add special request group
                </button>
                <p className="muted">
                  Total:{" "}
                  {[group, ...specials].reduce(
                    (a, x) => a + (Number(x.count) || 0),
                    0,
                  )}{" "}
                  requests · maximum 100 in this demo
                </p>
                <button
                  className="primary"
                  onClick={run}
                  disabled={busy || !locations.length}
                >
                  {busy ? "Running real requests…" : "Run concurrency test"}
                </button>
                <div className="notice">
                  Parcel requests enter the shared size-aware queue (XL, L, M,
                  then S; FIFO within each size). Luggage test sessions claim XL
                  compartments under the same PostgreSQL allocation lock. Test
                  allocations remain until released.
                </div>
                {report && (
                  <div className="admin-report">
                    <h3>Actual test result</h3>
                    <div className="admin-metrics">
                      {[
                        ["Requests", report.total_requested],
                        ["Created", report.created],
                        ["Allocated", report.allocated],
                        ["Conditional", report.conditional],
                        [
                          "Duplicate compartments",
                          report.duplicate_compartments.length,
                        ],
                        ["Errors", report.errors.length],
                      ].map(([k, v]) => (
                        <div key={k}>
                          <small>{k}</small>
                          <strong>{v}</strong>
                        </div>
                      ))}
                    </div>
                    <p>
                      Elapsed: {report.elapsed_seconds} seconds · Run ID:{" "}
                      <code>{report.run_id}</code>
                    </p>
                    <p>{report.note}</p>
                    <details>
                      <summary>
                        Individual request results ({report.results.length})
                      </summary>
                      <div className="admin-scroll">
                        {report.results.map((r) => (
                          <p key={r.id}>
                            <code>{r.id.slice(0, 8)}</code> · {r.kind} ·{" "}
                            {r.size} · {r.status} · Compartment{" "}
                            {r.compartment_id ?? "none"}
                          </p>
                        ))}
                      </div>
                    </details>
                  </div>
                )}
              </div>
            ) : (
              <div className="panel">
                <p>
                  This test is awaiting its real runner and metrics integration.
                  No simulated passing results will be displayed.
                </p>
              </div>
            )}
          </>
        )}
      </section>
      {view === "parcels" && (
        <section>
          <AssignParcels request={adminRequest} />
        </section>
      )}
      <section style={{ display: view === "lockers" ? "block" : "none" }}>
        <div className="admin-row">
          <div>
            <h2>Live locker status</h2>
            <p className="muted">
              Refreshes every 2 seconds from the shared PostgreSQL database.{" "}
              {lastRefresh
                ? `Last update ${lastRefresh.toLocaleTimeString()}`
                : ""}
            </p>
          </div>
          <button onClick={refresh}>Refresh now</button>
        </div>
        <div className="admin-locations">
          {locations.map((l) => (
            <div className="panel admin-location" key={l.key}>
              <h3>Location {l.key}</h3>
              <small>
                {l.station_name} ·{" "}
                {
                  l.compartments.filter((c) => c.active && !c.reservation_id)
                    .length
                }
                /{l.compartments.length} available
              </small>
              <div className="admin-slots">
                {l.compartments.map((c) => (
                  <button
                    disabled={!c.active}
                    key={c.id}
                    onClick={() => setSelected({ slot: c, location: l.key })}
                    className={`admin-slot ${!c.active ? "off" : c.reservation_id ? "occupied" : "free"}`}
                  >
                    <strong>{c.label}</strong>
                    <small>
                      {c.size} ·{" "}
                      {!c.active
                        ? "Inactive"
                        : c.reservation_id
                          ? "Reserved"
                          : "Available"}
                    </small>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
        {selected && (
          <div className="panel admin-selected">
            <div className="admin-row">
              <h3>
                Location {selected.location} — {selected.slot.label}
              </h3>
              <button onClick={() => setSelected(null)}>Close</button>
            </div>
            <p>
              Size: {selected.slot.size} ·{" "}
              {selected.slot.reservation_id ? "Reserved" : "Available"}
            </p>
            {selected.slot.reservation_id && (
              <>
                <p>Customer: {selected.slot.customer_id || "Unknown"}</p>
                <p>
                  Reservation: <code>{selected.slot.reservation_id}</code>
                </p>
              </>
            )}
            {selected.slot.reservation_id && (
              <button
                disabled={busy}
                className="primary"
                onClick={() => changeLocker("release", selected.slot)}
              >
                Force evict reservation
              </button>
            )}
          </div>
        )}
        {!locations.length && (
          <div className="notice">
            Check that the demo backend is running and admin demo endpoints are
            enabled to load Locations A, B, and C.
          </div>
        )}
      </section>
    </main>
  );
}
