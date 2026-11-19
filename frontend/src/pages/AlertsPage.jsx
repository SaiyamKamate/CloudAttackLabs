import { useMemo, useState } from 'react'
import { RefreshCw, Search, ShieldAlert } from 'lucide-react'
import { api } from '../api.js'
import { useApi } from '../lib/hooks.js'
import { countBy } from '../lib/data.js'
import { fmt, shortId } from '../lib/format.js'
import { href, navigate } from '../lib/router.js'
import { MetricCard, PageHeader, Panel } from '../components/ui/Layout.jsx'
import { SeverityBadge } from '../components/ui/Badges.jsx'
import { EmptyState, ErrorState } from '../components/ui/States.jsx'
import DataTable from '../components/ui/DataTable.jsx'
import AlertDetailDrawer from '../components/domain/AlertDetailDrawer.jsx'

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info']
const SEV_RANK = { info: 0, low: 1, medium: 2, high: 3, critical: 4 }

// Every alert the detection engine raised (GET /alerts), filterable, with an
// investigation drawer per alert.
export default function AlertsPage({ selectedId }) {
  const alerts = useApi(() => api.listAlerts(), [])
  const [search, setSearch] = useState('')
  const [severity, setSeverity] = useState('')
  const [rule, setRule] = useState('')
  const [linked, setLinked] = useState('')

  const all = useMemo(() => alerts.data ?? [], [alerts.data])
  const rules = useMemo(() => [...new Set(all.map((a) => a.rule_id))].sort(), [all])
  const bySeverity = countBy(all, (a) => a.severity)

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return all.filter((a) =>
      (!severity || a.severity === severity) &&
      (!rule || a.rule_id === rule) &&
      (!linked || (linked === 'linked' ? a.incident_id : !a.incident_id)) &&
      (!term || [a.title, a.rule_id, a.description, a.id, a.run_id].some((v) => v && String(v).toLowerCase().includes(term))),
    )
  }, [all, search, severity, rule, linked])

  return (
    <>
      <PageHeader
        title="Detection & Alerts"
        subtitle="Alerts raised by the detection rules from collected telemetry. Open one to see the events that triggered it."
        actions={<button className="btn" onClick={alerts.reload} disabled={alerts.refreshing}><RefreshCw size={14} className={alerts.refreshing ? 'spin' : ''} /> Refresh</button>}
      />

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <MetricCard label="Total alerts" icon={ShieldAlert} value={alerts.data ? all.length : '…'} sub={`${rules.length} rule${rules.length === 1 ? '' : 's'} fired`} />
        {['high', 'medium', 'low'].map((s) => (
          <MetricCard key={s} label={`${s[0].toUpperCase()}${s.slice(1)} severity`} value={alerts.data ? bySeverity[s] || 0 : '…'} accent={s === 'high' ? 'high' : undefined} onClick={() => setSeverity(severity === s ? '' : s)} sub={severity === s ? 'filter on — click to clear' : 'click to filter'} />
        ))}
      </div>

      <Panel flush>
        <div className="filters">
          <div className="search">
            <Search size={14} />
            <input className="input" placeholder="Search title, rule, description, ID…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search alerts" />
          </div>
          <select className="select" value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
            <option value="">All severities</option>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="select" value={rule} onChange={(e) => setRule(e.target.value)} aria-label="Rule">
            <option value="">All rules</option>
            {rules.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <select className="select" value={linked} onChange={(e) => setLinked(e.target.value)} aria-label="Incident link">
            <option value="">With or without incident</option>
            <option value="linked">Linked to an incident</option>
            <option value="unlinked">No incident</option>
          </select>
          <span className="filter-count">{rows.length} of {all.length}</span>
        </div>
        {alerts.error && !alerts.data ? <ErrorState message={alerts.error} onRetry={alerts.reload} /> : (
          <DataTable
            loading={alerts.loading}
            rows={rows}
            rowKey={(a) => a.id}
            selectedKey={selectedId}
            onRowClick={(a) => navigate('alerts', a.id)}
            initialSort={{ key: 'created_at', dir: 'desc' }}
            empty={<EmptyState compact title={all.length ? 'No alerts match the filters' : 'No alerts yet'} message={all.length ? undefined : 'Run a scenario to generate telemetry and detections.'} />}
            columns={[
              { key: 'severity', header: 'Severity', render: (a) => <SeverityBadge severity={a.severity} />, sortValue: (a) => SEV_RANK[a.severity] ?? -1 },
              {
                key: 'title',
                header: 'Alert',
                wrap: true,
                render: (a) => <><div className="cell-title">{a.title}</div><div className="cell-sub mono">{a.rule_id}</div></>,
                sortValue: (a) => a.title,
              },
              { key: 'event_count', header: 'Events', num: true, sortValue: (a) => a.event_count },
              { key: 'first', header: 'First event', render: (a) => <span className="nowrap">{fmt(a.first_event_at)}</span>, sortValue: (a) => a.first_event_at },
              { key: 'last', header: 'Last event', render: (a) => <span className="nowrap">{fmt(a.last_event_at)}</span>, sortValue: (a) => a.last_event_at },
              {
                key: 'incident',
                header: 'Related incident',
                render: (a) => a.incident_id
                  ? <a href={href('incidents', a.incident_id)} onClick={(e) => e.stopPropagation()} className="mono small">{shortId(a.incident_id)}</a>
                  : <span className="dim">—</span>,
              },
              { key: 'created_at', header: 'Raised', render: (a) => <span className="nowrap">{fmt(a.created_at)}</span>, sortValue: (a) => a.created_at },
            ]}
          />
        )}
      </Panel>

      {selectedId && <AlertDetailDrawer alertId={selectedId} onClose={() => navigate('alerts')} />}
    </>
  )
}
