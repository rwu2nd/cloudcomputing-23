import { useCallback, useEffect, useState } from "react";

const sizeOptions = [
  ["S", "Small"],
  ["M", "Medium"],
  ["L", "Large"],
  ["XL", "Extra large"],
];

// Admin page: list customer accounts (id + name, from the database) and assign
// an incoming parcel to one of them. `request(path, options)` is AdminPanel's
// authenticated /api/admin fetch helper.
export default function AssignParcels({ request }) {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const [size, setSize] = useState("M");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [success, setSuccess] = useState("");

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await request("/users");
      setUsers(Array.isArray(result) ? result : []);
    } catch (e) {
      setError(e.message);
      setUsers([]);
    } finally {
      setLoading(false);
    }
  }, [request]);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  function pickUser(user) {
    setSelected(user);
    setSize("M");
    setDescription("");
    setSuccess("");
    setError("");
  }

  async function assign(event) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const parcel = await request("/parcels", {
        method: "POST",
        body: JSON.stringify({
          user_id: selected.id,
          size,
          description: description.trim() || null,
        }),
      });
      setSuccess(
        `Parcel ${parcel.id.slice(0, 8).toUpperCase()} (${size}) assigned to ${parcel.username}. They can now choose a locker for it.`,
      );
      setSelected(null);
      setDescription("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="admin-row">
        <div>
          <h2>Assign parcels</h2>
          <p className="muted">
            Choose a user to assign an incoming parcel to. Users can only
            reserve a locker for parcels assigned to them.
          </p>
        </div>
        <button onClick={loadUsers} disabled={loading}>
          {loading ? "Loading…" : "Refresh users"}
        </button>
      </div>

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {success && (
        <div className="ok" role="status">
          {success}
        </div>
      )}

      {selected && (
        <form className="panel" onSubmit={assign}>
          <div className="admin-row">
            <h3>Assign a parcel to {selected.username}</h3>
            <button type="button" onClick={() => setSelected(null)}>
              Cancel
            </button>
          </div>
          <p className="muted">
            User ID: <code>{selected.id}</code>
          </p>
          <div className="admin-fields">
            <label>
              Locker size needed
              <select value={size} onChange={(e) => setSize(e.target.value)}>
                {sizeOptions.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Description (optional)
              <input
                value={description}
                maxLength={200}
                placeholder="e.g. Online order from a retailer"
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
          </div>
          <button className="primary" disabled={busy}>
            {busy ? "Assigning…" : "Assign parcel"}
          </button>
        </form>
      )}

      <div className="panel">
        <div className="admin-row">
          <h3>Users ({users.length})</h3>
        </div>
        {loading ? (
          <div className="empty">Loading users…</div>
        ) : users.length === 0 && !error ? (
          <div className="empty">
            No user accounts yet. Users appear here after they register.
          </div>
        ) : (
          <div className="booking-list">
            {users.map((user) => (
              <article className="booking-card" key={user.id}>
                <div className="booking-head">
                  <div>
                    <strong>{user.username}</strong>
                    <p>
                      ID: <code>{user.id}</code>
                    </p>
                  </div>
                  <button
                    className="primary"
                    onClick={() => pickUser(user)}
                    disabled={busy}
                  >
                    Assign parcel
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
