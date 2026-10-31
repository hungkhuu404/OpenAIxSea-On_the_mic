let presenter = false;
export function setPresenter(value) { presenter = value; }
export function apiURL(path) {
  return path + (presenter ? (path.includes('?') ? '&' : '?') + 'presenter=1' : '');
}
export async function request(path, options) {
  const response = await fetch(apiURL(path), options);
  if (!response.ok) throw new Error(`Request unavailable (${response.status})`);
  return response.json();
}
export function post(path, body) {
  return request(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
export async function fixtureSession() {
  return request('/api/ui/fixture');
}
export async function loadSession(id) {
  const session = id ? await request(`/api/sessions/${encodeURIComponent(id)}`) : await fixtureSession();
  session.law_display ||= {};
  const anchors = [...new Set((session.verdicts || []).flatMap(row => row.legal_basis || []))];
  await Promise.all(anchors.map(async anchor => {
    try { session.law_display[anchor] = (await request(`/api/ui/law/${encodeURIComponent(anchor)}`)).display_text; }
    catch { /* Never fall back to unfiltered law text. */ }
  }));
  return session;
}

export function events(publicMode, receive, failed = () => {}) {
  let since = 0, stopped = false, timer;
  const accept = event => {
    if (event.id != null) since = Math.max(since, Number(event.id));
    receive(event);
  };
  if (publicMode) {
    const poll = async () => {
      try {
        const rows = await request(`/api/events?since=${since}`);
        if (!stopped) for (const row of rows) accept(row);
      } catch (error) { if (!stopped) failed(error); }
      if (!stopped) timer = setTimeout(poll, 300);
    };
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }
  const stream = new EventSource(apiURL('/api/events/stream'));
  const parse = message => {
    try { accept(JSON.parse(message.data)); } catch (error) { failed(error); }
  };
  stream.onmessage = parse;
  for (const type of ['step', 'verdict', 'turn.verdict', 'session.flag', 'queue.changed', 'backfill.done']) {
    stream.addEventListener(type, message => {
      try {
        const row = JSON.parse(message.data);
        accept(row.type ? row : { type, payload: row, id: message.lastEventId });
      } catch (error) { failed(error); }
    });
  }
  stream.onerror = failed;
  return () => stream.close();
}
