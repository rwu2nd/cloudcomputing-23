import { useCallback, useEffect, useRef, useState } from 'react';
import './notifications.css';

const API = import.meta.env.VITE_API_BASE_URL ?? '/api';
const TYPES = ['INFO', 'SUCCESS', 'WARNING', 'ERROR'];

// Browsers can't speak gRPC directly, so the FastAPI backend bridges:
//   REST  -> unary RPCs   (Send / List / MarkAsRead)
//   SSE   -> server-stream (StreamNotifications)
// EventSource can't send an Authorization header, so we read the SSE
// response with fetch() + ReadableStream instead.

function timeLabel(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString();
}

export default function NotificationViewer({ accessToken, account, onBack }) {
  const [items, setItems] = useState([]);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [stream, setStream] = useState('connecting'); // connecting | live | down
  const [log, setLog] = useState([]);
  const [form, setForm] = useState({ title: '', message: '', type: 'INFO' });
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [flashId, setFlashId] = useState(null);
  const seen = useRef(new Set());

  const pushLog = useCallback((dir, rpc, detail, ms) => {
    setLog(prev => [
      { key: crypto.randomUUID(), at: new Date().toLocaleTimeString(), dir, rpc, detail, ms },
      ...prev,
    ].slice(0, 40));
  }, []);

  const call = useCallback(async (rpc, path, options = {}) => {
    const started = performance.now();
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers: {
        ...(options.headers || {}),
        Authorization: `Bearer ${accessToken}`,
      },
    });
    const data = await response.json().catch(() => null);
    const ms = Math.round(performance.now() - started);
    if (!response.ok) {
      const msg = typeof data?.detail === 'string' ? data.detail : 'Request failed';
      pushLog('err', rpc, msg, ms);
      throw new Error(msg);
    }
    return { data, ms };
  }, [accessToken, pushLog]);

  const refresh = useCallback(async () => {
    try {
      const { data, ms } = await call(
        'ListNotifications',
        `/notifications?unread_only=${unreadOnly}&limit=50`,
      );
      const list = data?.notifications ?? [];
      list.forEach(n => seen.current.add(n.id));
      setItems(list);
      setError('');
      pushLog('out', 'ListNotifications', `${list.length} returned`, ms);
    } catch (e) {
      setError(e.message);
    }
  }, [call, unreadOnly, pushLog]);

  useEffect(() => { refresh(); }, [refresh]);

  // Server-streaming RPC, with reconnect.
  useEffect(() => {
    const controller = new AbortController();
    let retry;

    async function connect() {
      setStream('connecting');
      try {
        const response = await fetch(`${API}/notifications/stream`, {
          headers: { Authorization: `Bearer ${accessToken}`, Accept: 'text/event-stream' },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
        setStream('live');
        pushLog('in', 'StreamNotifications', 'stream opened');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split('\n\n');
          buffer = frames.pop();
          for (const frame of frames) {
            const line = frame.split('\n').find(l => l.startsWith('data:'));
            if (!line) continue; // heartbeat comment
            const n = JSON.parse(line.slice(5).trim());
            if (seen.current.has(n.id)) continue;
            seen.current.add(n.id);
            setItems(prev => [n, ...prev]);
            setFlashId(n.id);
            pushLog('in', 'StreamNotifications', `pushed #${n.id} "${n.title}"`);
          }
        }
        throw new Error('stream closed');
      } catch (e) {
        if (controller.signal.aborted) return;
        setStream('down');
        pushLog('err', 'StreamNotifications', `${e.message}, retrying in 2s`);
        retry = setTimeout(connect, 2000);
      }
    }

    connect();
    return () => {
      controller.abort();
      clearTimeout(retry);
    };
  }, [accessToken, pushLog]);

  useEffect(() => {
    if (!flashId) return undefined;
    const t = setTimeout(() => setFlashId(null), 1600);
    return () => clearTimeout(t);
  }, [flashId]);

  async function send(event) {
    event.preventDefault();
    setSending(true);
    setError('');
    try {
      const { data, ms } = await call('SendNotification', '/notifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      pushLog('out', 'SendNotification', `stored #${data.notification.id}`, ms);
      setForm(f => ({ ...f, title: '', message: '' }));
      // The new row arrives through the stream, which is the point of the demo.
    } catch (e) {
      setError(e.message);
    } finally {
      setSending(false);
    }
  }

  async function markRead(id) {
    try {
      const { ms } = await call('MarkAsRead', `/notifications/${id}/read`, { method: 'POST' });
      pushLog('out', 'MarkAsRead', `#${id}`, ms);
      setItems(prev => unreadOnly
        ? prev.filter(n => n.id !== id)
        : prev.map(n => (n.id === id ? { ...n, is_read: true } : n)));
    } catch (e) {
      setError(e.message);
    }
  }

  const unread = items.filter(n => !n.is_read).length;
  const visible = unreadOnly ? items.filter(n => !n.is_read) : items;

  return (
    <>
      <button className="back" onClick={onBack}>← Back</button>
      <div className="overline">NOTIFICATION SERVICE</div>
      <h1>Live notifications</h1>
      <p className="subtitle">
        Browser → FastAPI → gRPC → notification-service → Postgres, with pushes streamed back.
      </p>

      <div className="notif-grid">
        <section className="panel notif-col">
          <div className="booking-head">
            <h2>Inbox for {account.username}</h2>
            <span className={`stream-dot ${stream}`}>
              {stream === 'live' ? 'Stream live' : stream === 'connecting' ? 'Connecting…' : 'Stream down'}
            </span>
          </div>

          <div className="notif-toolbar">
            <span>{unread} unread</span>
            <label className="notif-check">
              <input type="checkbox" checked={unreadOnly} onChange={e => setUnreadOnly(e.target.checked)} />
              Unread only
            </label>
            <button type="button" onClick={refresh}>Refresh</button>
          </div>

          {error && <div className="error" role="alert">{error}</div>}

          {visible.length === 0
            ? <div className="empty">No notifications yet. Send one from the form.</div>
            : <ul className="notif-list">
              {visible.map(n => (
                <li key={n.id} className={`notif ${n.type.toLowerCase()} ${n.is_read ? 'read' : ''} ${flashId === n.id ? 'flash' : ''}`}>
                  <div className="notif-head">
                    <strong>{n.title}</strong>
                    <span className="notif-time">{timeLabel(n.created_at)}</span>
                  </div>
                  {n.message && <p>{n.message}</p>}
                  <div className="notif-foot">
                    <span className="notif-type">{n.type}</span>
                    {n.is_read
                      ? <span className="notif-readmark">Read</span>
                      : <button type="button" onClick={() => markRead(n.id)}>Mark as read</button>}
                  </div>
                </li>
              ))}
            </ul>}
        </section>
{/*
        <div className="notif-col">
          <form className="panel" onSubmit={send}>
            <h2>Send a notification</h2>
            <label>Title
              <input required maxLength={200} value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} placeholder="Parcel arrived" />
            </label>
            <label>Message
              <input value={form.message} onChange={e => setForm({ ...form, message: e.target.value })} placeholder="Locker B4 is ready" />
            </label>
            <label>Type
              <select value={form.type} onChange={e => setForm({ ...form, type: e.target.value })}>
                {TYPES.map(t => <option key={t}>{t}</option>)}
              </select>
            </label>
            <button className="primary submit" disabled={sending || !form.title.trim()}>
              {sending ? 'Sending…' : 'Send notification'}
            </button>
          </form>

          <section className="panel">
            <h2>gRPC call log</h2>
            {log.length === 0
              ? <div className="empty">Calls will appear here.</div>
              : <ol className="rpc-log">
                {log.map(l => (
                  <li key={l.key} className={l.dir}>
                    <span className="rpc-arrow">{l.dir === 'in' ? '←' : l.dir === 'out' ? '→' : '✕'}</span>
                    <span className="rpc-name">{l.rpc}</span>
                    <span className="rpc-detail">{l.detail}</span>
                    <span className="rpc-ms">{l.ms != null ? `${l.ms} ms` : l.at}</span>
                  </li>
                ))}
              </ol>}
          </section>
        </div>
      </div>
    </>
  );
}
