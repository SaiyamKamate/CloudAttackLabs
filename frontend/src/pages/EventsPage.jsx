import { useMemo, useState } from 'react'
import { Activity, Database, RefreshCw, Search } from 'lucide-react'
import { api } from '../api.js'
import { useApi } from '../lib/hooks.js'
import { countBy, loadEvents, timeBuckets } from '../lib/data.js'
import { fmt, shortId } from '../lib/format.js'
import { Callout, MetricCard, PageHeader, Panel, Segmented } from '../components/ui/Layout.jsx'
import { EmptyState, ErrorState } from '../components/ui/States.jsx'
import { HBarList, Legend, StackedBarChart } from '../components/ui/Charts.jsx'
import { SERIES_COLOURS } from '../lib/colours.js'
import EventTable from '../components/domain/EventTable.jsx'
import EventDetailDrawer from '../components/domain/EventDetailDrawer.jsx'

const RANGES = [
  { value: '15m', label: '15 min', ms: 15 * 60e3 },
  { value: '1h', label: '1 h', ms: 3600e3 },
  { value: '24h', label: '24 h', ms: 86400e3 },
  { value: '7d', label: '7 d', ms: 7 * 86400e3 },
  { value: 'all', label: 'All', ms: null },
]
const MAX_EVENTS = 5000
const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info']

// The event explorer, on GET /events. The server filters by time (`since`),
// run and event type; source, severity and the text search filter the loaded
// events in the browser.
export default function EventsPage() {
  const [range, setRange] = useState('7d')
  const [runId, setRunId] = useState('')
  const [eventType, setEventType] = useState('')
  const [source, setSource] = useState('')
  const [severity, setSeverity] = useState('')
  const [search, setSearch] = useState('')
  const [eventId, setEventId] = useState(null)

  // Runs for the run filter, from the audit log (no extra request per run).
  const runs = useApi(() => api.listAudit({ action: 'run.start', limit: 1000 }), [])

  const events = useApi(() => {
    const ms = RANGES.find((r) => r.value === range)?.ms
    const since = ms ? new Date(Date.now() - ms).toISOString() : undefined
    return loadEvents({ runId, eventType, since, max: MAX_EVENTS })
  }, [range, runId, eventType])

  const loaded = useMemo(() => events.data?.events ?? [], [events.data])
  const types = useMemo(() => [...new Set(loaded.map((e) => e.event_type))].sort(), [loaded])
  const sources = useMemo(() => [...new Set(loaded.map((e) => e.source))].sort(), [loaded])

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return loaded.filter((e) =>
      (!source || e.source === source) &&
      (!severity || e.severity === severity) &&
      (!term || [e.message, e.resource, e.source_ip, e.container_id, e.username, e.raw_event_reference, e.rule_id, e.event_id, e.scenario_id]
        .some((v) => v && String(v).toLowerCase().includes(term))),
    )
  }, [loaded, source, severity, search])

  const buckets = useMemo(() => timeBuckets(rows, (e) => e.timestamp, (e) => e.severity, 40), [rows])
  const bySource = countBy(rows, (e) => e.source)
  const byType = countBy(rows, (e) => e.event_type)
  const bySeverity = countBy(rows, (e) => e.severity)
  const presentSeverities = SEVERITIES.filter((s) => bySeverity[s])

  return (
    <>
      <PageHeader
        title="Events & Telemetry"
        subtitle="Normalized events collected from ShopCloud’s logs, Docker and Falco. Every row links to its raw evidence."
        actions={
          <>
            <Segmented options={RANGES} value={range} onChange={setRange} />
            <button className="btn" onClick={events.reload} disabled={events.refreshing}><RefreshCw size={14} className={events.refreshing ? 'spin' : ''} /> Refresh</button>
          </>
        }
      />

      <div className="stack">
        {events.data?.truncated && (
          <Callout tone="warn" title={`Showing the first ${loaded.length} events of this range`}>
            The events API returns events oldest first. Narrow the time range or pick a run to see later events.
          </Callout>
        )}

        <div className="grid grid-4">
          <MetricCard label="Events in view" icon={Activity} value={events.data ? rows.length : '…'} sub={events.data ? `${loaded.length} loaded from the API` : 'loading'} />
          <MetricCard label="Sources" icon={Database} value={events.data ? Object.keys(bySource).length : '…'} sub={Object.entries(bySource).map(([k, v]) => `${k} ${v}`).join(' · ') || '—'} />
          <MetricCard label="Event types" value={events.data ? Object.keys(byType).length : '…'} sub={Object.keys(byType).slice(0, 3).join(', ') || '—'} />
          <MetricCard label="Medium or higher" value={events.data ? (bySeverity.medium || 0) + (bySeverity.high || 0) + (bySeverity.critical || 0) : '…'} accent="high" sub="by event severity" />
        </div>

        <div className="grid grid-main-side">
          <Panel title="Event volume" meta={rows.length ? `${fmt(rows[0]?.timestamp)} → ${fmt(rows[rows.length - 1]?.timestamp)}` : undefined}>
            {rows.length > 0 ? (
              <>
                <StackedBarChart buckets={buckets} series={presentSeverities} height={150} />
                <Legend items={presentSeverities.map((s) => ({ label: s, colour: SERIES_COLOURS[s], value: bySeverity[s] }))} />
              </>
            ) : <EmptyState compact title="No events in this view" />}
          </Panel>
          <Panel title="Top event types">
            {Object.keys(byType).length ? (
              <HBarList
                total={rows.length}
                items={Object.entries(byType).sort((a, b) => b[1] - a[1]).slice(0, 7).map(([label, value]) => ({ label: label.replaceAll('_', ' '), value }))}
              />
            ) : <EmptyState compact title="No events" />}
          </Panel>
        </div>

        <Panel flush>
          <div className="filters">
            <div className="search">
              <Search size={14} />
              <input className="input" placeholder="Search message, IP, container, user, raw reference…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search events" />
            </div>
            <select className="select" value={runId} onChange={(e) => setRunId(e.target.value)} aria-label="Run">
              <option value="">All runs</option>
              {(runs.data ?? []).map((row) => (
                <option key={row.id} value={row.object_id}>{row.detail?.scenario_id ?? 'run'} · {shortId(row.object_id)} · {new Date(row.timestamp).toLocaleString()}</option>
              ))}
            </select>
            <select className="select" value={eventType} onChange={(e) => setEventType(e.target.value)} aria-label="Event type">
              <option value="">All event types</option>
              {[...new Set([...types, eventType].filter(Boolean))].map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <select className="select" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source">
              <option value="">All sources</option>
              {sources.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select className="select" value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
              <option value="">All severities</option>
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <span className="filter-count">{rows.length} of {loaded.length}</span>
          </div>
          {events.error && !events.data ? <ErrorState message={events.error} onRetry={events.reload} /> : (
            <EventTable
              key={`${range}-${runId}-${eventType}`}
              events={rows}
              loading={events.loading || (events.refreshing && !events.data)}
              onOpen={setEventId}
              newestFirst
              empty={<EmptyState compact title="No events match" message={range !== 'all' ? 'Try a wider time range.' : undefined} />}
              footer="Newest first. Click an event to open its raw record."
            />
          )}
        </Panel>
      </div>

      {eventId && <EventDetailDrawer eventId={eventId} onClose={() => setEventId(null)} />}
    </>
  )
}
