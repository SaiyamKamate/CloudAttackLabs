// Views assembled from several existing endpoints. Everything here goes
// through api.js; nothing is invented. Where the backend has no list endpoint
// (runs, responses), the list is rebuilt from records the backend does keep.
import { api } from '../api.js'

export const TERMINAL_RUN = ['completed', 'incomplete', 'failed', 'cleaned_up']

// Run `fn` over `items`, at most `limit` at a time, keeping order.
export async function mapLimit(items, limit, fn) {
  const results = new Array(items.length)
  let next = 0
  async function worker() {
    while (next < items.length) {
      const index = next++
      results[index] = await fn(items[index], index)
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker))
  return results
}

// Every run the backend has started. There is no GET /runs, but every run start
// is audited ("run.start", object_id = run id), so the audit log lists them and
// GET /runs/{id} supplies each run. Alerts, incidents and cleanup audit rows are
// joined by run id. Newest first.
export async function loadRuns() {
  const [starts, cleanups, alerts, incidents] = await Promise.all([
    api.listAudit({ action: 'run.start', limit: 1000 }),
    api.listAudit({ action: 'cleanup', limit: 1000 }),
    api.listAlerts(),
    api.listIncidents(),
  ])
  const ids = [...new Set(starts.map((row) => row.object_id).filter(Boolean))]
  const runs = await mapLimit(ids, 8, (id) => api.getRun(id).catch(() => null))

  const alertsByRun = groupBy(alerts, (a) => a.run_id)
  const incidentsByRun = groupBy(incidents, (i) => i.run_id)
  // Audit is newest first, so the first cleanup row per run is its latest.
  const cleanupByRun = {}
  for (const row of cleanups) if (!cleanupByRun[row.object_id]) cleanupByRun[row.object_id] = row

  return runs.filter(Boolean).map((run) => ({
    ...run,
    alerts: alertsByRun[run.id] || [],
    incidents: incidentsByRun[run.id] || [],
    cleanup: cleanupByRun[run.id] || null,
  })).sort((a, b) => time(b.started_at) - time(a.started_at))
}

// Events, oldest first, following the API's 1000-per-call limit forward with
// `since` until `max` events are read or there are no more.
export async function loadEvents({ runId, eventType, since, max = 5000 } = {}) {
  const seen = new Set()
  const events = []
  let cursor = since || undefined
  let truncated = false
  for (;;) {
    const page = await api.listEvents({ run_id: runId || undefined, event_type: eventType || undefined, since: cursor, limit: 1000 })
    for (const event of page) {
      if (!seen.has(event.event_id)) {
        seen.add(event.event_id)
        events.push(event)
      }
    }
    if (page.length < 1000) break
    if (events.length >= max) { truncated = true; break }
    const last = page[page.length - 1].timestamp
    if (last === page[0].timestamp) { truncated = true; break } // 1000 events in the same instant
    cursor = last
  }
  return { events, truncated }
}

// Every incident with its detail (alerts, responses, timeline), newest first.
export async function loadIncidentDetails() {
  const incidents = await api.listIncidents()
  const details = await mapLimit(incidents, 8, (i) => api.getIncident(i.id).catch(() => null))
  return details.filter(Boolean).sort((a, b) => time(b.created_at) - time(a.created_at))
}

// Every response action, with the incident it belongs to. There is no
// GET /responses, so they come from the incident details. Newest first.
export async function loadResponses() {
  const incidents = await loadIncidentDetails()
  const rows = []
  for (const incident of incidents) {
    for (const response of incident.responses) rows.push({ ...response, incident })
  }
  return rows.sort((a, b) => time(b.requested_at) - time(a.requested_at))
}

// Responses still waiting for a decision, from the audit log: requested and
// neither approved nor rejected. Each is confirmed with GET /responses/{id}.
export async function loadPendingApprovals() {
  const [requested, approved, rejected] = await Promise.all([
    api.listAudit({ action: 'response.requested', limit: 1000 }),
    api.listAudit({ action: 'response.approved', limit: 1000 }),
    api.listAudit({ action: 'response.rejected', limit: 1000 }),
  ])
  const decided = new Set([...approved, ...rejected].map((row) => row.object_id))
  const candidates = [...new Set(requested.map((row) => row.object_id))].filter((id) => !decided.has(id))
  const responses = await mapLimit(candidates, 6, (id) => api.getResponse(id).catch(() => null))
  return responses.filter((r) => r && r.status === 'pending_approval')
}

export function groupBy(items, key) {
  const groups = {}
  for (const item of items) {
    const k = key(item)
    if (k === null || k === undefined) continue
    ;(groups[k] ||= []).push(item)
  }
  return groups
}

export function countBy(items, key) {
  const counts = {}
  for (const item of items) {
    const k = key(item) ?? 'unknown'
    counts[k] = (counts[k] || 0) + 1
  }
  return counts
}

export function time(value) {
  return value ? new Date(value).getTime() : 0
}

// What detection found for a run, from the alerts it raised. For an
// observation scenario (type "observation") any alert is a false positive.
export function detectionOutcome(run, observation = false) {
  if (!TERMINAL_RUN.includes(run.status)) return { label: 'pending', tone: 'neutral' }
  if (run.alerts.length === 0) {
    return observation ? { label: 'no alerts', tone: 'ok' } : { label: 'not detected', tone: 'warn' }
  }
  return observation
    ? { label: `${run.alerts.length} false positive${run.alerts.length > 1 ? 's' : ''}`, tone: 'warn' }
    : { label: `detected (${run.alerts.length})`, tone: 'ok' }
}

// Cleanup state for a run, from the run status and the cleanup audit row.
export function cleanupOutcome(run) {
  if (run.cleanup) {
    const outcome = run.cleanup.detail?.outcome
    return outcome === 'success' ? { label: 'cleaned up', tone: 'ok' } : { label: `cleanup ${outcome || 'recorded'}`, tone: 'warn' }
  }
  if (run.status === 'cleaned_up') return { label: 'cleaned up', tone: 'ok' }
  if (!TERMINAL_RUN.includes(run.status)) return { label: '—', tone: 'neutral' }
  return { label: 'not cleaned', tone: 'neutral' }
}

// Split items into about `count` equal time buckets over their own time span,
// counting each item under its series. Labels are clock times within one day,
// otherwise dates with times.
export function timeBuckets(items, getTime, getSeries, count = 30) {
  const times = items.map((item) => time(getTime(item))).filter(Boolean)
  if (times.length === 0) return []
  const first = Math.min(...times)
  const last = Math.max(...times)
  const width = Math.max(60e3, (last - first) / count)
  const n = Math.floor((last - first) / width) + 1
  const buckets = Array.from({ length: n }, (_, i) => ({ start: first + i * width, values: {} }))
  for (const item of items) {
    const t = time(getTime(item))
    if (!t) continue
    const bucket = buckets[Math.min(n - 1, Math.floor((t - first) / width))]
    const series = getSeries(item)
    bucket.values[series] = (bucket.values[series] || 0) + 1
  }
  const sameDay = new Date(first).toDateString() === new Date(last).toDateString()
  return buckets.map((b) => {
    const d = new Date(b.start)
    const clock = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    return { ...b, label: sameDay ? clock : `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${clock}` }
  })
}
