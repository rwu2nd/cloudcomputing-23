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
  // Parcel collection states
  const [page, setPage] = useState("booking");
  const [parcels, setParcels] = useState([]);
  const [selectedParcel, setSelectedParcel] = useState(null);
  const [password, setPassword] = useState("");
  const [collectionMessage, setCollectionMessage] = useState("");
  const [loadingParcels, setLoadingParcels] = useState(false);


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

  // retrieve parcels that are ready for collection
async function receiveParcel() {
  setLoadingParcels(true);
  setCollectionMessage("");

  try {
    const response = await fetch(`${API_BASE_URL}/parcels/collection`);

    if (!response.ok) {
      throw new Error("Unable to retrieve parcels.");
    }

    const data = await response.json();

    setParcels(Array.isArray(data) ? data : []);
    setSelectedParcel(null);
    setPassword("");
    setPage("collection");
  } catch (error) {
    setCollectionMessage(error.message);
  } finally {
    setLoadingParcels(false);
  }
}

// verify collection password and unlock the locker
async function unlockLocker() {
  if (!selectedParcel || !password.trim()) {
    setCollectionMessage("Please enter your collection password.");
    return;
  }

  try {
    const response = await fetch(
      `${API_BASE_URL}/reservations/${selectedParcel.reservation_id}/unlock`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ password }),
      }
    );

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "Unable to unlock locker.");
    }

    setCollectionMessage("Locker unlocked successfully!");

    setParcels((previousParcels) =>
      previousParcels.filter(
        (parcel) =>
          parcel.reservation_id !== selectedParcel.reservation_id
      )
    );

    setSelectedParcel(null);
    setPassword("");
  } catch (error) {
    setCollectionMessage(error.message);
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
  // added parcel collection
return (
  <main className="page">
    <div className="card">
      {page === "booking" ? (
        <>
          <h1>Smart Locker System</h1>

          <form onSubmit={checkout}>
            <label className="field">
              Name
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>

            <label className="field">
              Parcel size
              <select
                value={size}
                onChange={(e) => setSize(e.target.value)}
              >
                <option value="S">Small</option>
                <option value="M">Medium</option>
                <option value="L">Large</option>
                <option value="XL">Extra large</option>
              </select>
            </label>

            <label className="field">
              Preferred station
              <select
                value={preferred}
                onChange={(e) => choosePreferred(e.target.value)}
              >
                <option value="">
                  {stations.length
                    ? "Choose a station..."
                    : "No stations available yet"}
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
              <select
                value={backup}
                onChange={(e) => setBackup(e.target.value)}
              >
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

          <button
            type="button"
            className="primary"
            style={{ marginTop: "12px" }}
            onClick={receiveParcel}
            disabled={loadingParcels}
          >
            {loadingParcels ? "Loading..." : "Receive Parcel"}
          </button>
        </>
      ) : (
        <>
          <h1>Receive Parcel</h1>

          {collectionMessage && (
            <p
              className={
                collectionMessage.includes("successfully")
                  ? "ok"
                  : "error"
              }
            >
              {collectionMessage}
            </p>
          )}

          {selectedParcel ? (
            <>
              <h2>Enter Collection Password</h2>

              <p>
                Station: {selectedParcel.station_name}
              </p>

              <p>
                Compartment: {selectedParcel.compartment_label}
              </p>

              <label className="field">
                Collection password
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                />
              </label>

              <button
                type="button"
                className="primary"
                onClick={unlockLocker}
              >
                Unlock Locker
              </button>

              <button
                type="button"
                onClick={() => {
                  setSelectedParcel(null);
                  setPassword("");
                  setCollectionMessage("");
                }}
              >
                Back to parcel list
              </button>
            </>
          ) : (
            <>
              <h2>Available Parcels</h2>

              {parcels.length === 0 ? (
                <p>
                  There are currently no parcels ready for collection.
                </p>
              ) : (
                parcels.map((parcel) => (
                  <div
                    key={parcel.reservation_id}
                    className="field"
                  >
                    <p>
                      <strong>Station:</strong>{" "}
                      {parcel.station_name}
                    </p>

                    <p>
                      <strong>Compartment:</strong>{" "}
                      {parcel.compartment_label}
                    </p>

                    <p>
                      <strong>Parcel size:</strong>{" "}
                      {parcel.required_size}
                    </p>

                    <button
                      type="button"
                      className="primary"
                      onClick={() => {
                        setSelectedParcel(parcel);
                        setCollectionMessage("");
                      }}
                    >
                      Select Parcel
                    </button>
                  </div>
                ))
              )}
            </>
          )}

          <button
            type="button"
            onClick={() => {
              setPage("booking");
              setSelectedParcel(null);
              setPassword("");
              setCollectionMessage("");
            }}
          >
            Back to booking
          </button>
        </>
      )}

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


