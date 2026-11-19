import { useCallback, useEffect, useState } from 'react';

const API = import.meta.env.VITE_API_BASE_URL ?? '/api';

/**
 * Keeps a live list of the signed-in user's unread notifications.
 * Loads the unread list once, then listens to /notifications/stream (SSE),
 * so `unreadCount` updates the moment a new notification arrives.
 */
export default function useNotifications(token) {
  const [notifications, setNotifications] = useState([]); // unread only

  const reload = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API}/notifications?unread_only=true&limit=100`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return;
      const data = await res.json();
      setNotifications((data?.notifications || []).filter(n => !n.is_read));
    } catch { /* non-critical */ }
  }, [token]);

  const markRead = useCallback(async id => {
    setNotifications(old => old.filter(n => n.id !== id)); // update the badge immediately
    try {
      await fetch(`${API}/notifications/${id}/read`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch { /* already hidden locally; reload() will resync */ }
  }, [token]);

  useEffect(() => {
    if (!token) { setNotifications([]); return undefined; }
    let stopped = false;
    const controller = new AbortController();

    function onIncoming(n) {
      if (!n || n.is_read) return;
      setNotifications(old => (old.some(x => x.id === n.id) ? old : [n, ...old]));
      try {
        if ('Notification' in window && Notification.permission === 'granted') {
          new Notification(n.title || 'New notification', { body: n.message || '' });
        }
      } catch { /* notifications unavailable */ }
    }

    async function listen() {
      while (!stopped) {
        try {
          const res = await fetch(`${API}/notifications/stream`, {
            headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
            signal: controller.signal,
          });
          if (!res.ok || !res.body) throw new Error('stream unavailable');
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let idx;
            while ((idx = buffer.indexOf('\n\n')) !== -1) {
              const frame = buffer.slice(0, idx);
              buffer = buffer.slice(idx + 2);
              const line = frame.split('\n').find(l => l.startsWith('data: '));
              if (!line) continue;
              try { onIncoming(JSON.parse(line.slice(6))); } catch { /* ignore malformed frame */ }
            }
          }
        } catch {
          if (stopped) return;
        }
        if (!stopped) {
          await new Promise(resolve => setTimeout(resolve, 3000));
          reload(); // catch anything missed while disconnected
        }
      }
    }

    reload();
    listen();
    return () => { stopped = true; controller.abort(); };
  }, [token, reload]);

  return { notifications, unreadCount: notifications.length, markRead, reload };
}
