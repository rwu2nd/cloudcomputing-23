import AdminPanel from './AdminPanel.jsx';
import LuggagePortal from './LuggagePortal.jsx';
import { useCallback, useEffect, useState } from 'react';
import './index.css';

// Base URL for API calls. In Docker/Kubernetes this is "/api" and nginx forwards
// it to the backend. Override at build time with VITE_API_BASE_URL if needed.
const API = import.meta.env.VITE_API_BASE_URL ?? '/api';
const AUTH_TOKEN_KEY = 'parcelpoint-access-token';
const sizes = { S: 'Small', M: 'Medium', L: 'Large', XL: 'Extra large' };
async function request(path, opts = {}) {
  let response;
  try {
    const token = sessionStorage.getItem(AUTH_TOKEN_KEY);
    const headers = { ...(opts.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
    response = await fetch(`${API}${path}`, { ...opts, headers });
  } catch { throw Error('Unable to connect to the server.'); }
  const data = await response.json().catch(() => null);
  if (!response.ok) throw Error(typeof data?.detail === 'string' ? data.detail : 'Request failed. Please try again.');
  return data;
}
const isPending = s => !['CANCELLED', 'FULFILLED', 'UNALLOCATED'].includes(s);
function Pill({ children, tone = 'neutral' }) { return <span className={`pill ${tone}`}>{children}</span> }
function Arrow() { return <span aria-hidden="true">↗</span> }
function Notice({ children }) { return <div className="notice" role="status">{children}</div> }
function SectionChoice({ icon, title, sub, onClick }) { return <button className="choice" onClick={onClick}><span className="choice-icon">{icon}</span><strong>{title}</strong><small>{sub}</small><span className="choice-arrow"><Arrow /></span></button> }

export default function App() {
  const [account, setAccount] = useState(null);
  const [accessToken, setAccessToken] = useState(() => sessionStorage.getItem(AUTH_TOKEN_KEY) || '');
  const [authMode, setAuthMode] = useState('login');
  const [authUsername, setAuthUsername] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authError, setAuthError] = useState('');
  const [authBusy, setAuthBusy] = useState(false);
  const [screen, setScreen] = useState('choose');
  const [kind, setKind] = useState('collect');
  const [customer, setCustomer] = useState('');
  const [lookup, setLookup] = useState('');
  const [records, setRecords] = useState([]);
  const [recordsError, setRecordsError] = useState('');
  const [stations, setStations] = useState([]);
  const [stationError, setStationError] = useState('');
  const [size, setSize] = useState('M');
  const [station, setStation] = useState('');
  const [count, setCount] = useState(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [receipt, setReceipt] = useState(null);
  const [testMode, setTestMode] = useState('load');
  // Parcel collection (receive parcel / unlock locker) state
  const [parcels, setParcels] = useState([]);
  const [selectedParcel, setSelectedParcel] = useState(null);
  const [password, setPassword] = useState('');
  const [collectionMessage, setCollectionMessage] = useState('');
  const [collectionOk, setCollectionOk] = useState(false);
  const [loadingParcels, setLoadingParcels] = useState(false);
  const [unlocking, setUnlocking] = useState(false);

  const loadStations = useCallback(async () => {
    try { setStationError(''); const s = await request('/stations'); setStations((Array.isArray(s) ? s : []).filter(x => x.active !== false).slice(0, 3).map((x, i) => ({ ...x, name: `Location ${'ABC'[i]}` }))); }
    catch (e) { setStationError(e.message) }
  }, []);
  useEffect(() => { loadStations() }, [loadStations]);
  useEffect(() => {
if (!station || !size) { setCount(null); return; }
    let active = true; setChecking(true); setCount(null);
    const refresh = async () => { try { const result = await request(`/stations/${encodeURIComponent(station)}/compartments`); if (active) { setCount(result.filter(x => x.active && !x.reserved && x.size === size).length); setStationError('') } } catch (e) { if (active) { setCount(null); setStationError(e.message) } } finally { if (active) setChecking(false) } };
    refresh(); const interval = setInterval(refresh, 10000); return () => { active = false; clearInterval(interval) };
  }, [station, size]);
  async function loadRecords(id = lookup) {
    if (!id.trim()) { setRecords([]); return }
    setRecordsError('');
    try { const all = await request('/reservations'); setRecords((Array.isArray(all) ? all : []).filter(r => r.customer_id === id.trim())); }
    catch (e) { setRecordsError(e.message) }
  }
  // Retrieve parcels that are ready for collection
  async function loadParcels() {
    setLoadingParcels(true); setCollectionMessage(''); setCollectionOk(false);
    try {
      const data = await request('/parcels/collection');
      setParcels(Array.isArray(data) ? data : []);
      setSelectedParcel(null); setPassword('');
    } catch (e) { setCollectionMessage(e.message) }
    finally { setLoadingParcels(false) }
  }
  // Verify the collection password and unlock the locker
  async function unlockLocker() {
    if (!selectedParcel || !password.trim()) { setCollectionOk(false); setCollectionMessage('Please enter your collection password.'); return }
    setUnlocking(true);
    try {
      await request(`/reservations/${selectedParcel.reservation_id}/unlock`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }) });
      setParcels(prev => prev.filter(p => p.reservation_id !== selectedParcel.reservation_id));
      setSelectedParcel(null); setPassword('');
      setCollectionOk(true); setCollectionMessage('Locker unlocked successfully!');
    } catch (e) { setCollectionOk(false); setCollectionMessage(e.message) }
    finally { setUnlocking(false) }
  }
  function move(next) { setError(''); setScreen(next); if (next === 'collect' || next === 'return') { setKind(next); loadRecords(); } if (next === 'new') { setCount(null); setSize('M'); setStation(''); } if (next === 'receive') { setSelectedParcel(null); setPassword(''); setCollectionMessage(''); setCollectionOk(false); loadParcels(); } }
  useEffect(() => {
    if (screen !== 'confirmed' || !receipt?.id || receipt.collection_password) return;
    let cancelled = false;
    let timer;
    const checkReservation = async () => {
      try {
        const latest = await request(`/reservations/${receipt.id}`);
        if (cancelled) return;
        setReceipt(current => current?.id === latest.id ? { ...current, ...latest } : current);
        if (latest.status === 'ALLOCATED') {
          const arrived = await request(`/reservations/${receipt.id}/arrive`, { method: 'POST' });
          if (!cancelled) setReceipt(current => current?.id === latest.id ? { ...current, ...latest, ...arrived } : current);
        } else if (latest.status === 'CONDITIONAL') {
          timer = setTimeout(checkReservation, 1200);
        }
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
    };
    if (receipt.status === 'CONDITIONAL' || receipt.status === 'ALLOCATED') checkReservation();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [screen, receipt?.id, receipt?.collection_password]);
  async function book(e) {
    e.preventDefault(); setError('');
    if (!customer.trim() || !station) return setError('Enter your customer ID and select a location.');
    if (kind !== 'collect') return setError('This parcel workflow currently supports collection reservations only.');
    if (!count) return setError('No suitable locker is currently available. Refresh or choose another location.');
    setSaving(true);
    try {
      const data = await request('/reservations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ customer_id: customer.trim(), station_id: Number(station), required_size: size }),
      });
      setReceipt(data); move('confirmed');
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }
  useEffect(() => {
    if (!accessToken) return;
    let active = true;
    fetch(`${API}/luggage/auth/me`, { headers: { Authorization: `Bearer ${accessToken}` } })
      .then(async response => {
        const data = await response.json().catch(() => null);
        if (!response.ok) throw new Error(data?.detail || 'Your session has expired. Please sign in again.');
        if (active) setAccount(data);
      })
      .catch(() => {
        sessionStorage.removeItem(AUTH_TOKEN_KEY);
        if (active) { setAccessToken(''); setAccount(null); }
      });
    return () => { active = false; };
  }, [accessToken]);

  async function authenticate(event) {
    event.preventDefault(); setAuthBusy(true); setAuthError('');
    try {
      const endpoint = authMode === 'register' ? '/luggage/auth/register' : '/luggage/auth/login';
      const response = await fetch(`${API}${endpoint}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: authUsername, password: authPassword }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Unable to sign in.');
      if (data.user.role === 'ADMIN' && authMode === 'register') throw new Error('Administrator accounts cannot be self-registered.');
      sessionStorage.setItem(AUTH_TOKEN_KEY, data.access_token);
      setAccessToken(data.access_token); setAccount(data.user); setAuthPassword(''); setScreen('choose');
      setLookup(data.user.username); setCustomer(data.user.username);
    } catch (e) { setAuthError(e.message); }
    finally { setAuthBusy(false); }
  }

  async function signOut() {
    try { await fetch(`${API}/luggage/auth/logout`, { method: 'POST', headers: { Authorization: `Bearer ${accessToken}` } }); } catch { /* clear local session regardless */ }
    sessionStorage.removeItem(AUTH_TOKEN_KEY); setAccessToken(''); setAccount(null);
    setScreen('choose'); setRecords([]); setParcels([]);
  }

  const stationLabel = id => stations.find(s => String(s.id) === String(id))?.name || `Location ${id}`;
  const pendingRecords = records.filter(r => isPending(r.status));
  if (!account) return <div className="app auth-app">
    <header className="top"><div className="top-inner"><div className="brand"><span className="brand-symbol">▦</span><span>parcel<span className="brand-blue">point</span><small>SMART LOCKER SYSTEM</small></span></div></div></header>
    <main className="main auth-main"><div className="overline">WELCOME TO PARCELPOINT</div><h1>Sign in to continue</h1><p className="subtitle">Access parcel and luggage services, or sign in to the administrator dashboard.</p>
      <form className="panel auth-panel" onSubmit={authenticate}><div className="auth-toggle"><button type="button" className={authMode==='login'?'selected':''} onClick={()=>{setAuthMode('login');setAuthError('')}}>Login</button><button type="button" className={authMode==='register'?'selected':''} onClick={()=>{setAuthMode('register');setAuthError('')}}>Create user account</button></div>
        <label>Username<input required minLength={3} maxLength={32} value={authUsername} onChange={e=>setAuthUsername(e.target.value)} autoComplete="username" /></label>
        <label>Password<input required minLength={authMode==='register'?10:1} maxLength={128} type="password" value={authPassword} onChange={e=>setAuthPassword(e.target.value)} autoComplete={authMode==='register'?'new-password':'current-password'} /></label>
        {authMode==='register'&&<small>Password must be at least 10 characters.</small>}
        {authError&&<div className="error" role="alert">{authError}</div>}
        <button className="primary submit" disabled={authBusy}>{authBusy?'Please wait…':authMode==='login'?'Login':'Create account'}</button>
      </form>
    </main><footer>ParcelPoint · Distributed systems coursework prototype</footer></div>;
  if (account.role === 'ADMIN') return <AdminPanel accessToken={accessToken} account={account} onLogout={signOut} />;

  return <div className="app">
    <header className="top"><div className="top-inner"><div className="brand"><span className="brand-symbol">▦</span><span>parcel<span className="brand-blue">point</span><small>SMART LOCKER SYSTEM</small></span></div><div className="user-session"><span>Signed in as <strong>{account.username}</strong></span><button onClick={signOut}>Sign out</button></div></div></header>
    <main className="main">
      {screen === 'choose' && <><div className="overline">BOOK A LOCKER</div><h1>What would you like to store?</h1><p className="subtitle">Choose a service to get started.</p><div className="choice-grid"><SectionChoice icon="▣" title="Parcel" sub="Collect deliveries or return items" onClick={() => move('parcel')} /><SectionChoice icon="▤" title="Luggage" sub="Store your bags in a locker" onClick={() => { setKind('luggage'); setScreen('luggage'); setError(''); }} /></div></>}
      {screen === 'parcel' && <><button className="back" onClick={() => move('choose')}>← Back</button><div className="overline">PARCEL SERVICES</div><h1>What would you like to do?</h1><p className="subtitle">Manage your delivery or return.</p><div className="choice-grid"><SectionChoice icon="▣" title="Choose locker for incoming parcel" sub="Reserve a compartment at your selected station" onClick={() => { setKind('collect'); setCustomer(account.username); move('new'); }} /><SectionChoice icon="↓" title="Collect parcel" sub="View parcels ready for collection and unlock a locker" onClick={() => { setLookup(account.username); setKind('collect'); setScreen('collect'); setError(''); loadRecords(account.username); }} /></div></>}
      {screen === 'luggage' && <LuggagePortal accessToken={accessToken} account={account} onBack={() => move('choose')} />}
      {(screen === 'collect' || screen === 'return') && <><button className="back" onClick={() => move('parcel')}>← Parcel services</button><div className="overline">{screen === 'collect' ? 'COLLECT PARCEL' : 'RETURN PARCEL'}</div><h1>{screen === 'collect' ? 'Pending collections' : 'Pending returns'}</h1><p className="subtitle">Enter your customer ID to check your requests.</p><div className="panel"><div className="lookup"><label>Customer ID<input value={lookup} onChange={e => setLookup(e.target.value)} placeholder="Customer ID" /></label><button className="primary" onClick={() => loadRecords()}>Find items</button></div>{recordsError && <div className="error">{recordsError}</div>}
        <Notice>The current backend has reservations, but does not identify whether each reservation is a collection or return. Items are not shown as pending collections/returns until your team adds a service type and return due dates.</Notice>
        {pendingRecords.length > 0 && <p className="muted">{pendingRecords.length} existing reservation(s) found, but they cannot yet be classified by service.</p>}
        <div className="empty">No verified pending {screen === 'collect' ? 'collections' : 'returns'} to display.</div>
      </div>
        {screen === 'collect' && <button className="secondary-action wide-action" onClick={() => move('receive')}>Receive a parcel (unlock locker)</button>}
        <button className="primary wide-action" onClick={() => { setKind(screen === 'collect' ? 'collect' : 'return'); setCustomer(lookup); move('new') }}>{screen === 'collect' ? '＋ Book a new parcel delivery' : '＋ New parcel return request'}</button></>}
      {screen === 'receive' && <><button className="back" onClick={() => move('collect')}>← Collect parcel</button><div className="overline">RECEIVE PARCEL</div><h1>Receive parcel</h1><p className="subtitle">Select a parcel that is ready for collection, then enter your collection password to unlock its locker.</p><div className="panel">
        {collectionMessage && <div className={collectionOk ? 'ok' : 'error'} role={collectionOk ? 'status' : 'alert'}>{collectionMessage}</div>}
        {selectedParcel ? <>
          <h2>Enter collection password</h2>
          <div className="fact"><span>Station</span><strong>{selectedParcel.station_name}</strong></div>
          <div className="fact"><span>Compartment</span><strong>{selectedParcel.compartment_label}</strong></div>
          <label>Collection password<input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Enter your password" autoComplete="off" /></label>
          <button type="button" className="primary submit" onClick={unlockLocker} disabled={unlocking}>{unlocking ? 'Unlocking…' : 'Unlock locker'}</button>
          <button type="button" className="secondary-action" onClick={() => { setSelectedParcel(null); setPassword(''); setCollectionMessage(''); setCollectionOk(false) }}>Back to parcel list</button>
        </> : <>
          <h2>Available parcels</h2>
          {loadingParcels ? <div className="empty">Loading parcels…</div> : parcels.length === 0 ? <div className="empty">There are currently no parcels ready for collection.</div> : <div className="booking-list">{parcels.map(parcel => <article className="booking-card" key={parcel.reservation_id}><div className="booking-head"><strong>{parcel.station_name}</strong><Pill tone="good">Ready</Pill></div><p>Compartment {parcel.compartment_label} · {sizes[parcel.required_size] || parcel.required_size} ({parcel.required_size})</p><button type="button" className="primary" onClick={() => { setSelectedParcel(parcel); setCollectionMessage(''); setCollectionOk(false) }}>Select parcel</button></article>)}</div>}
          <button type="button" className="secondary-action" onClick={loadParcels} disabled={loadingParcels}>Refresh list</button>
        </>}
      </div></>}
      {screen === 'new' && <><button className="back" onClick={() => move(kind === 'luggage' ? 'luggage' : kind)}>← Back</button><div className="overline">{kind === 'luggage' ? 'LUGGAGE STORAGE' : kind === 'return' ? 'NEW RETURN REQUEST' : 'NEW PARCEL DELIVERY'}</div><h1>Choose your locker</h1><p className="subtitle">Select a size and location to see live locker availability. No backup locations.</p><form className="panel" onSubmit={book}><label>Customer ID<input required value={customer} onChange={e => setCustomer(e.target.value)} placeholder="Enter customer ID" maxLength={120} /></label><div className="form-grid"><label>Locker size<select value={size} onChange={e => setSize(e.target.value)}>{Object.entries(sizes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>Location<select value={station} onChange={e => setStation(e.target.value)} required><option value="">Select a location</option>{stations.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label></div>
        {stationError && <div className="error">Could not check availability: {stationError}</div>}
        {station && <div className="availability">{checking ? 'Checking available lockers…' : count === null ? 'Availability unavailable' : count > 0 ? <><Pill tone="good">Available</Pill><strong>{count} {size} locker{count === 1 ? '' : 's'} at {stationLabel(station)}</strong></> : <><Pill tone="bad">Unavailable</Pill><strong>No {size} lockers at {stationLabel(station)}</strong></>}</div>}
        <Notice>Availability is a snapshot, refreshed every 10 seconds. The server rechecks availability during booking to prevent double bookings.</Notice>
        {kind !== 'collect' && <Notice>Parcel returns are not yet connected to a return-specific backend workflow.</Notice>}
        {error && <div className="error" role="alert">{error}</div>}
        <button className="primary submit" disabled={kind !== 'collect' || !station || count === null || count === 0 || saving}>{saving ? 'Submitting…' : kind === 'collect' ? 'Confirm parcel reservation' : 'Return booking unavailable'}</button>
      </form></>}
      {screen === 'confirmed' && receipt && <><button className="back" onClick={() => move('choose')}>← Start again</button><div className="panel confirmed"><div className="confirm-check">✓</div><div className="overline">BOOKING RESPONSE</div><h1>{['ALLOCATED', 'ARRIVED'].includes(receipt.status) ? 'Locker reserved' : 'Reservation submitted'}</h1><p className="subtitle">The status below comes from the backend.</p><div className="fact"><span>Reservation</span><strong>{receipt.id}</strong></div><div className="fact"><span>Status</span><Pill tone={['ALLOCATED', 'ARRIVED'].includes(receipt.status) ? 'good' : 'neutral'}>{receipt.status}</Pill></div><div className="fact"><span>Size</span><strong>{sizes[receipt.required_size]}</strong></div><div className="fact"><span>Location</span><strong>{stationLabel(receipt.station_id)}</strong></div>{receipt.collection_password && <div className="fact"><span>Collection password</span><strong>{receipt.collection_password}</strong></div>}{receipt.status === 'ARRIVED' && receipt.collection_password && <Notice>Keep this password private. Enter it on the Receive Parcel screen to unlock your compartment.</Notice>}{receipt.status !== 'ARRIVED' && <Notice>{receipt.status === 'CONDITIONAL' ? 'Your request is queued. This page will update when a locker is allocated.' : `Reservation status: ${receipt.status}.`}</Notice>}</div></>}
    </main>
    <footer>ParcelPoint · Distributed systems coursework prototype</footer>
  </div>;
}
