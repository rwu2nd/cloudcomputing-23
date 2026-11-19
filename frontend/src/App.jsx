import AdminPanel from "./AdminPanel.jsx";
import LuggagePortal from "./LuggagePortal.jsx";
import { useCallback, useEffect, useState } from "react";
import "./index.css";
import NotificationViewer from './NotificationViewer.jsx';
import useNotifications from './useNotifications';

// near your other hooks; use whatever variable holds the access token

// In Docker/Kubernetes nginx forwards /api/* to FastAPI.
const API = import.meta.env.VITE_API_BASE_URL ?? "/api";
const AUTH_TOKEN_KEY = "parcelpoint-access-token";
const sizes = { S: "Small", M: "Medium", L: "Large", XL: "Extra large" };
const parcelStates = {
  AWAITING_LOCKER: "Awaiting locker",
  LOCKER_RESERVED: "Locker reserved",
  READY_FOR_COLLECTION: "Ready for collection",
};

async function request(path, options = {}) {
  let response;
  try {
    const token = sessionStorage.getItem(AUTH_TOKEN_KEY);
    const headers = {
      ...(options.headers || {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
    response = await fetch(`${API}${path}`, { ...options, headers });
  } catch {
    throw new Error("Unable to connect to the server.");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : "Request failed. Please try again.",
    );
  }
  return data;
}

const isTerminal = (status) =>
  ["CANCELLED", "FULFILLED", "UNALLOCATED"].includes(status);

function Pill({ children, tone = "neutral" }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

function Notice({ children }) {
  return (
    <div className="notice" role="status">
      {children}
    </div>
  );
}

function SectionChoice({ icon, title, sub, onClick }) {
  return (
    <button className="choice" onClick={onClick}>
      <span className="choice-icon">{icon}</span>
      <strong>{title}</strong>
      <small>{sub}</small>
      <span className="choice-arrow" aria-hidden="true">
        ↗
      </span>
    </button>
  );
}

export default function App() {
  const [account, setAccount] = useState(null);
  const [accessToken, setAccessToken] = useState(
    () => sessionStorage.getItem(AUTH_TOKEN_KEY) || "",
  );
  const [authMode, setAuthMode] = useState("login");
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);

  const [screen, setScreen] = useState("choose");
  const [stations, setStations] = useState([]);
  const [stationError, setStationError] = useState("");
  const [station, setStation] = useState("");
  const [size, setSize] = useState("M");
  const [availableCount, setAvailableCount] = useState(null);
  const [checkingAvailability, setCheckingAvailability] = useState(false);
  const [savingReservation, setSavingReservation] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState(null);

  // Parcels an administrator has assigned to this user (step before choosing a locker).
  const [myParcels, setMyParcels] = useState([]);
  const [loadingMyParcels, setLoadingMyParcels] = useState(false);
  const [myParcelsError, setMyParcelsError] = useState("");
  const [bookingParcel, setBookingParcel] = useState(null);

  // Parcels that have arrived and are ready to collect.
  const [parcels, setParcels] = useState([]);
  const [selectedParcel, setSelectedParcel] = useState(null);
  const [collectionMessage, setCollectionMessage] = useState("");
  const [collectionOk, setCollectionOk] = useState(false);
  const [loadingParcels, setLoadingParcels] = useState(false);
  const [unlockingParcel, setUnlockingParcel] = useState(false);

  const { notifications, unreadCount, markRead, reload } = useNotifications(accessToken);

  const loadStations = useCallback(async () => {
    try {
      setStationError("");
      const result = await request("/stations");
      setStations(
        (Array.isArray(result) ? result : []).filter(
          (item) => item.active !== false,
        ),
      );
    } catch (e) {
      setStationError(e.message);
      setStations([]);
    }
  }, []);

  useEffect(() => {
    loadStations();
  }, [loadStations]);

  // Poll the selected station's actual compartment inventory. The API reads
  // locker_compartments, so the count reflects current database reservations.
  useEffect(() => {
    if (!station || !size) {
      setAvailableCount(null);
      setCheckingAvailability(false);
      return undefined;
    }

    let active = true;
    setCheckingAvailability(true);
    setAvailableCount(null);

    const refreshAvailability = async () => {
      try {
        const compartments = await request(
          `/stations/${encodeURIComponent(station)}/compartments`,
        );
        if (active) {
          const count = (
            Array.isArray(compartments) ? compartments : []
          ).filter(
            (item) => item.active && !item.reserved && item.size === size,
          ).length;
          setAvailableCount(count);
          setStationError("");
        }
      } catch (e) {
        if (active) {
          setAvailableCount(null);
          setStationError(e.message);
        }
      } finally {
        if (active) setCheckingAvailability(false);
      }
    };

    refreshAvailability();
    const timer = setInterval(refreshAvailability, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [station, size]);

  const loadParcels = useCallback(async () => {
    setLoadingParcels(true);
    setCollectionMessage("");
    setCollectionOk(false);
    try {
      // This endpoint is already scoped to the authenticated account.
      const result = await request("/parcels/collection");
      setParcels(Array.isArray(result) ? result : []);
      setSelectedParcel(null);
    } catch (e) {
      setCollectionMessage(e.message);
      setCollectionOk(false);
    } finally {
      setLoadingParcels(false);
    }
  }, []);

  const loadMyParcels = useCallback(async () => {
    setLoadingMyParcels(true);
    setMyParcelsError("");
    try {
      // Scoped to the signed-in account by the backend.
      const result = await request("/parcels");
      setMyParcels(Array.isArray(result) ? result : []);
    } catch (e) {
      setMyParcelsError(e.message);
      setMyParcels([]);
    } finally {
      setLoadingMyParcels(false);
    }
  }, []);

  function goTo(next) {
    setError("");
    setScreen(next);
    if (next === "notifications") {
      notifications
          .filter((n) => !n.read)
          .forEach((n) => markRead(n.id));
    }
    if (next === "myparcels") {
      setBookingParcel(null);
      setStation("");
      setAvailableCount(null);
      loadMyParcels();
    }
    if (next === "collect") {
      setSelectedParcel(null);
      loadParcels();
    }
  }

  // A locker can only be chosen for one of the user's assigned parcels; the
  // locker size is fixed by that parcel.
  function chooseParcel(parcel) {
    setError("");
    setBookingParcel(parcel);
    setSize(parcel.size);
    setStation("");
    setAvailableCount(null);
    setScreen("new");
  }

  async function unlockParcel() {
    if (!selectedParcel) return;
    setUnlockingParcel(true);
    setCollectionMessage("");
    try {
      await request(`/reservations/${selectedParcel.reservation_id}/unlock`, {
        method: "POST",
      });
      setCollectionOk(true);
      setCollectionMessage(
        "Locker unlocked successfully. Your parcel has been marked as collected.",
      );
      setSelectedParcel(null);
      const updated = await request("/parcels/collection");
      setParcels(Array.isArray(updated) ? updated : []);
    } catch (e) {
      setCollectionOk(false);
      setCollectionMessage(e.message);
    } finally {
      setUnlockingParcel(false);
    }
  }

  // Once a reservation is allocated, this demo transitions it to ARRIVED so
  // the parcel appears under Collect Parcel.
  const receiptArrived = receipt?.status === "ARRIVED";
  useEffect(() => {
    if (
      screen !== "confirmed" ||
      !receipt?.id ||
      receiptArrived ||
      isTerminal(receipt.status)
    ) {
      return undefined;
    }

    let cancelled = false;
    let timer;
    const checkReservation = async () => {
      try {
        const latest = await request(`/reservations/${receipt.id}`);
        if (cancelled) return;
        setReceipt((current) =>
          current?.id === latest.id ? { ...current, ...latest } : current,
        );

        if (latest.status === "ALLOCATED") {
          const arrived = await request(`/reservations/${receipt.id}/arrive`, {
            method: "POST",
          });
          if (!cancelled) {
            setReceipt((current) =>
              current?.id === latest.id
                ? { ...current, ...latest, ...arrived }
                : current,
            );
          }
        } else if (latest.status === "CONDITIONAL") {
          timer = setTimeout(checkReservation, 1200);
        }
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
    };

    if (receipt.status === "CONDITIONAL" || receipt.status === "ALLOCATED") {
      checkReservation();
    }

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [screen, receipt?.id, receiptArrived]);

  async function bookParcel(event) {
    event.preventDefault();
    setError("");
    if (!bookingParcel) return setError("Choose one of your parcels first.");
    if (!station) return setError("Select a locker location.");
    if (availableCount === null || availableCount === 0) {
      return setError(
        "No suitable locker is currently available. Refresh or choose another location.",
      );
    }

    setSavingReservation(true);
    try {
      // The backend derives customer_id from the bearer token and the locker
      // size from the assigned parcel; the browser only sends the choices.
      const result = await request("/reservations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          station_id: Number(station),
          parcel_id: bookingParcel.id,
        }),
      });
      setReceipt(result);
      setScreen("confirmed");
    } catch (e) {
      setError(e.message);
    } finally {
      setSavingReservation(false);
    }
  }

  useEffect(() => {
    if (!accessToken) {
      setAccount(null);
      return undefined;
    }
    let active = true;
    fetch(`${API}/luggage/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
      .then(async (response) => {
        const data = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(
            data?.detail || "Your session has expired. Please sign in again.",
          );
        }
        if (active) setAccount(data);
      })
      .catch(() => {
        sessionStorage.removeItem(AUTH_TOKEN_KEY);
        if (active) {
          setAccessToken("");
          setAccount(null);
        }
      });
    return () => {
      active = false;
    };
  }, [accessToken]);

  async function authenticate(event) {
    event.preventDefault();
    setAuthBusy(true);
    setAuthError("");
    try {
      const endpoint =
        authMode === "register"
          ? "/luggage/auth/register"
          : "/luggage/auth/login";
      const response = await fetch(`${API}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: authUsername,
          password: authPassword,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(
          typeof data?.detail === "string" ? data.detail : "Unable to sign in.",
        );
      }
      if (!data?.access_token || !data?.user) {
        throw new Error(
          "The server did not return a login token. Check the luggage authentication endpoints.",
        );
      }
      if (data.user.role === "ADMIN" && authMode === "register") {
        throw new Error("Administrator accounts cannot be self-registered.");
      }
      sessionStorage.setItem(AUTH_TOKEN_KEY, data.access_token);
      setAccessToken(data.access_token);
      setAccount(data.user);
      setAuthPassword("");
      setScreen("choose");
    } catch (e) {
      setAuthError(e.message);
    } finally {
      setAuthBusy(false);
    }
  }

  async function signOut() {
    try {
      await fetch(`${API}/luggage/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      });
    } catch {
      // Clear the browser session even when the API cannot be reached.
    }
    sessionStorage.removeItem(AUTH_TOKEN_KEY);
    setAccessToken("");
    setAccount(null);
    setScreen("choose");
    setReceipt(null);
    setParcels([]);
    setMyParcels([]);
    setBookingParcel(null);
  }

  const stationLabel = (id) =>
    stations.find((item) => String(item.id) === String(id))?.name ||
    `Station ${id}`;

  if (!account) {
    return (
      <div className="app auth-app">
        <header className="top">
          <div className="top-inner">
            <div className="brand">
              <span className="brand-symbol">▦</span>
              <span>
                parcel<span className="brand-blue">point</span>
                <small>SMART LOCKER SYSTEM</small>
              </span>
            </div>
          </div>
        </header>
        <main className="main auth-main">
          <div className="overline">WELCOME TO PARCELPOINT</div>
          <h1>Sign in to continue</h1>
          <p className="subtitle">
            Access parcel and luggage services, or sign in to the administrator
            dashboard.
          </p>
          <form className="panel auth-panel" onSubmit={authenticate}>
            <div className="auth-toggle">
              <button
                type="button"
                className={authMode === "login" ? "selected" : ""}
                onClick={() => {
                  setAuthMode("login");
                  setAuthError("");
                }}
              >
                Login
              </button>
              <button
                type="button"
                className={authMode === "register" ? "selected" : ""}
                onClick={() => {
                  setAuthMode("register");
                  setAuthError("");
                }}
              >
                Create user account
              </button>
            </div>
            <label>
              Username
              <input
                required
                minLength={3}
                maxLength={32}
                value={authUsername}
                onChange={(e) => setAuthUsername(e.target.value)}
                autoComplete="username"
              />
            </label>
            <label>
              Password
              <input
                required
                minLength={authMode === "register" ? 10 : 1}
                maxLength={128}
                type="password"
                value={authPassword}
                onChange={(e) => setAuthPassword(e.target.value)}
                autoComplete={
                  authMode === "register" ? "new-password" : "current-password"
                }
              />
            </label>
            {authMode === "register" && (
              <small>Password must be at least 10 characters.</small>
            )}
            {authError && (
              <div className="error" role="alert">
                {authError}
              </div>
            )}
            <button className="primary submit" disabled={authBusy}>
              {authBusy
                ? "Please wait…"
                : authMode === "login"
                  ? "Login"
                  : "Create account"}
            </button>
          </form>
        </main>
        <footer>ParcelPoint · Distributed systems coursework prototype</footer>
      </div>
    );
  }

  if (account.role === "ADMIN") {
    return (
      <AdminPanel
        accessToken={accessToken}
        account={account}
        onLogout={signOut}
      />
    );
  }

  return (
    <div className="app">
      <header className="top">
        <div className="top-inner">
          <div className="brand">
            <span className="brand-symbol">▦</span>
            <span>
              parcel<span className="brand-blue">point</span>
              <small>SMART LOCKER SYSTEM</small>
            </span>
          </div>
          <div className="user-session">
            <span style={{ position: 'relative', display: 'inline-flex' }}>
              <button
                type="button"
                className="notification-shortcut"
                onClick={() => goTo("notifications")}
                aria-label={unreadCount > 0 ? `Open notifications (${unreadCount} unread)` : 'Open notifications'}
                title="Notifications"
                style={{ /* your existing styles, unchanged */ }}
              >
                <span aria-hidden="true">🔔</span>
              </button>
              {unreadCount > 0 && (
                <span
                  aria-hidden="true"
                  style={{
                    position: 'absolute',
                    top: '-4px',
                    right: '-4px',
                    minWidth: '18px',
                    height: '18px',
                    padding: '0 5px',
                    boxSizing: 'border-box',
                    borderRadius: '9px',
                    background: '#e5484d',
                    color: '#fff',
                    fontSize: '11px',
                    fontWeight: 700,
                    lineHeight: '18px',
                    textAlign: 'center',
                    pointerEvents: 'none',
                  }}
                >
                  {unreadCount > 99 ? '99+' : unreadCount}
                </span>
              )}
            </span>
            <span>
              Signed in as <strong>{account.username}</strong>
            </span>
            <button onClick={signOut}>Sign out</button>
          </div>
        </div>
      </header>

      <main className="main">
        {screen === "choose" && (
          <>
            <div className="overline">BOOK A LOCKER</div>
            <h1>Parcel or luggage?</h1>
            <p className="subtitle">Choose a service to get started.</p>
            <div className="choice-grid">
              <SectionChoice
                icon="▣"
                title="Parcel"
                sub="Reserve a locker or collect an incoming parcel."
                onClick={() => goTo("parcel")}
              />
              <SectionChoice
                icon="▤"
                title="Luggage"
                sub="Store your bags in an extra-large locker."
                onClick={() => goTo("luggage")}
              />
              <SectionChoice icon="◔" title="Notifications" sub="Live gRPC notification demo." onClick={() => goTo('notifications')} />
          </div>
          </>
        )}


        {screen === 'notifications' && <NotificationViewer accessToken={accessToken} account={account} onBack={() => goTo('choose')} />}
          
        {screen === "parcel" && (
          <>
            <button className="back" onClick={() => goTo("choose")}>
              ← Back
            </button>
            <div className="overline">PARCEL SERVICES</div>
            <h1>What would you like to do?</h1>
            <p className="subtitle">Manage your incoming parcel.</p>
            <div className="choice-grid">
              <SectionChoice
                icon="▣"
                title="Choose locker for parcel"
                sub="Pick one of your incoming parcels and reserve a compartment for it."
                onClick={() => goTo("myparcels")}
              />
              <SectionChoice
                icon="↓"
                title="Collect parcel"
                sub="View parcels ready for collection and unlock your locker."
                onClick={() => goTo("collect")}
              />
            </div>
          </>
        )}

        {screen === "luggage" && (
          <LuggagePortal
            accessToken={accessToken}
            account={account}
            onBack={() => goTo("choose")}
            notifications={notifications}
            markRead={markRead}
            reload={reload} 
          />
        )}

        {screen === "myparcels" && (
          <>
            <button className="back" onClick={() => goTo("parcel")}>
              ← Parcel Services
            </button>
            <div className="overline">PARCEL SERVICES</div>
            <h1>Your incoming parcels</h1>
            <p className="subtitle">
              Choose the parcel you want to reserve a locker for. Only parcels
              assigned to your account are shown.
            </p>
            <div className="panel">
              <div className="booking-head">
                <h2>Your parcels</h2>
                <button
                  type="button"
                  onClick={loadMyParcels}
                  disabled={loadingMyParcels}
                >
                  {loadingMyParcels ? "Refreshing…" : "Refresh"}
                </button>
              </div>
              {myParcelsError && (
                <div className="error" role="alert">
                  {myParcelsError}
                </div>
              )}
              {loadingMyParcels ? (
                <div className="empty">Loading parcels…</div>
              ) : myParcels.length === 0 && !myParcelsError ? (
                <div className="empty">
                  You have no incoming parcels. Parcels appear here once an
                  administrator assigns them to your account.
                </div>
              ) : (
                <div className="booking-list">
                  {myParcels.map((parcel) => (
                    <article className="booking-card" key={parcel.id}>
                      <div className="booking-head">
                        <strong>
                          {parcel.description ||
                            `Parcel ${parcel.id.slice(0, 8).toUpperCase()}`}
                        </strong>
                        <Pill
                          tone={
                            parcel.state === "AWAITING_LOCKER"
                              ? "neutral"
                              : "good"
                          }
                        >
                          {parcelStates[parcel.state] || parcel.state}
                        </Pill>
                      </div>
                      <p>
                        {sizes[parcel.size] || parcel.size} locker needed
                        {parcel.station_name && parcel.compartment_label
                          ? ` · ${parcel.station_name}, compartment ${parcel.compartment_label}`
                          : ""}
                      </p>
                      {parcel.state === "AWAITING_LOCKER" ? (
                        <button
                          type="button"
                          className="primary"
                          onClick={() => chooseParcel(parcel)}
                        >
                          Choose locker
                        </button>
                      ) : (
                        <small>
                          {parcel.state === "READY_FOR_COLLECTION"
                            ? "Go to Collect Parcel to unlock this locker."
                            : "A locker is already reserved for this parcel."}
                        </small>
                      )}
                    </article>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {screen === "new" && bookingParcel && (
          <>
            <button className="back" onClick={() => goTo("myparcels")}>
              ← Your parcels
            </button>
            <div className="overline">PARCEL SERVICES</div>
            <h1>Choose your locker</h1>
            <p className="subtitle">
              Select a location to see live availability from the database.
              The locker size is set by your parcel.
            </p>
            <form className="panel" onSubmit={bookParcel}>
              <div className="fact">
                <span>Parcel</span>
                <strong>
                  {bookingParcel.description ||
                    `Parcel ${bookingParcel.id.slice(0, 8).toUpperCase()}`}
                </strong>
              </div>
              <div className="form-grid form-grid-first">
                <label>
                  Locker size
                  <input
                    value={sizes[size] || size}
                    disabled
                    readOnly
                    aria-label="Locker size (set by your parcel)"
                  />
                </label>
                <label>
                  Location
                  <select
                    value={station}
                    onChange={(e) => setStation(e.target.value)}
                    required
                  >
                    <option value="">Select a location</option>
                    {stations.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name} · {item.zone}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {stationError && (
                <div className="error" role="alert">
                  Could not load/check locker availability: {stationError}
                </div>
              )}
              {station && (
                <div className="availability">
                  {checkingAvailability ? (
                    "Checking available lockers…"
                  ) : availableCount === null ? (
                    "Availability unavailable"
                  ) : availableCount > 0 ? (
                    <>
                      <Pill tone="good">Available</Pill>
                      <strong>
                        {availableCount} {size} locker
                        {availableCount === 1 ? "" : "s"} at{" "}
                        {stationLabel(station)}
                      </strong>
                    </>
                  ) : (
                    <>
                      <Pill tone="bad">Unavailable</Pill>
                      <strong>
                        No {size} lockers at {stationLabel(station)}
                      </strong>
                    </>
                  )}
                </div>
              )}
              <Notice>
                Availability refreshes automatically every 5 seconds. The
                backend checks again when a reservation is submitted to prevent
                double booking.
              </Notice>
              {error && (
                <div className="error" role="alert">
                  {error}
                </div>
              )}
              <button
                className="primary submit"
                disabled={
                  !station ||
                  availableCount === null ||
                  availableCount === 0 ||
                  savingReservation
                }
              >
                {savingReservation
                  ? "Submitting…"
                  : "Confirm parcel reservation"}
              </button>
            </form>
          </>
        )}

        {screen === "collect" && (
          <>
            <button className="back" onClick={() => goTo("parcel")}>
              ← Parcel Services
            </button>
            <div className="overline">COLLECT PARCEL</div>
            <h1>Parcels ready for collection</h1>
            <p className="subtitle">
              Only parcels assigned to your signed-in account are shown.
            </p>
            <div className="panel">
              {collectionMessage && (
                <div
                  className={collectionOk ? "ok" : "error"}
                  role={collectionOk ? "status" : "alert"}
                >
                  {collectionMessage}
                </div>
              )}
              {selectedParcel ? (
                <>
                  <h2>Unlock your parcel</h2>
                  <div className="fact">
                    <span>Station</span>
                    <strong>{selectedParcel.station_name}</strong>
                  </div>
                  <div className="fact">
                    <span>Compartment</span>
                    <strong>{selectedParcel.compartment_label}</strong>
                  </div>
                  <button
                    type="button"
                    className="primary submit"
                    onClick={unlockParcel}
                    disabled={unlockingParcel}
                  >
                    {unlockingParcel ? "Unlocking…" : "Unlock locker"}
                  </button>
                  <button
                    type="button"
                    className="secondary-action"
                    onClick={() => {
                      setSelectedParcel(null);
                      setCollectionMessage("");
                      setCollectionOk(false);
                    }}
                  >
                    Back to parcel list
                  </button>
                </>
              ) : (
                <>
                  <div className="booking-head">
                    <h2>Your parcels</h2>
                    <button
                      type="button"
                      onClick={loadParcels}
                      disabled={loadingParcels}
                    >
                      {loadingParcels ? "Refreshing…" : "Refresh"}
                    </button>
                  </div>
                  {loadingParcels ? (
                    <div className="empty">Loading parcels…</div>
                  ) : parcels.length === 0 ? (
                    <div className="empty">
                      There are currently no parcels ready for collection.
                    </div>
                  ) : (
                    <div className="booking-list">
                      {parcels.map((parcel) => (
                        <article
                          className="booking-card"
                          key={parcel.reservation_id}
                        >
                          <div className="booking-head">
                            <strong>{parcel.station_name}</strong>
                            <Pill tone="good">Ready</Pill>
                          </div>
                          <p>
                            Compartment {parcel.compartment_label} ·{" "}
                            {sizes[parcel.required_size] ||
                              parcel.required_size}
                          </p>
                          <button
                            type="button"
                            className="primary"
                            onClick={() => {
                              setSelectedParcel(parcel);
                              setCollectionMessage("");
                              setCollectionOk(false);
                            }}
                          >
                            Select parcel
                          </button>
                        </article>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </>
        )}

        {screen === "confirmed" && receipt && (
          <>
            <button className="back" onClick={() => goTo("parcel")}>
              ← Parcel Services
            </button>
            <div className="panel confirmed">
              <div className="confirm-check">✓</div>
              <div className="overline">BOOKING RESPONSE</div>
              <h1>
                {["ALLOCATED", "ARRIVED"].includes(receipt.status)
                  ? "Locker reserved"
                  : "Reservation submitted"}
              </h1>
              <p className="subtitle">
                The reservation status is retrieved from the backend.
              </p>
              <div className="fact">
                <span>Reservation</span>
                <strong>{receipt.id}</strong>
              </div>
              <div className="fact">
                <span>Status</span>
                <Pill
                  tone={
                    ["ALLOCATED", "ARRIVED"].includes(receipt.status)
                      ? "good"
                      : "neutral"
                  }
                >
                  {receipt.status}
                </Pill>
              </div>
              <div className="fact">
                <span>Size</span>
                <strong>{sizes[receipt.required_size]}</strong>
              </div>
              <div className="fact">
                <span>Location</span>
                <strong>{stationLabel(receipt.station_id)}</strong>
              </div>
              {receipt.status === "ARRIVED" ? (
                <>
                  <Notice>
                    Your locker is ready. Open Collect Parcel and tap Unlock
                    locker when you are at the station.
                  </Notice>
                  <button
                    className="primary submit"
                    onClick={() => goTo("collect")}
                  >
                    Go to Collect Parcel
                  </button>
                </>
              ) : (
                <Notice>
                  {receipt.status === "CONDITIONAL"
                    ? "Your request is queued. This page will update when a locker is allocated."
                    : `Reservation status: ${receipt.status}.`}
                </Notice>
              )}
            </div>
          </>
        )}
      </main>
      <footer>ParcelPoint · Distributed systems coursework prototype</footer>
    </div>
  );
}
