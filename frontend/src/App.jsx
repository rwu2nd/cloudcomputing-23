import { useEffect, useState } from "react";

// Base URL for API calls. In Docker/Kubernetes this is "/api" and nginx forwards
// it to the backend. Override at build time with VITE_API_BASE_URL if needed.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

export default function App() {
  const [stations, setStations] = useState([]);
  const [name, setName] = useState("");
  const [size, setSize] = useState("S");
  const [preferred, setPreferred] = useState(""); // station id
  const [backup, setBackup] = useState(""); // optional station id
  const [error, setError] = useState("");
  const [done, setDone] = useState(false); // false = form page, true = "coming soon" page


  // Load the station list once when the page opens
  useEffect(() => {
    fetch(`${API_BASE_URL}/stations`)
      .then((r) => {
        if (!r.ok) throw new Error();
        return r.json();
      })
      .then((data) => setStations(Array.isArray(data) ? data : []))
      .catch(() => setError("Could not load stations."));
      setStations([
       { id: 1, name: "Station A", zone: "North" },
       { id: 2, name: "Station B", zone: "South" },
       { id: 3, name: "Station C", zone: "East" },
      ]); // a mock data
  }, []);

  // Choosing a preferred station clears the backup if it is the same station
  function choosePreferred(id) {
    setPreferred(id);
    if (backup === id) setBackup();
  }

  async function checkout(e) {
    e.preventDefault();
    if (!name.trim() || !preferred) {
      setError("Enter your name and pick a preferred station.");
      return;
    }
    try {
      const res = await fetch(`${API_BASE_URL}/reservations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          customer_id: name.trim(),
          preferred_station_id: Number(preferred),
          backup_station_ids: backup ? [Number(backup)] : [],
          required_size: size,
        }),
      });
      if (!res.ok) throw new Error();
      setError("");
      setDone(true);
    } catch {
      setError("Booking failed, please try again.");
    }
  }

  // Second "page"
  if (done) {
    return (
      <main className="page">
        <div className="card">
          <h1>Thank you for your order</h1>
          <p>Coming soon: this part is still being built.</p>
          <button className="primary" onClick={() => setDone(false)}>
            Back
          </button>
        </div>
      </main>
    );
  }

  // First "page"
  return (
    <main className="page">
      <div className="card">
        <h1>Smart Locker System</h1>

        <form onSubmit={checkout}>
          <label className="field">
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>

          <label className="field">
            Parcel size
            <select value={size} onChange={(e) => setSize(e.target.value)}>
              <option value="S">Small</option>
              <option value="M">Medium</option>
              <option value="L">Large</option>
              <option value="XL">Extra large</option>
            </select>
          </label>

          <label className="field">
            Preferred station
            <select value={preferred} onChange={(e) => choosePreferred(e.target.value)}>
              <option value="">
                {stations.length ? "Choose a station..." : "No stations available yet"}
              </option>
              {stations.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.zone})
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            Backup station (optional)
            <select value={backup} onChange={(e) => setBackup(e.target.value)}>
              <option value="">No backup</option>
              {stations
                .filter((s) => String(s.id) !== preferred)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.zone})
                  </option>
                ))}
            </select>
          </label>

          {error && <p className="error">{error}</p>}
          <button className="primary" type="submit">
            Check out
          </button>
        </form>


      {/*later for debugging */}
      {/* {<section>
        <h2>Connectivity check</h2>
        {result.loading && <p>Checking...</p>}
        {result.error && (
          <p className="bad">Backend not reachable ({result.error})</p>
        )}
        {result.data && (
          <>
            <p className="ok">Backend reachable</p>
            <p className={result.data.database_reachable ? "ok" : "bad"}>
              {result.data.database_reachable
                ? "Database reachable from the backend"
                : "Database NOT reachable from the backend"}
            </p>
          </>
        )}
      </section>} */}
   
     </div>
    </main>
  );
}


