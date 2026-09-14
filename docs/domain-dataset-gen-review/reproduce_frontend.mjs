/** Safe Node reproductions of normalized logic from auth.ts/api.ts/candidates page.
 * No production endpoints. These are NOT React/browser E2E or full repository tests.
 */
import assert from 'node:assert/strict';
import http from 'node:http';
import { writeFile } from 'node:fs/promises';
const results = [];
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const record = (test, observed, details={}) => { assert(observed, test); results.push({ test, bug_observed: true, ...details }); };

function createAuthLogic(fetchImpl) {
  // In-memory TokenStore replaces browser localStorage only; generation guards retained.
  let generation = 0, access = null, refresh = null, pendingRefresh = null, authFailed = false;
  const store = {
    getAccessToken: () => access,
    getRefreshToken: () => refresh,
    currentGeneration: () => generation,
    setTokens(a, r, options) {
      if (options?.expectedGeneration !== undefined && options.expectedGeneration !== generation) return;
      generation++; access = a; refresh = r;
    },
    clearTokens() { generation++; access = refresh = null; },
  };
  async function requestRefresh() {
    const rt = store.getRefreshToken();
    if (!rt) return false;
    const gen = store.currentGeneration();
    try {
      const res = await fetchImpl('/auth/refresh', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      if (!data.access_token || !data.refresh_token) return false;
      store.setTokens(data.access_token, data.refresh_token, { expectedGeneration: gen });
      return true;
    } catch { return false; }
  }
  function refreshToken() {
    if (!pendingRefresh) pendingRefresh = requestRefresh().catch(() => false).finally(() => { pendingRefresh = null; });
    return pendingRefresh;
  }
  function handleAuthFailure() {
    if (authFailed) return;
    authFailed = true;
    store.clearTokens();
  }
  async function fetchApi(path, options) {
    const buildHeaders = () => ({ ...options?.headers, Authorization: `Bearer ${store.getAccessToken()}` });
    let res = await fetchImpl(path, { ...options, headers: buildHeaders() });
    if (res.status === 401) {
      const refreshed = await refreshToken();
      if (refreshed) res = await fetchImpl(path, { ...options, headers: buildHeaders() });
    }
    if (res.status === 401) handleAuthFailure();
    return res;
  }
  return { store, refreshToken, fetchApi };
}

async function sessionRaces() {
  for (const success of [false, true]) {
    const gate = deferred(), entered = deferred(), writes = [];
    let attempts = 0;
    const logic = createAuthLogic(async (path, opts) => {
      if (path === '/auth/refresh') { entered.resolve(); return gate.promise; }
      attempts++;
      if (attempts === 1) return { status: 401, ok: false };
      writes.push({ authorization: opts.headers.Authorization, body: opts.body });
      return { status: 200, ok: true };
    });
    logic.store.setTokens('OLD_USER_ACCESS', 'OLD_USER_REFRESH');
    const pending = logic.fetchApi('/candidates/example', { method: 'PATCH', body: 'OLD_USER_MUTATION' });
    await entered.promise;
    logic.store.setTokens('NEW_USER_ACCESS', 'NEW_USER_REFRESH');
    gate.resolve({ ok: success, status: success ? 200 : 401,
      json: async () => ({ access_token: 'OLD_REFRESHED_ACCESS', refresh_token: 'OLD_REFRESHED_RT' }) });
    await pending;
    if (!success) record('old_refresh_failure_clears_new_session', logic.store.getAccessToken() === null,
      { new_session_token_after_old_failure: logic.store.getAccessToken() });
    else record('old_mutation_replayed_as_new_user', writes[0]?.authorization === 'Bearer NEW_USER_ACCESS',
      { replayed_write: writes[0], new_session_token: logic.store.getAccessToken() });
  }
}

function createTimeoutError(timeout) { const e = new Error(`timeout ${timeout}`); e.name = 'TimeoutError'; return e; }
// Transcribed fetchWithTimeout with only TypeScript annotations removed.
function fetchWithTimeout(url, options, timeout, externalSignal) {
  if (!timeout && !externalSignal) return fetch(url, options);
  const timeoutController = timeout ? new AbortController() : null;
  const combinedController = timeoutController && externalSignal ? new AbortController() : null;
  let didTimeout = false;
  const timer = timeoutController ? setTimeout(() => { didTimeout = true; timeoutController.abort(); }, timeout) : null;
  let cleanup = () => {};
  let signal = externalSignal ?? timeoutController?.signal;
  if (timeoutController && externalSignal && combinedController) {
    const forwardAbort = () => { if (!combinedController.signal.aborted) combinedController.abort(); };
    if (externalSignal.aborted || timeoutController.signal.aborted) forwardAbort();
    else {
      externalSignal.addEventListener('abort', forwardAbort, { once: true });
      timeoutController.signal.addEventListener('abort', forwardAbort, { once: true });
      cleanup = () => {
        externalSignal.removeEventListener('abort', forwardAbort);
        timeoutController.signal.removeEventListener('abort', forwardAbort);
      };
    }
    signal = combinedController.signal;
  }
  return fetch(url, { ...options, signal }).catch(error => {
    if (didTimeout && timeout) throw createTimeoutError(timeout);
    throw error;
  }).finally(() => { if (timer) clearTimeout(timer); cleanup(); });
}

async function headerBodyTimeout() {
  const server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.flushHeaders();
    setTimeout(() => res.end('{"ok":true}'), 600);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const abort = new AbortController();
  const start = performance.now();
  try {
    const res = await fetchWithTimeout(`http://127.0.0.1:${server.address().port}/slow-body`, {}, 200, abort.signal);
    setTimeout(() => abort.abort(), 20);
    const body = await res.json();
    const elapsed = Math.round(performance.now() - start);
    record('response_body_ignores_deadline_and_abort', body.ok && elapsed > 200 && abort.signal.aborted,
      { deadline_ms: 200, elapsed_ms: elapsed, caller_aborted: abort.signal.aborted, body_completed: true });
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}

async function hangingRefresh() {
  let calls = 0;
  const logic = createAuthLogic(() => { calls++; return new Promise(() => {}); });
  logic.store.setTokens('a', 'r');
  const p1 = logic.refreshToken(), p2 = logic.refreshToken();
  const status = await Promise.race([p1.then(() => 'resolved'), delay(30).then(() => 'pending')]);
  record('refresh_singleflight_has_no_own_deadline', p1 === p2 && status === 'pending' && calls === 1,
    { shared_promise: p1 === p2, transport_calls: calls, pending_after_probe: true,
      note: 'The lack of a deadline is verified by source; 30 ms is only a bounded observation, not a claim about infinite real-network time.' });
}

async function candidateSelectionRace() {
  let expandedId, chunkContent, sourceChunk;
  const a = deferred(), b = deferred();
  function expand(id, request) {
    expandedId = id;
    // candidates/page.tsx applies chunk responses without an expanded-id guard.
    return request.then(chunk => { chunkContent = chunk.content; sourceChunk = chunk.id; });
  }
  const pa = expand('candidate-A', a.promise);
  const pb = expand('candidate-B', b.promise);
  b.resolve({ id: 'chunk-B', content: 'Evidence B' }); await pb;
  a.resolve({ id: 'chunk-A', content: 'Evidence A' }); await pa;
  record('stale_candidate_evidence_response', expandedId === 'candidate-B' && sourceChunk === 'chunk-A',
    { expanded_candidate: expandedId, displayed_source_chunk: sourceChunk, displayed_content: chunkContent });
}

await sessionRaces();
await headerBodyTimeout();
await hangingRefresh();
await candidateSelectionRace();
const output = { mode: 'local normalized source-logic reproductions; NOT React/browser E2E', node: process.version,
  total: results.length, bug_observed: results.filter(r => r.bug_observed).length, results };
await writeFile(new URL('./frontend-results.json', import.meta.url), JSON.stringify(output, null, 2)+'\n');
console.log(JSON.stringify(output, null, 2));
