import { loadSession, events } from './data.js';

// Keep a single view alive while serialized reads refresh its evidence.
export function watchSession(view, sessionId, {
  publicMode = false, active = () => true, load = loadSession, subscribe = events,
} = {}) {
  let stopped = false, refreshing = false, dirty = false;
  const unsubscribe = subscribe(publicMode, async event => {
    if (stopped || !active()) return;
    const globalRefresh = event.type === 'backfill.done';
    if (!globalRefresh && event.payload.session_id !== sessionId) return;
    view.event(event);
    if (!globalRefresh && !['verdict', 'turn.verdict', 'session.flag'].includes(event.type)) return;
    dirty = true;
    if (refreshing) return;
    refreshing = true;
    try {
      do {
        dirty = false;
        const updated = await load(sessionId, publicMode);
        if (stopped || !active()) return;
        if (updated) view.update(updated);
      } while (dirty);
    } catch { /* Keep the last confirmed evidence visible; the next event retries. */ }
    finally { refreshing = false; }
  });
  return () => { stopped = true; unsubscribe(); };
}
