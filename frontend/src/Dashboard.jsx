import { useCallback, useEffect, useMemo, useState } from "react";
import "./dashboard.css";

// Same base URL convention as App.jsx: nginx forwards /api/* to the backend.
const API = import.meta.env.VITE_API_BASE_URL ?? "/api";
const SIZES = ["S", "M", "L", "XL"];
const REFRESH_MS = 5000;
const TEST_ZONES = ["TEST", "demo", "pytest"]; // hidden unless "Show test data" is ticked

// Plain-language names for the technical statuses.
const LABEL = {
  CONDITIONAL: "Waiting for space",
  ALLOCATED: "Locker reserved",
  ARRIVED: "Parcel in locker",
  FULFILLED: "Collected",
  CANCELLED: "Cancelled",
  UNALLOCATED: "Unallocated",
};
const FLOW = ["CONDITIONAL", "ALLOCATED", "ARRIVED", "FULFILLED"];
const ACTIVE = ["CONDITIONAL", "ALLOCATED", "ARRIVED"];

async function api(path, options = {}) {
  const { body, ...rest } = options;
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...rest,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const json = await res.json().catch(() => null);
  if (!res.ok) {
    const d = json?.detail;
    const msg = Array.isArray(d) ? d.map((x) => x.msg).join("; ") : typeof d === "string" ? d : d?.message;
    throw new Error(msg ?? `Request failed (${res.status})`);
  }
  return json;
}

const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "–");
function ago(iso) {
  if (!iso) return "–";
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
const tone = (pct) => (pct >= 85 ? "bad" : pct >= 60 ? "warn" : "good");

function Badge({ status }) {
  return <span className={`badge badge-${status}`}>{LABEL[status] ?? status}</span>;
}

export default function Dashboard() {
  const [data, setData] = useState({ stats: null, availability: [], compartments: [], reservations: [] });
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [auto, setAuto] = useState(true);
  const [updated, setUpdated] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showTest, setShowTest] = useState(false);
  const [selectedStation, setSelectedStation] = useState(null);
  const [filter, setFilter] = useState("ACTIVE"); // ACTIVE | ALL | a status
  const [openRes, setOpenRes] = useState(null);
  const [events, setEvents] = useState([]);
  const [tracking, setTracking] = useState("");
  const [parcel, setParcel] = useState(null);
  const [form, setForm] = useState({ customer_id: "", preferred: "", backups: [], size: "S", priority: 0 });
  const [demo, setDemo] = useState({ demand: 25, supply: 5, running: false, result: null });

  const load = useCallback(async () => {
    try {
      const [stats, availability, compartments, reservations] = await Promise.all([
        api("/stats"), api("/availability"), api("/compartments"), api("/reservations"),
      ]);
      setData({ stats, availability, compartments, reservations });
      setError("");
      setUpdated(new Date());
    } catch (e) {
      setError(`Can't reach the system: ${e.message}`);
    }
  }, []);

  useEffect(() => {
    load();
    if (!auto) return undefined;
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load, auto]);

  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(""), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  async function run(label, fn) {
    setBusy(true);
    try {
      setToast(`${label}: ${(await fn()) ?? "done"}`);
      setError("");
      await load();
    } catch (e) {
      setError(`${label} failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }
  const post = (path, method = "POST") => api(path, { method });

  const arrive = (id) => run("Parcel arrival", async () => {
    const r = await post(`/reservations/${id}/arrive`);
    return r.status === "CONDITIONAL" ? "no space free yet, still waiting" : "parcel placed in a locker";
  });
  const collect = (id) => run("Collection", async () => { await post(`/reservations/${id}/fulfill`); return "parcel collected, locker freed"; });
  const cancel = (id) => {
    if (window.confirm("Cancel this reservation?")) run("Cancel", async () => { await post(`/reservations/${id}`, "DELETE"); return "cancelled"; });
  };
  const processWaiting = () => run("Place waiting parcels", async () => `${(await post("/arrivals/process")).processed} processed (highest priority first)`);

  function createReservation(e) {
    e.preventDefault();
    const preferred = Number(form.preferred);
    if (!form.customer_id.trim() || !preferred) { setError("Enter a customer and choose a station."); return; }
    run("New booking", async () => {
      const r = await api("/reservations", { method: "POST", body: {
        customer_id: form.customer_id.trim(), preferred_station_id: preferred,
        backup_station_ids: form.backups.map(Number).filter((b) => b !== preferred),
        required_size: form.size, priority: Number(form.priority) || 0,
      } });
      setForm((f) => ({ ...f, customer_id: "" }));
      return `created ${r.id.slice(0, 8)}`;
    });
  }

  async function showEvents(id) {
    setOpenRes(openRes === id ? null : id);
    if (openRes !== id) {
      try { setEvents(await api(`/reservations/${id}/events`)); } catch (e) { setError(e.message); }
    }
  }

  async function lookupParcel(e) {
    e.preventDefault();
    if (!tracking.trim()) return;
    try { setParcel(await api(`/parcels/${encodeURIComponent(tracking.trim())}`)); setError(""); }
    catch (err) { setParcel(null); setError(`Parcel lookup: ${err.message}`); }
  }

  // Concurrency demo: many simultaneous arrivals for a few compartments.
  async function runDemo() {
    const { demand, supply } = demo;
    setDemo((d) => ({ ...d, running: true, result: null }));
    const ids = [];
    try {
      const st = await api("/stations", { method: "POST", body: { name: `demo-${Date.now().toString(36)}`, zone: "demo" } });
      for (let i = 0; i < supply; i++) {
        await api(`/stations/${st.id}/compartments`, { method: "POST", body: { label: `M${i + 1}`, size: "M" } });
      }
      const made = await Promise.all(Array.from({ length: demand }, (_, i) => api("/reservations", {
        method: "POST", body: { customer_id: `DEMO-${i + 1}`, preferred_station_id: st.id, required_size: "M" } })));
      ids.push(...made.map((m) => m.id));
      const t0 = performance.now();
      const res = await Promise.all(ids.map((id) => api(`/reservations/${id}/arrive`, { method: "POST" })));
      const ms = Math.round(performance.now() - t0);
      const placed = res.filter((r) => r.status === "ARRIVED");
      const comps = placed.map((r) => r.compartment_id);
      const duplicates = comps.length - new Set(comps).size;
      const ok = duplicates === 0 && placed.length === Math.min(supply, demand);
      setDemo((d) => ({ ...d, running: false, result: { demand, supply, placed: placed.length, waiting: demand - placed.length, duplicates, ms, ok } }));
    } catch (e) {
      setDemo((d) => ({ ...d, running: false, result: { error: e.message } }));
    } finally {
      await Promise.all(ids.map((id) => fetch(`${API}/reservations/${id}`, { method: "DELETE" }).catch(() => null)));
      load();
    }
  }

  // ---------- derived ----------
  const { stats, availability, compartments, reservations } = data;
  const allStations = stats?.stations ?? [];
  const stations = allStations.filter((s) => showTest || !TEST_ZONES.includes(s.zone));
  const visibleIds = new Set(stations.map((s) => s.station_id));
  const nameOf = Object.fromEntries(allStations.map((s) => [s.station_id, s.name]));
  const visibleRes = reservations.filter((r) => visibleIds.has(r.preferred_station_id));
  const count = (st) => visibleRes.filter((r) => r.status === st).length;
  const total = stations.reduce((n, s) => n + s.total_compartments, 0);
  const occupied = stations.reduce((n, s) => n + s.occupied_compartments, 0);
  const free = total - occupied;
  const pct = total ? Math.round((100 * occupied) / total) : 0;
  const integrity = stats?.integrity;
  const integrityOk = integrity && integrity.double_assignments === 0 && integrity.orphaned_compartments === 0;
  const waiting = visibleRes.filter((r) => r.status === "CONDITIONAL");
  const oldestWaiting = waiting.length ? waiting.reduce((a, b) => (new Date(a.created_at) < new Date(b.created_at) ? a : b)) : null;
  const soldOut = availability.filter((a) => visibleIds.has(a.station_id) && a.total > 0 && a.available === 0);
  const avail = useMemo(() => Object.fromEntries(availability.map((a) => [`${a.station_id}-${a.size}`, a])), [availability]);
  const cellsByStation = useMemo(() => {
    const m = {};
    compartments.forEach((c) => { (m[c.station_id] ??= []).push(c); });
    return m;
  }, [compartments]);
  const detailId = selectedStation ?? stations[0]?.station_id;
  const tableRows = visibleRes
    .filter((r) => (filter === "ALL" ? true : filter === "ACTIVE" ? ACTIVE.includes(r.status) : r.status === filter))
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    .slice(0, 50);

  const health = error ? { cls: "bad", text: error }
    : !stats ? { cls: "", text: "Loading…" }
    : integrityOk ? { cls: "good", text: "All systems normal – no double bookings detected" }
    : { cls: "bad", text: "Attention: the database integrity check found a problem" };

  return (
    <div className="dash">
      <header className={`status-bar ${health.cls}`}>
        <div className="status-text">
          <span className="dot-big" /> {health.text}
        </div>
        <div className="status-tools">
          <label><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Live</label>
          <label><input type="checkbox" checked={showTest} onChange={(e) => setShowTest(e.target.checked)} /> Show test data</label>
          <button onClick={load} disabled={busy}>Refresh</button>
          <span className="muted">{updated ? `Updated ${updated.toLocaleTimeString()}` : ""}</span>
          <a href="#/">Booking page</a>
        </div>
      </header>

      {toast && <div className="toast">{toast}</div>}

      <section className="hero">
        <div className="hero-main">
          <div className="hero-num">{free}</div>
          <div>
            <div className="hero-label">lockers free</div>
            <div className="muted">{occupied} in use of {total} · {pct}% full</div>
          </div>
        </div>
        <div className="meter big"><div className={tone(pct)} style={{ width: `${pct}%` }} /></div>
      </section>

      <section>
        <h2>Needs attention</h2>
        {!waiting.length && !soldOut.length ? (
          <p className="ok-line">✔ Nothing waiting and no size sold out.</p>
        ) : (
          <div className="attention">
            {waiting.length > 0 && (
              <div className="alert warn">
                <b>{waiting.length} parcel{waiting.length > 1 ? "s" : ""} waiting for space</b>
                <span>Oldest waiting {ago(oldestWaiting.created_at)}</span>
                <button disabled={busy} onClick={processWaiting}>Place waiting parcels now</button>
              </div>
            )}
            {soldOut.length > 0 && (
              <div className="alert bad">
                <b>Sold out</b>
                <span>{soldOut.map((a) => `${a.station_name} ${a.size}`).join(" · ")}</span>
              </div>
            )}
          </div>
        )}
      </section>

      <section>
        <h2>Parcel journey</h2>
        <div className="flow">
          {FLOW.map((st, i) => (
            <button key={st} className={`step badge-${st} ${filter === st ? "on" : ""}`} onClick={() => setFilter(filter === st ? "ACTIVE" : st)}>
              <span className="step-num">{count(st)}</span>
              <span>{LABEL[st]}</span>
              {i < FLOW.length - 1 && <span className="arrow">→</span>}
            </button>
          ))}
          <button className={`step badge-CANCELLED ${filter === "CANCELLED" ? "on" : ""}`} onClick={() => setFilter(filter === "CANCELLED" ? "ACTIVE" : "CANCELLED")}>
            <span className="step-num">{count("CANCELLED")}</span><span>Cancelled</span>
          </button>
        </div>
      </section>

      <section>
        <h2>Stations <span className="muted">(click one for detail)</span></h2>
        <div className="cards">
          {stations.map((s) => {
            const p = Math.round(s.utilisation_pct ?? 0);
            const cells = cellsByStation[s.station_id] ?? [];
            return (
              <div key={s.station_id} className={`scard ${tone(p)} ${detailId === s.station_id ? "sel" : ""}`} onClick={() => setSelectedStation(s.station_id)}>
                <div className="scard-top"><h3>{s.name}</h3><span className="zone">{s.zone}</span></div>
                <div className="big"><b>{s.available_compartments}</b> free <span className="muted">of {s.total_compartments}</span></div>
                <div className="meter"><div className={tone(p)} style={{ width: `${p}%` }} /></div>
                <div className="sizes">
                  {SIZES.map((size) => {
                    const a = avail[`${s.station_id}-${size}`];
                    if (!a) return null;
                    return <span key={size} className={`chip ${a.available === 0 ? "out" : ""}`}>{size} {a.available}/{a.total}</span>;
                  })}
                </div>
                <div className="dots">
                  {cells.map((c) => (
                    <i key={c.compartment_id} className={!c.active ? "inactive" : c.reserved ? c.reservation_status : "free"}
                       title={`${c.label} (${c.size}) – ${c.reserved ? `${c.customer_id}, ${LABEL[c.reservation_status]}` : "free"}`} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
        <div className="legend">
          <span><i className="free" /> free</span>
          <span><i className="ALLOCATED" /> reserved</span>
          <span><i className="ARRIVED" /> parcel inside</span>
        </div>
      </section>

      {detailId && (
        <section>
          <h2>{nameOf[detailId]} – compartments</h2>
          <div className="grid">
            {(cellsByStation[detailId] ?? []).map((c) => (
              <div key={c.compartment_id} className={`cell ${!c.active ? "inactive" : c.reserved ? c.reservation_status : "free"}`}
                   title={c.reserved ? `${c.customer_id} – ${LABEL[c.reservation_status]}` : "free"}>
                <b>{c.label}</b><small>{c.size}{c.reserved ? ` · ${c.customer_id}` : ""}</small>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <div className="row-head">
          <h2>Reservations</h2>
          <div className="seg">
            {[["ACTIVE", "Active"], ["ALL", "All"]].map(([k, t]) => (
              <button key={k} className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>{t}</button>
            ))}
            {!["ACTIVE", "ALL"].includes(filter) && <button className="on">{LABEL[filter]}</button>}
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Customer</th><th>Size</th><th>Station</th><th>Status</th><th>Created</th><th>Do</th></tr></thead>
            <tbody>
              {tableRows.map((r) => (
                <>
                  <tr key={r.id} className={openRes === r.id ? "selected" : ""}>
                    <td><b>{r.customer_id}</b><br /><button className="link" onClick={() => showEvents(r.id)}>#{r.id.slice(0, 8)} history</button></td>
                    <td>{r.required_size}{r.priority > 0 && <span className="prio" title="Priority">P{r.priority}</span>}</td>
                    <td>
                      {nameOf[r.allocated_station_id ?? r.preferred_station_id]}
                      {r.allocated_station_id && r.allocated_station_id !== r.preferred_station_id && <span className="prio" title="Redirected to a backup station">backup</span>}
                    </td>
                    <td><Badge status={r.status} /></td>
                    <td>{ago(r.created_at)}</td>
                    <td className="actions">
                      {["CONDITIONAL", "ALLOCATED"].includes(r.status) && <button disabled={busy} onClick={() => arrive(r.id)}>Parcel arrived</button>}
                      {["ALLOCATED", "ARRIVED"].includes(r.status) && <button disabled={busy} onClick={() => collect(r.id)}>Collected</button>}
                      {ACTIVE.includes(r.status) && <button className="danger" disabled={busy} onClick={() => cancel(r.id)}>Cancel</button>}
                    </td>
                  </tr>
                  {openRes === r.id && (
                    <tr key={`${r.id}-h`}><td colSpan="6" className="events">
                      {events.length ? events.map((ev, i) => (
                        <div key={i}>{fmt(ev.created_at)} – <b>{LABEL[ev.to_status] ?? ev.to_status}</b>{ev.station_id ? ` at ${nameOf[ev.station_id] ?? ev.station_id}` : ""}</div>
                      )) : "No history."}
                    </td></tr>
                  )}
                </>
              ))}
              {!tableRows.length && <tr><td colSpan="6" className="muted">Nothing here. 🎉</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="two-col">
        <details open>
          <summary><h2>New booking</h2></summary>
          <form onSubmit={createReservation}>
            <label className="field">Customer
              <input value={form.customer_id} onChange={(e) => setForm({ ...form, customer_id: e.target.value })} />
            </label>
            <div className="pair">
              <label className="field">Parcel size
                <select value={form.size} onChange={(e) => setForm({ ...form, size: e.target.value })}>
                  {SIZES.map((s) => <option key={s}>{s}</option>)}
                </select>
              </label>
              <label className="field">Priority (0–100)
                <input type="number" min="0" max="100" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} />
              </label>
            </div>
            <label className="field">Preferred station
              <select value={form.preferred} onChange={(e) => setForm({ ...form, preferred: e.target.value, backups: form.backups.filter((b) => b !== e.target.value) })}>
                <option value="">Choose…</option>
                {stations.map((s) => <option key={s.station_id} value={s.station_id}>{s.name} ({s.available_compartments} free)</option>)}
              </select>
            </label>
            <div className="field">Backup stations <span className="muted">(tried in the order you tick them)</span>
              <div className="checks">
                {stations.filter((s) => String(s.station_id) !== form.preferred).map((s) => (
                  <label key={s.station_id}>
                    <input type="checkbox" checked={form.backups.includes(String(s.station_id))}
                      onChange={(e) => setForm({ ...form, backups: e.target.checked ? [...form.backups, String(s.station_id)] : form.backups.filter((b) => b !== String(s.station_id)) })} />
                    {s.name}
                  </label>
                ))}
              </div>
            </div>
            <button className="primary" type="submit" disabled={busy}>Create booking</button>
          </form>
        </details>

        <details open>
          <summary><h2>Concurrency demo</h2></summary>
          <p className="muted">Creates a temporary station, then fires many arrivals at once for a few lockers. Correct behaviour: exactly as many parcels placed as lockers, and zero double bookings. (Browsers send only a few requests at a time; the pytest suite is the full stress test.)</p>
          <div className="pair">
            <label className="field">Simultaneous requests
              <input type="number" min="2" max="100" value={demo.demand} onChange={(e) => setDemo({ ...demo, demand: Math.min(100, Math.max(2, Number(e.target.value))) })} />
            </label>
            <label className="field">Lockers available
              <input type="number" min="1" max="50" value={demo.supply} onChange={(e) => setDemo({ ...demo, supply: Math.min(50, Math.max(1, Number(e.target.value))) })} />
            </label>
          </div>
          <button className="primary" onClick={runDemo} disabled={demo.running}>{demo.running ? "Running…" : "Run concurrency demo"}</button>
          {demo.result?.error && <p className="banner bad-bg">{demo.result.error}</p>}
          {demo.result && !demo.result.error && (
            <div className={`verdict ${demo.result.ok ? "good" : "bad"}`}>
              <div className="verdict-title">{demo.result.ok ? "✔ PASS – no double bookings" : "✖ FAIL – check the backend"}</div>
              <div className="kpis">
                <div><b>{demo.result.demand}</b><span>requests</span></div>
                <div><b>{demo.result.supply}</b><span>lockers</span></div>
                <div><b>{demo.result.placed}</b><span>placed</span></div>
                <div><b>{demo.result.waiting}</b><span>waiting</span></div>
                <div><b>{demo.result.duplicates}</b><span>double bookings</span></div>
                <div><b>{demo.result.ms} ms</b><span>total time</span></div>
              </div>
            </div>
          )}
          <hr />
          <h3>Find a parcel</h3>
          <form onSubmit={lookupParcel} className="inline-form">
            <input placeholder="SLK-XXXXXXXXXX" value={tracking} onChange={(e) => setTracking(e.target.value)} />
            <button type="submit">Look up</button>
          </form>
          {parcel && (
            <dl className="parcel">
              <dt>Tracking</dt><dd>{parcel.tracking_number}</dd>
              <dt>State</dt><dd>{parcel.state}</dd>
              <dt>Size</dt><dd>{parcel.size}</dd>
              <dt>Stored</dt><dd>{fmt(parcel.stored_at)}</dd>
              <dt>Collected</dt><dd>{fmt(parcel.collected_at)}</dd>
            </dl>
          )}
        </details>
      </section>
    </div>
  );
}
