import { useMemo, useState } from 'react'
import {
  Activity, ArrowLeft, Crosshair, FileText, Gauge, History, Lock, RefreshCw, Search, ShieldAlert,
  ShieldCheck, Siren,
} from 'lucide-react'
import { api } from '../api.js'
import { useUI } from '../lib/context.js'
import { useApi } from '../lib/hooks.js'
import { useAction } from '../lib/useAction.js'
import { groupBy, mapLimit } from '../lib/data.js'
import { highestSeverity, responseStatusTone, responseVerification, riskTone } from '../lib/describe.js'
import { fmt, fmtClock, humanize, score, shortId } from '../lib/format.js'
import { href, navigate } from '../lib/router.js'
import { Callout, KeyValue, PageHeader, Panel, Segmented, Tabs } from '../components/ui/Layout.jsx'
import { SeverityBadge, StatusBadge } from '../components/ui/Badges.jsx'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States.jsx'
import DataTable from '../components/ui/DataTable.jsx'
import RiskBreakdown from '../components/domain/RiskBreakdown.jsx'
import IncidentTimeline from '../components/domain/IncidentTimeline.jsx'
import ResponsePanel from '../components/domain/ResponsePanel.jsx'
import EventTable from '../components/domain/EventTable.jsx'
import AlertDetailDrawer from '../components/domain/AlertDetailDrawer.jsx'
import EventDetailDrawer from '../components/domain/EventDetailDrawer.jsx'

const SEV_RANK = { info: 0, low: 1, medium: 2, high: 3, critical: 4 }

export default function IncidentsPage({ selectedId }) {
  return selectedId ? <IncidentWorkspace incidentId={selectedId} /> : <IncidentList />
}

function RiskCell({ value }) {
  if (value === null || value === undefined) return <span className="dim">not scored</span>
  return (
    <div className="row" style={{ flexWrap: 'nowrap', gap: 8 }}>
      <span style={{ width: 22, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }} className="strong">{value}</span>
      <span className="risk-track" style={{ width: 60, flex: 'none' }}><span className={`risk-fill ${riskTone(value)}`} style={{ display: 'block', width: `${value}%` }} /></span>
    </div>
  )
}

function IncidentList() {
  const data = useApi(async () => {
    const [incidents, alerts] = await Promise.all([api.listIncidents(), api.listAlerts()])
    const alertsByIncident = groupBy(alerts, (a) => a.incident_id)
    return incidents.map((i) => ({ ...i, alerts: alertsByIncident[i.id] || [], severity: highestSeverity(alertsByIncident[i.id] || []) }))
  }, [])
  const [status, setStatus] = useState('all')
  const [search, setSearch] = useState('')
  const [severity, setSeverity] = useState('')

  const all = useMemo(() => data.data ?? [], [data.data])
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return all.filter((i) =>
      (status === 'all' || i.status === status) &&
      (!severity || i.severity === severity) &&
      (!term || [i.title, i.id, i.correlation_key, i.run_id].some((v) => v && String(v).toLowerCase().includes(term))),
    )
  }, [all, status, severity, search])
  const open = all.filter((i) => i.status === 'open').length

  return (
    <>
      <PageHeader
        title="Incidents"
        subtitle="Correlated alerts grouped into incidents, each with an explainable risk score, a timeline and a response."
        actions={<button className="btn" onClick={data.reload} disabled={data.refreshing}><RefreshCw size={14} className={data.refreshing ? 'spin' : ''} /> Refresh</button>}
      />
      <Panel flush>
        <div className="filters">
          <Segmented
            value={status}
            onChange={setStatus}
            options={[
              { value: 'all', label: `All ${all.length}` },
              { value: 'open', label: `Open ${open}` },
              { value: 'closed', label: `Closed ${all.length - open}` },
            ]}
          />
          <div className="search">
            <Search size={14} />
            <input className="input" placeholder="Search title, ID, correlation key…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search incidents" />
          </div>
          <select className="select" value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
            <option value="">All severities</option>
            {['critical', 'high', 'medium', 'low', 'info'].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <span className="filter-count">{rows.length} of {all.length}</span>
        </div>
        {data.error && !data.data ? <ErrorState message={data.error} onRetry={data.reload} /> : (
          <DataTable
            loading={data.loading}
            rows={rows}
            rowKey={(i) => i.id}
            onRowClick={(i) => navigate('incidents', i.id)}
            initialSort={{ key: 'created_at', dir: 'desc' }}
            empty={<EmptyState compact title={all.length ? 'No incidents match the filters' : 'No incidents yet'} />}
            footer="Severity is the highest severity among the incident’s alerts."
            columns={[
              { key: 'severity', header: 'Severity', render: (i) => i.severity ? <SeverityBadge severity={i.severity} /> : <span className="dim">—</span>, sortValue: (i) => SEV_RANK[i.severity] ?? -1 },
              { key: 'title', header: 'Incident', wrap: true, render: (i) => <><div className="cell-title">{i.title}</div><div className="cell-sub mono">{shortId(i.id)}</div></>, sortValue: (i) => i.title },
              { key: 'status', header: 'Status', render: (i) => <StatusBadge tone={i.status === 'open' ? 'warn' : 'neutral'}>{i.status}</StatusBadge>, sortValue: (i) => i.status },
              { key: 'risk', header: 'Risk score', render: (i) => <RiskCell value={i.risk_score} />, sortValue: (i) => i.risk_score },
              { key: 'alerts', header: 'Alerts', num: true, render: (i) => i.alerts.length, sortValue: (i) => i.alerts.length },
              { key: 'created_at', header: 'Opened', render: (i) => <span className="nowrap">{fmt(i.created_at)}</span>, sortValue: (i) => i.created_at },
            ]}
          />
        )}
      </Panel>
    </>
  )
}

// Phase strip: how the attack went from execution to response, from the
// incident's own timeline and records.
function AttackFlow({ incident, run }) {
  const t = incident.timeline
  const started = t.find((e) => e.type === 'scenario_started')
  const finished = t.find((e) => e.type === 'scenario_finished')
  const events = t.filter((e) => e.type === 'event')
  const alertsFired = t.filter((e) => e.type === 'alert_fired')
  const response = incident.responses[incident.responses.length - 1]
  const verification = response ? responseVerification(response) : null
  const sources = [...new Set(events.map((e) => e.evidence?.source).filter(Boolean))]

  const phases = [
    {
      step: 'Execution', icon: Crosshair,
      state: started ? 'done' : 'none',
      value: run ? run.scenario_id : started ? 'scenario run' : 'no run recorded',
      sub: started ? `${fmtClock(started.time)}${finished ? ` → ${fmtClock(finished.time)}` : ''}` : '—',
    },
    {
      step: 'Telemetry', icon: Activity,
      state: events.length ? 'done' : 'none',
      value: `${events.length} event${events.length === 1 ? '' : 's'}`,
      sub: sources.length ? sources.join(', ') : 'none on the timeline',
    },
    {
      step: 'Detection', icon: ShieldAlert,
      state: alertsFired.length || incident.alerts.length ? 'alert' : 'none',
      value: `${incident.alerts.length} alert${incident.alerts.length === 1 ? '' : 's'}`,
      sub: [...new Set(incident.alerts.map((a) => a.rule_id))].join(', ') || '—',
    },
    {
      step: 'Risk', icon: Gauge,
      state: incident.risk_score === null ? 'none' : 'alert',
      value: score(incident.risk_score),
      sub: `opened ${fmtClock(incident.created_at)}`,
    },
    {
      step: 'Response', icon: ShieldCheck,
      state: !response ? 'none' : response.status === 'verified' ? 'done' : response.status === 'failed' ? 'fail' : 'pending',
      value: response ? humanize(response.status) : 'none requested',
      sub: response ? `${response.action} · ${verification.label}` : '—',
    },
  ]

  return (
    <div className="phases">
      {phases.map((p) => (
        <div key={p.step} className={`phase ${p.state}`}>
          <div className="ph-step"><p.icon size={11} />{p.step}</div>
          <div className="ph-value" title={p.value}>{p.value}</div>
          <div className="ph-sub" title={p.sub}>{p.sub}</div>
        </div>
      ))}
    </div>
  )
}

function IncidentWorkspace({ incidentId }) {
  const { confirm } = useUI()
  const [busy, act] = useAction()
  const [tab, setTab] = useState('timeline')
  const [alertId, setAlertId] = useState(null)
  const [eventId, setEventId] = useState(null)

  const data = useApi(async () => {
    const incident = await api.getIncident(incidentId)
    const run = incident.run_id ? await api.getRun(incident.run_id).catch(() => null) : null
    return { incident, run }
  }, [incidentId])
  const incident = data.data?.incident
  const run = data.data?.run

  // The events behind every linked alert, for the Evidence tab.
  const alertIds = incident ? incident.alerts.map((a) => a.id).join(',') : ''
  const evidence = useApi(async () => {
    if (!alertIds) return []
    const details = await mapLimit(alertIds.split(','), 6, (id) => api.getAlert(id))
    const seen = new Map()
    for (const d of details) for (const e of d.events) seen.set(e.event_id, e)
    return [...seen.values()].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  }, [alertIds])

  async function closeIncident() {
    const ok = await confirm({
      title: 'Close this incident?',
      message: 'A closed incident takes no new response. The backend refuses to close it while a response is pending approval.',
      confirmLabel: 'Close incident',
      danger: true,
    })
    if (!ok) return
    await act('Close incident', () => api.closeIncident(incident.id), { success: 'Incident closed' })
    data.reload()
  }

  function openEvidence(item) {
    if (item.kind === 'event') setEventId(item.id)
    else if (item.kind === 'alert') setAlertId(item.id)
    else if (item.kind === 'run') navigate('runs', item.id)
  }

  if (data.loading) return <LoadingState label="Loading incident…" />
  if (!incident) {
    return (
      <>
        <a href={href('incidents')} className="row small" style={{ marginBottom: 12 }}><ArrowLeft size={13} /> All incidents</a>
        <Panel><ErrorState message={data.error} onRetry={data.reload} /></Panel>
      </>
    )
  }

  // The timeline as the backend built it, plus the incident's own opening time.
  const timeline = [...incident.timeline, {
    time: incident.created_at,
    type: 'incident_opened',
    description: `Incident opened: ${incident.alerts.length} correlated alert${incident.alerts.length === 1 ? '' : 's'}, risk ${score(incident.risk_score)}.`,
    evidence: { kind: 'incident', id: incident.id, href: `/api/v1/incidents/${incident.id}` },
  }].sort((a, b) => new Date(a.time) - new Date(b.time))
  const severity = highestSeverity(incident.alerts)

  return (
    <>
      <a href={href('incidents')} className="row small" style={{ marginBottom: 12, display: 'inline-flex' }}><ArrowLeft size={13} /> All incidents</a>
      <div className="page-header">
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ marginBottom: 6 }}>
            {severity && <SeverityBadge severity={severity} />}
            <StatusBadge tone={incident.status === 'open' ? 'warn' : 'neutral'}>{incident.status}</StatusBadge>
            <span className="dim small mono">{incident.id}</span>
          </div>
          <h1>{incident.title}</h1>
          <div className="subtitle">
            Opened {fmt(incident.created_at)}
            {run && <> · scenario <a href={href('scenarios', run.scenario_id)}>{run.scenario_id}</a></>}
            {incident.run_id && <> · run <a href={href('runs', incident.run_id)} className="mono">{shortId(incident.run_id)}</a></>}
          </div>
        </div>
        <div className="page-header-actions">
          <button className="btn" onClick={data.reload} disabled={data.refreshing}><RefreshCw size={14} className={data.refreshing ? 'spin' : ''} /> Refresh</button>
          {incident.run_id && <button className="btn" onClick={() => navigate('reports', incident.run_id)}><FileText size={14} /> View report</button>}
          {incident.status === 'open' && <button className="btn btn-danger" onClick={closeIncident} disabled={Boolean(busy)}><Lock size={14} /> Close incident</button>}
        </div>
      </div>

      <div className="stack">
        {data.error && <Callout tone="crit">{data.error}</Callout>}
        <AttackFlow incident={incident} run={run} />

        <div className="grid grid-main-side" style={{ alignItems: 'start' }}>
          <div className="panel">
            <div className="panel-body" style={{ paddingBottom: 4 }}>
              <Tabs
                active={tab}
                onChange={setTab}
                tabs={[
                  { key: 'timeline', label: 'Attack timeline', icon: History, count: timeline.length },
                  { key: 'alerts', label: 'Linked alerts', icon: ShieldAlert, count: incident.alerts.length },
                  { key: 'events', label: 'Supporting events', icon: Activity, count: evidence.data?.length },
                  { key: 'context', label: 'Context', icon: Siren },
                ]}
              />
            </div>
            {tab === 'timeline' && (
              <div className="panel-body" style={{ paddingTop: 0 }}>
                <IncidentTimeline entries={timeline} onEvidence={openEvidence} />
              </div>
            )}
            {tab === 'alerts' && (
              <DataTable
                rows={incident.alerts}
                rowKey={(a) => a.id}
                onRowClick={(a) => setAlertId(a.id)}
                columns={[
                  { key: 'severity', header: 'Severity', render: (a) => <SeverityBadge severity={a.severity} /> },
                  { key: 'rule_id', header: 'Rule', render: (a) => <code>{a.rule_id}</code> },
                  { key: 'title', header: 'Title', wrap: true },
                  { key: 'event_count', header: 'Events', num: true },
                  { key: 'created_at', header: 'Fired', render: (a) => <span className="nowrap">{fmt(a.created_at)}</span> },
                ]}
              />
            )}
            {tab === 'events' && (
              evidence.loading ? <LoadingState compact /> : evidence.error ? <ErrorState compact message={evidence.error} onRetry={evidence.reload} /> : (
                <EventTable events={evidence.data} onOpen={setEventId} pageSize={25} compact footer="Events that caused the linked alerts. Click one for its raw record." />
              )
            )}
            {tab === 'context' && (
              <div className="panel-body">
                <KeyValue rows={[
                  ['Incident ID', <code key="i">{incident.id}</code>],
                  ['Correlation key', <code key="c">{incident.correlation_key ?? '—'}</code>],
                  ['Lab', incident.lab_id ? <code key="l">{incident.lab_id}</code> : '—'],
                  ['Scenario', run ? `${run.scenario_id} v${run.scenario_version}` : '—'],
                  ['Run status', run ? <StatusBadge key="s" tone="neutral">{run.status}</StatusBadge> : '—'],
                  ['Run window', run ? `${fmt(run.started_at)} → ${fmt(run.finished_at)}` : '—'],
                  ['Responses', incident.responses.length
                    ? incident.responses.map((r) => <div key={r.id}><StatusBadge tone={responseStatusTone(r.status)}>{r.status}</StatusBadge> <code>{r.action}</code> {fmt(r.requested_at)}</div>)
                    : 'none'],
                ]} />
              </div>
            )}
          </div>

          <div className="stack">
            <Panel title="Risk assessment" icon={Gauge}>
              <RiskBreakdown score={incident.risk_score} explanation={incident.risk_explanation} />
            </Panel>
            <ResponsePanel
              key={`${incident.id}-${incident.status}`}
              incidentId={incident.id}
              onChange={data.reload}
              closed={incident.status === 'closed'}
            />
          </div>
        </div>
      </div>

      {alertId && <AlertDetailDrawer alertId={alertId} onClose={() => { setAlertId(null); data.reload() }} />}
      {eventId && <EventDetailDrawer eventId={eventId} onClose={() => setEventId(null)} />}
    </>
  )
}
