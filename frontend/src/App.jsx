import AdminPanel from './AdminPanel.jsx';
import LuggagePortal from './LuggagePortal.jsx';
import { useCallback, useEffect, useState } from 'react';
import './index.css';

// In Docker/Kubernetes nginx forwards /api/* to FastAPI.
const API = import.meta.env.VITE_API_BASE_URL ?? '/api';
const AUTH_TOKEN_KEY = 'parcelpoint-access-token';
const sizes = { S: 'Small', M: 'Medium', L: 'Large', XL: 'Extra large' };

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
    throw new Error('Unable to connect to the server.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'Request failed. Please try again.');
  }
  return data;
}

const isTerminal = status => ['CANCELLED', 'FULFILLED', 'UNALLOCATED'].includes(status);

function Pill({ children, tone = 'neutral' }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

function Notice({ children }) {
  return <div className="notice" role="status">{children}</div>;
}

function SectionChoice({ icon, title, sub, onClick }) {
  return (
    <button className="choice" onClick={onClick}>
      <span className="choice-icon">{icon}</span>
      <strong>{title}</strong>
      <small>{sub}</small>
      <span className="choice-arrow" aria-hidden="true">↗</span>
    </button>
  );
}

export default function App() {
  const [account, setAccount] = useState(null);
  const [accessToken, setAccessToken] = useState(() => sessionStorage.getItem(AUTH_TOKEN_KEY) || '');
  const [authMode, setAuthMode] = useState('login');
  const [authUsername, setAuthUsername] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authError, setAuthError] = useState('');
  const [authBusy, setAuthBusy] = useState(false);

  const [screen, setScreen] = useState('choose');
  const [stations, setStations] = useState([]);
  const [stationError, setStationError] = useState('');
  const [station, setStation] = useState('');
  const [size, setSize] = useState('M');
  const [availableCount, setAvailableCount] = useState(null);
  const [checkingAvailability, setCheckingAvailability] = useState(false);
  const [savingReservation, setSavingReservation] = useState(false);
  const [error, setError] = useState('');
  const [receipt, setReceipt] = useState(null);

  const [parcels, setParcels] = useState([]);
  const [selectedParcel, setSelectedParcel] = useState(null);
  const [collectionPassword, setCollectionPassword] = useState('');
  const [collectionMessage, setCollectionMessage] = useState('');
  const [collectionOk, setCollectionOk] = useState(false);
  const [loadingParcels, setLoadingParcels] = useState(false);
  const [unlockingParcel, setUnlockingParcel] = useState(false);

  const loadStations = useCallback(async () => {
    try {
      setStationError('');
      const result = await request('/stations');
      setStations((Array.isArray(result) ? result : []).filter(item => item.active !== false));
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
        const compartments = await request(`/stations/${encodeURIComponent(station)}/compartments`);
        if (active) {
          const count = (Array.isArray(compartments) ? compartments : []).filter(
            item => item.active && !item.reserved && item.size === size,
          ).length;
          setAvailableCount(count);
          setStationError('');
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
    setCollectionMessage('');
    setCollectionOk(false);
    try {
      // This endpoint is already scoped to the authenticated account.
      const result = await request('/parcels/collection');
      setParcels(Array.isArray(result) ? result : []);
      setSelectedParcel(null);
      setCollectionPassword('');
    } catch (e) {
      setCollectionMessage(e.message);
      setCollectionOk(false);
    } finally {
      setLoadingParcels(false);
    }
  }, []);

  function goTo(next) {
    setError('');
    setScreen(next);
    if (next === 'new') {
      setStation('');
      setAvailableCount(null);
      setSize('M');
    }
    if (next === 'collect') {
      setSelectedParcel(null);
      setCollectionPassword('');
      loadParcels();
    }
  }

  async function unlockParcel() {
    if (!selectedParcel || !collectionPassword.trim()) {
      setCollectionOk(false);
      setCollectionMessage('Enter the collection password to unlock this locker.');
      return;
    }
    setUnlockingParcel(true);
    setCollectionMessage('');
    try {
      await request(`/reservations/${selectedParcel.reservation_id}/unlock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: collectionPassword }),
      });
      setCollectionOk(true);
      setCollectionMessage('Locker unlocked successfully. Your parcel has been marked as collected.');
      setSelectedParcel(null);
      setCollectionPassword('');
      const updated = await request('/parcels/collection');
      setParcels(Array.isArray(updated) ? updated : []);
    } catch (e) {
      setCollectionOk(false);
      setCollectionMessage(e.message);
    } finally {
      setUnlockingParcel(false);
    }
  }

  // Once a reservation is allocated, this demo transitions it to ARRIVED and
  // displays the one-time collection password returned by the backend.
  useEffect(() => {
    if (screen !== 'confirmed' || !receipt?.id || receipt.collection_password || isTerminal(receipt.status)) {
      return undefined;
    }

    let cancelled = false;
    let timer;
    const checkReservation = async () => {
      try {
        const latest = await request(`/reservations/${receipt.id}`);
        if (cancelled) return;
        setReceipt(current => current?.id === latest.id ? { ...current, ...latest } : current);

        if (latest.status === 'ALLOCATED') {
          const arrived = await request(`/reservations/${receipt.id}/arrive`, { method: 'POST' });
          if (!cancelled) {
            setReceipt(current => current?.id === latest.id ? { ...current, ...latest, ...arrived } : current);
          }
        } else if (latest.status === 'CONDITIONAL') {
          timer = setTimeout(checkReservation, 1200);
        }
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
    };

    if (receipt.status === 'CONDITIONAL' || receipt.status === 'ALLOCATED') {
      checkReservation();
    }

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [screen, receipt?.id, receipt?.collection_password]);

  async function bookParcel(event) {
    event.preventDefault();
    setError('');
    if (!station) return setError('Select a locker location.');
    if (availableCount === null || availableCount === 0) {
      return setError('No suitable locker is currently available. Refresh or choose another location.');
    }

    setSavingReservation(true);
    try {
      // The backend derives customer_id from the bearer token. The browser
      // does not ask the customer to type or submit an ID.
      const result = await request('/reservations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ station_id: Number(station), required_size: size }),
      });
      setReceipt(result);
      setScreen('confirmed');
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
      .then(async response => {
        const data = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(data?.detail || 'Your session has expired. Please sign in again.');
        }
        if (active) setAccount(data);
      })
      .catch(() => {
        sessionStorage.removeItem(AUTH_TOKEN_KEY);
        if (active) {
          setAccessToken('');
          setAccount(null);
        }
      });
    return () => { active = false; };
  }, [accessToken]);

  async function authenticate(event) {
    event.preventDefault();
    setAuthBusy(true);
    setAuthError('');
    try {
      const endpoint = authMode === 'register' ? '/luggage/auth/register' : '/luggage/auth/login';
      const response = await fetch(`${API}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: authUsername, password: authPassword }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(typeof data?.detail === 'string' ? data.detail : 'Unable to sign in.');
      }
      if (!data?.access_token || !data?.user) {
        throw new Error('The server did not return a login token. Check the luggage authentication endpoints.');
      }
      if (data.user.role === 'ADMIN' && authMode === 'register') {
        throw new Error('Administrator accounts cannot be self-registered.');
      }
      sessionStorage.setItem(AUTH_TOKEN_KEY, data.access_token);
      setAccessToken(data.access_token);
      setAccount(data.user);
      setAuthPassword('');
      setScreen('choose');
    } catch (e) {
      setAuthError(e.message);
    } finally {
      setAuthBusy(false);
    }
  }

  async function signOut() {
    try {
      await fetch(`${API}/luggage/auth/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${accessToken}` },
      });
    } catch {
      // Clear the browser session even when the API cannot be reached.
    }
    sessionStorage.removeItem(AUTH_TOKEN_KEY);
    setAccessToken('');
    setAccount(null);
    setScreen('choose');
    setReceipt(null);
    setParcels([]);
  }

  const stationLabel = id => stations.find(item => String(item.id) === String(id))?.name || `Station ${id}`;

  if (!account) {
    return (
      <div className="app auth-app">
        <header className="top">
          <div className="top-inner">
            <div className="brand"><span className="brand-symbol">▦</span><span>parcel<span className="brand-blue">point</span><small>SMART LOCKER SYSTEM</small></span></div>
          </div>
        </header>
        <main className="main auth-main">
          <div className="overline">WELCOME TO PARCELPOINT</div>
          <h1>Sign in to continue</h1>
          <p className="subtitle">Access parcel and luggage services, or sign in to the administrator dashboard.</p>
          <form className="panel auth-panel" onSubmit={authenticate}>
            <div className="auth-toggle">
              <button type="button" className={authMode === 'login' ? 'selected' : ''} onClick={() => { setAuthMode('login'); setAuthError(''); }}>Login</button>
              <button type="button" className={authMode === 'register' ? 'selected' : ''} onClick={() => { setAuthMode('register'); setAuthError(''); }}>Create user account</button>
            </div>
            <label>Username<input required minLength={3} maxLength={32} value={authUsername} onChange={e => setAuthUsername(e.target.value)} autoComplete="username" /></label>
            <label>Password<input required minLength={authMode === 'register' ? 10 : 1} maxLength={128} type="password" value={authPassword} onChange={e => setAuthPassword(e.target.value)} autoComplete={authMode === 'register' ? 'new-password' : 'current-password'} /></label>
            {authMode === 'register' && <small>Password must be at least 10 characters.</small>}
            {authError && <div className="error" role="alert">{authError}</div>}
            <button className="primary submit" disabled={authBusy}>{authBusy ? 'Please wait…' : authMode === 'login' ? 'Login' : 'Create account'}</button>
          </form>
        </main>
        <footer>ParcelPoint · Distributed systems coursework prototype</footer>
      </div>
    );
  }

  if (account.role === 'ADMIN') {
    return <AdminPanel accessToken={accessToken} account={account} onLogout={signOut} />;
  }

  return (
    <div className="app">
      <header className="top">
        <div className="top-inner">
          <div className="brand"><span className="brand-symbol">▦</span><span>parcel<span className="brand-blue">point</span><small>SMART LOCKER SYSTEM</small></span></div>
          <div className="user-session"><span>Signed in as <strong>{account.username}</strong></span><button onClick={signOut}>Sign out</button></div>
        </div>
      </header>

      <main className="main">
        {screen === 'choose' && <>
          <div className="overline">BOOK A LOCKER</div>
          <h1>Parcel or luggage?</h1>
          <p className="subtitle">Choose a service to get started.</p>
          <div className="choice-grid">
            <SectionChoice icon="▣" title="Parcel" sub="Reserve a locker or collect an incoming parcel." onClick={() => goTo('parcel')} />
            <SectionChoice icon="▤" title="Luggage" sub="Store your bags in an extra-large locker." onClick={() => goTo('luggage')} />
          </div>
        </>}

        {screen === 'parcel' && <>
          <button className="back" onClick={() => goTo('choose')}>← Back</button>
          <div className="overline">PARCEL SERVICES</div>
          <h1>What would you like to do?</h1>
          <p className="subtitle">Manage your incoming parcel.</p>
          <div className="choice-grid">
            <SectionChoice icon="▣" title="Choose locker for parcel" sub="Reserve a compartment at your selected station." onClick={() => goTo('new')} />
            <SectionChoice icon="↓" title="Collect parcel" sub="View parcels ready for collection and unlock your locker." onClick={() => goTo('collect')} />
          </div>
        </>}

        {screen === 'luggage' && <LuggagePortal accessToken={accessToken} account={account} onBack={() => goTo('choose')} />}

        {screen === 'new' && <>
          <button className="back" onClick={() => goTo('parcel')}>← Parcel Services</button>
          <div className="overline">PARCEL SERVICES</div>
          <h1>Choose your locker</h1>
          <p className="subtitle">Select a size and location to see live availability from the database.</p>
          <form className="panel" onSubmit={bookParcel}>
            <div className="form-grid form-grid-first">
              <label>Locker size
                <select value={size} onChange={e => setSize(e.target.value)}>
                  {Object.entries(sizes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                </select>
              </label>
              <label>Location
                <select value={station} onChange={e => setStation(e.target.value)} required>
                  <option value="">Select a location</option>
                  {stations.map(item => <option key={item.id} value={item.id}>{item.name} · {item.zone}</option>)}
                </select>
              </label>
            </div>
            {stationError && <div className="error" role="alert">Could not load/check locker availability: {stationError}</div>}
            {station && <div className="availability">
              {checkingAvailability ? 'Checking available lockers…' : availableCount === null ? 'Availability unavailable' : availableCount > 0 ? <><Pill tone="good">Available</Pill><strong>{availableCount} {size} locker{availableCount === 1 ? '' : 's'} at {stationLabel(station)}</strong></> : <><Pill tone="bad">Unavailable</Pill><strong>No {size} lockers at {stationLabel(station)}</strong></>}
            </div>}
            <Notice>Availability refreshes automatically every 5 seconds. The backend checks again when a reservation is submitted to prevent double booking.</Notice>
            {error && <div className="error" role="alert">{error}</div>}
            <button className="primary submit" disabled={!station || availableCount === null || availableCount === 0 || savingReservation}>
              {savingReservation ? 'Submitting…' : 'Confirm parcel reservation'}
            </button>
          </form>
        </>}

        {screen === 'collect' && <>
          <button className="back" onClick={() => goTo('parcel')}>← Parcel Services</button>
          <div className="overline">COLLECT PARCEL</div>
          <h1>Parcels ready for collection</h1>
          <p className="subtitle">Only parcels assigned to your signed-in account are shown.</p>
          <div className="panel">
            {collectionMessage && <div className={collectionOk ? 'ok' : 'error'} role={collectionOk ? 'status' : 'alert'}>{collectionMessage}</div>}
            {selectedParcel ? <>
              <h2>Unlock your parcel</h2>
              <div className="fact"><span>Station</span><strong>{selectedParcel.station_name}</strong></div>
              <div className="fact"><span>Compartment</span><strong>{selectedParcel.compartment_label}</strong></div>
              <label>Collection password
                <input type="password" value={collectionPassword} onChange={e => setCollectionPassword(e.target.value)} placeholder="Enter collection password" autoComplete="off" />
              </label>
              <button type="button" className="primary submit" onClick={unlockParcel} disabled={unlockingParcel}>{unlockingParcel ? 'Unlocking…' : 'Unlock locker'}</button>
              <button type="button" className="secondary-action" onClick={() => { setSelectedParcel(null); setCollectionPassword(''); setCollectionMessage(''); setCollectionOk(false); }}>Back to parcel list</button>
            </> : <>
              <div className="booking-head"><h2>Your parcels</h2><button type="button" onClick={loadParcels} disabled={loadingParcels}>{loadingParcels ? 'Refreshing…' : 'Refresh'}</button></div>
              {loadingParcels ? <div className="empty">Loading parcels…</div> : parcels.length === 0 ? <div className="empty">There are currently no parcels ready for collection.</div> : <div className="booking-list">
                {parcels.map(parcel => <article className="booking-card" key={parcel.reservation_id}>
                  <div className="booking-head"><strong>{parcel.station_name}</strong><Pill tone="good">Ready</Pill></div>
                  <p>Compartment {parcel.compartment_label} · {sizes[parcel.required_size] || parcel.required_size}</p>
                  <button type="button" className="primary" onClick={() => { setSelectedParcel(parcel); setCollectionMessage(''); setCollectionOk(false); }}>Select parcel</button>
                </article>)}
              </div>}
            </>}
          </div>
        </>}

        {screen === 'confirmed' && receipt && <>
          <button className="back" onClick={() => goTo('parcel')}>← Parcel Services</button>
          <div className="panel confirmed">
            <div className="confirm-check">✓</div>
            <div className="overline">BOOKING RESPONSE</div>
            <h1>{['ALLOCATED', 'ARRIVED'].includes(receipt.status) ? 'Locker reserved' : 'Reservation submitted'}</h1>
            <p className="subtitle">The reservation status is retrieved from the backend.</p>
            <div className="fact"><span>Reservation</span><strong>{receipt.id}</strong></div>
            <div className="fact"><span>Status</span><Pill tone={['ALLOCATED', 'ARRIVED'].includes(receipt.status) ? 'good' : 'neutral'}>{receipt.status}</Pill></div>
            <div className="fact"><span>Size</span><strong>{sizes[receipt.required_size]}</strong></div>
            <div className="fact"><span>Location</span><strong>{stationLabel(receipt.station_id)}</strong></div>
            {receipt.collection_password && <div className="fact"><span>Collection password</span><strong>{receipt.collection_password}</strong></div>}
            {receipt.status === 'ARRIVED' && receipt.collection_password ? <>
              <Notice>Keep this password private. Open Collect Parcel and enter it when collecting your parcel.</Notice>
              <button className="primary submit" onClick={() => goTo('collect')}>Go to Collect Parcel</button>
            </> : <Notice>{receipt.status === 'CONDITIONAL' ? 'Your request is queued. This page will update when a locker is allocated.' : `Reservation status: ${receipt.status}.`}</Notice>}
          </div>
        </>}
      </main>
      <footer>ParcelPoint · Distributed systems coursework prototype</footer>
    </div>
  );
}
