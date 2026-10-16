import { useEffect, useState } from "react";

// Base URL for API calls. In Docker/Kubernetes this is "/api" and nginx forwards
// it to the backend. Override at build time with VITE_API_BASE_URL if needed.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

// PLACEHOLDER - frontend teammate: replace this whole file with the real UI.
// It only checks that browser -> nginx -> backend -> database is wired up.
export default function App() {
  const [result, setResult] = useState({ loading: true });

  useEffect(() => {
    fetch(`${API_BASE_URL}/ping`)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => setResult({ data }))
      .catch((error) => setResult({ error: error.message }));
  }, []);

  return (
    <main className="page">
      <h1>Smart Locker System</h1>
      <p className="note">
        Infrastructure placeholder. Replace <code>src/App.jsx</code> with the real UI.
      </p>

      <section>
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
      </section>
    </main>
  );
}
