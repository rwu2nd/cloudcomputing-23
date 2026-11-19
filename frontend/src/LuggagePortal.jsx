import { useCallback, useEffect, useState } from 'react';

const API = import.meta.env.VITE_API_BASE_URL ?? '/api';
const MAX_REMINDER_MINUTES = 7 * 24 * 60; // 7 days

async function apiRequest(path, token, options = {}) {
  let response;
  try {
    response = await fetch(`${API}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new Error('Unable to connect to the server.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof data?.detail === 'string'
      ? data.detail
      : Array.isArray(data?.detail) && data.detail[0]?.msg
        ? `${(data.detail[0].loc || []).slice(1).join('.')}: ${data.detail[0].msg}`
        : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return data;
}

const luggageRequest = (path, token, options) => apiRequest(`/luggage${path}`, token, options);
const notificationRequest = (path, token, options) => apiRequest(`/notifications${path}`, token, options);

const money = cents => `SGD ${(Number(cents || 0) / 100).toFixed(2)}`;

// `notifications` (unread list) and `markRead` come from useNotifications() in App.jsx,
// so the bell badge and this page share one stream and stay in sync.
export default function LuggagePortal({
  onBack,
  accessToken: token,
  account: user,
  notifications = [],
  markRead = () => {},
}) {
  const [view, setView] = useState('choose');
  const [slots, setSlots] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [selectedSlot, setSelectedSlot] = useState('');
  const [checkout, setCheckout] = useState({});
  const [unlocked, setUnlocked] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  // Reminder fields on the "Choose a locker" form (blank amount = no reminder).
  const [storeReminder, setStoreReminder] = useState({ amount: '', unit: 'minutes' });
  // Pending reminders held by the backend: { [sessionId]: dueAtIso }
  const [reminders, setReminders] = useState({});

  const refresh = useCallback(async currentToken => {
    const [available, mine] = await Promise.all([
      luggageRequest('/slots', currentToken),
      luggageRequest('/sessions', currentToken),
    ]);
    setSlots(Array.isArray(available) ? available : []);
    setSessions(Array.isArray(mine) ? mine : []);
  }, []);

  const refreshReminders = useCallback(async currentToken => {
    try {
      const data = await notificationRequest('/reminders', currentToken);
      const map = {};
      (data?.reminders || []).forEach(r => { map[r.session_id] = r.due_at; });
      setReminders(map);
    } catch { /* non-critical */ }
  }, []);

  useEffect(() => {
    if (token) refresh(token).catch(e => setError(e.message));
  }, [token, refresh]);

  // Load pending reminders, and reload whenever a notification arrives
  // (a reminder that just fired is no longer pending).
  useEffect(() => {
    if (token) refreshReminders(token);
  }, [token, notifications.length, refreshReminders]);

  // Returns an error string, or null if the amount/unit are valid.
  function validateReminder(amountStr, unit) {
    const value = Number(amountStr);
    const minutes = unit === 'hours' ? value * 60 : value;
    if (!Number.isInteger(value) || value < 1) return 'Please enter a whole number of 1 or more for the reminder.';
    if (minutes > MAX_REMINDER_MINUTES) return 'Reminders can be set up to 7 days ahead.';
    return null;
  }

  async function askNotificationPermission() {
    try {
      if ('Notification' in window && Notification.permission === 'default') {
        await Notification.requestPermission();
      }
    } catch { /* ignore */ }
  }

  async function cancelReminder(sessionId) {
    setError(''); setMessage('');
    try {
      await notificationRequest(`/reminders/${sessionId}`, token, { method: 'DELETE' });
      setMessage('Reminder cancelled.');
      await refreshReminders(token);
    } catch (e) { setError(e.message); }
  }

  async function storeSelectedSlot() {
    if (!selectedSlot) return;
    setError(''); setMessage('');

    const wantsReminder = storeReminder.amount !== '';
    if (wantsReminder) {
      const problem = validateReminder(storeReminder.amount, storeReminder.unit);
      if (problem) { setError(problem); return; }
      await askNotificationPermission();
    }

    setBusy(true);
    try {
      const result = await luggageRequest('/sessions', token, {
        method: 'POST', body: JSON.stringify({ compartment_id: Number(selectedSlot) }),
      });
      let note = '';
      if (wantsReminder && result.session_id != null) {
        const value = Number(storeReminder.amount);
        const minutes = storeReminder.unit === 'hours' ? value * 60 : value;
        try {
          const r = await notificationRequest('/reminders', token, {
            method: 'POST',
            body: JSON.stringify({
              session_id: result.session_id,
              minutes,
              label: `${result.station_name} · ${result.compartment_label}`,
            }),
          });
          note = ` Reminder set for ${new Date(r.due_at).toLocaleTimeString()}.`;
          await refreshReminders(token);
        } catch (e) {
          note = ` Your luggage is stored, but the reminder could not be set: ${e.message}`;
        }
      }
      setMessage(`Luggage stored in ${result.station_name}, compartment ${result.compartment_label}. ${result.unlock?.message || ''}${note}`);
      setSelectedSlot('');
      setStoreReminder({ amount: '', unit: 'minutes' });
      await refresh(token);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function startCheckout(sessionId) {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await luggageRequest(`/sessions/${sessionId}/checkout`, token, { method: 'POST' });
      setCheckout(old => ({ ...old, [sessionId]: result }));
      setMessage(`Checkout calculated: ${result.amount} for ${result.billable_days} started 24-hour period(s).`);
      await refresh(token);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function pay(sessionId) {
    const payment = checkout[sessionId];
    if (!payment?.payment_reference) return startCheckout(sessionId);
    setBusy(true); setError(''); setMessage('');
    try {
      await luggageRequest(`/payments/${payment.payment_reference}/demo-pay`, token, { method: 'POST' });
      setMessage('Demo payment successful. You can now unlock your luggage locker.');
      await refresh(token);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function unlock(sessionId) {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await luggageRequest(`/sessions/${sessionId}/unlock`, token, { method: 'POST' });
      setUnlocked(old => ({ ...old, [sessionId]: true }));
      setMessage(result.message || 'Virtual locker unlocked. Collect your luggage, then confirm below.');
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function complete(sessionId) {
    setBusy(true); setError(''); setMessage('');
    try {
      await luggageRequest(`/sessions/${sessionId}/complete`, token, { method: 'POST' });
      if (reminders[sessionId]) {
        try { await notificationRequest(`/reminders/${sessionId}`, token, { method: 'DELETE' }); } catch { /* ignore */ }
        await refreshReminders(token);
      }
      setMessage('Retrieval completed. The XL compartment is now available.');
      await refresh(token);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  // Shows the pending reminder (with cancel) on a session card, if one exists.
  function renderReminder(session) {
    const due = reminders[session.session_id];
    if (!due) return null;
    return <div className="reminder">
      <p>⏰ Reminder set for <strong>{new Date(due).toLocaleString()}</strong></p>
      <button className="secondary-action" onClick={() => cancelReminder(session.session_id)}>Cancel reminder</button>
    </div>;
  }

  const activeSlots = slots.filter(slot => slot.size === 'XL');

  return <main>
    <button className="back" onClick={onBack}>← Back</button>
    <div className="overline">LUGGAGE SERVICES</div>
    <h1>{view === 'choose' ? 'What would you like to do?' : view === 'store' ? 'Store luggage' : 'Retrieve luggage'}</h1>
    <p className="subtitle">XL lockers only · SGD 10 per started 24-hour period · payment when you retrieve your luggage.</p>
    {error && <div className="error" role="alert">{error}</div>}
    {message && <div className="notice" role="status">{message}</div>}
    {notifications.map(n => <div className="notice" role="alert" key={n.id}>
      <strong>{n.title}</strong>{n.message ? ` — ${n.message}` : ''}{' '}
      <button className="secondary-action" onClick={() => markRead(n.id)}>Dismiss</button>
    </div>)}
    {user && <>
      {view === 'choose' && <div className="luggage-action-grid">
        <button className="choice" onClick={() => setView('store')}><span className="choice-icon">▤</span><strong>Store luggage</strong><small>Choose an available XL locker and store your bags.</small></button>
        <button className="choice" onClick={() => setView('retrieve')}><span className="choice-icon">↓</span><strong>Retrieve luggage</strong><small>Calculate payment, pay the demo fee and unlock your locker.</small></button>
      </div>}
      {view !== 'choose' && <button className="back" onClick={() => setView('choose')}>← Luggage services</button>}
      {view === 'store' && <div className="panel">
        <div className="booking-head"><strong>Signed in as {user.username}</strong></div>
        <h2>Available XL lockers</h2>
        {activeSlots.length === 0 ? <div className="empty">No XL compartments are currently available.</div> : <>
          <label>Choose a locker
            <select value={selectedSlot} onChange={e => setSelectedSlot(e.target.value)}>
              <option value="">Select an available XL locker</option>
              {activeSlots.map(slot => <option key={slot.compartment_id} value={slot.compartment_id}>{slot.station_name} · {slot.label}</option>)}
            </select>
          </label>
          <label>Remind me in (optional)
            <span className="reminder-row">
              <input
                type="number" min="1" step="1" inputMode="numeric"
                placeholder="e.g. 30"
                value={storeReminder.amount}
                onChange={e => setStoreReminder(r => ({ ...r, amount: e.target.value }))}
                aria-label="Reminder amount"
              />
              <select
                value={storeReminder.unit}
                onChange={e => setStoreReminder(r => ({ ...r, unit: e.target.value }))}
                aria-label="Reminder unit"
              >
                <option value="minutes">minutes</option>
                <option value="hours">hours</option>
              </select>
            </span>
          </label>
          <button className="primary submit" onClick={storeSelectedSlot} disabled={!selectedSlot || busy}>Store luggage and virtually unlock</button>
        </>}
      </div>}
      {view === 'retrieve' && <div className="panel">
        <div className="booking-head"><h2>My luggage sessions</h2><button onClick={() => { refresh(token); refreshReminders(token); }} disabled={busy}>Refresh</button></div>
        {sessions.length === 0 ? <div className="empty">You have no luggage sessions.</div> : <div className="booking-list">
          {sessions.map(session => <article className="booking-card" key={session.session_id}>
            <div className="booking-head"><strong>{session.station_name} · {session.compartment_label}</strong><span className="pill">{session.status.replaceAll('_', ' ')}</span></div>
            <p>Stored: {new Date(session.stored_at).toLocaleString()}</p>
            {session.status === 'STORED' && <button className="primary" disabled={busy} onClick={() => startCheckout(session.session_id)}>Calculate retrieval payment</button>}
            {session.status === 'PAYMENT_PENDING' && <>
              <p>Amount: <strong>{money(session.amount_cents)}</strong> · {session.billable_days} started 24-hour period(s)</p>
              <button className="primary" disabled={busy || !checkout[session.session_id]} onClick={() => pay(session.session_id)}>Pay (demo only)</button>
              {!checkout[session.session_id] && <button className="secondary-action" disabled={busy} onClick={() => startCheckout(session.session_id)}>Load payment details</button>}
            </>}
            {session.status === 'PAID_AWAITING_PICKUP' && <>
              <p>Paid: <strong>{money(session.amount_cents)}</strong></p>
              <button className="primary" disabled={busy} onClick={() => unlock(session.session_id)}>Unlock in app</button>
              <button className="secondary-action" onClick={() => complete(session.session_id)} disabled={busy || !unlocked[session.session_id]}>Confirm luggage collected</button>
            </>}
            {session.status !== 'RETRIEVED' && renderReminder(session)}
            {session.status === 'RETRIEVED' && <small>Retrieval completed {session.retrieved_at ? new Date(session.retrieved_at).toLocaleString() : ''}</small>}
          </article>)}
        </div>}
      </div>}
      {view !== 'choose' && <p className="muted">This is a virtual locker demonstration. App unlocks are recorded in the database; no physical locker hardware or real payment gateway is connected.</p>}
    </>}
  </main>;
}
