import { useMemo } from 'react'
import {
  Activity, AlertTriangle, FlaskConical, Gauge, RefreshCw, ShieldAlert, ShieldCheck, Siren,
} from 'lucide-react'
import { api } from '../api.js'
import { usePlatform } from '../lib/context.js'
import { useApi, useNow } from '../lib/hooks.js'
import { TERMINAL_RUN, countBy, groupBy, loadRuns, time, timeBuckets } from '../lib/data.js'
import { auditDetail, highestSeverity, runStatusTone } from '../lib/describe.js'
import { fmt, fmtAgo, humanize, shortId } from '../lib/format.js'
import { navigate } from '../lib/router.js'
import { MetricCard, PageHeader, Panel } from '../components/ui/Layout.jsx'
import { SeverityBadge, StatusBadge } from '../components/ui/Badges.jsx'
import { EmptyState, ErrorState, SkeletonRows } from '../components/ui/States.jsx'
import { Donut, Legend, StackedBarChart } from '../components/ui/Charts.jsx'
import { SERIES_COLOURS } from '../lib/colours.js'
import HealthStatusPanel from '../components/domain/HealthStatusPanel.jsx'

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info']
const RUN_SERIES = ['completed', 'cleaned_up', 'incomplete', 'failed', 'running', 'pending']

function ListRow({ onClick, left, title, sub, side }) {
  return (
    <button className="list-row" onClick={onClick}>
      {left && <span className="lr-left">{left}</span>}
      <span className="lr-main">
        <div className="lr-title">{title}</div>
        {sub && <div className="lr-sub">{sub}</div>}
      </span>
      {side && <span className="lr-side">{side}</span>}
    </button>
  )
}

// The security operations overview. Every figure is counted from backend
// responses; nothing is estimated.
export default function OverviewPage() {
  const { activeLab, labDetail, backend, attention, refreshLabDetail } = usePlatform()
  const now = useNow(30000)
  const data = useApi(async () => {
    const [runs, alerts, incidents, responseAudit] = await Promise.all([
      loadRuns(),
      api.listAlerts(),
      api.listIncidents(),
      api.listAudit({ object_type: 'response_action', limit: 12 }),
    ])
    return { runs, alerts, incidents, responseAudit }
  }, [])

  const d = data.data
  const stats = useMemo(() => {
    if (!d) return null
    const alertsByIncident = groupBy(d.alerts, (a) => a.incident_id)
    const incidents = d.incidents.map((i) => ({ ...i, severity: highestSeverity(alertsByIncident[i.id] || []) }))
    const open = incidents.filter((i) => i.status === 'open')
    return {
      incidents,
      open,
      highOpen: open.filter((i) => ['critical', 'high'].includes(i.severity)).length,
      highAll: incidents.filter((i) => ['critical', 'high'].includes(i.severity)).length,
      activeRuns: d.runs.filter((r) => !TERMINAL_RUN.includes(r.status)).length,
      bySeverity: countBy(d.alerts, (a) => a.severity),
    }
  }, [d])

  const buckets = useMemo(() => (d ? timeBuckets(d.runs, (r) => r.started_at, (r) => r.status, 30) : []), [d])
  const presentRunSeries = RUN_SERIES.filter((s) => buckets.some((b) => b.values[s]))
  const span = d?.runs.length ? `${fmt(d.runs[d.runs.length - 1].started_at)} → ${fmt(d.runs[0].started_at)}` : undefined

  function refresh() {
    data.reload()
    refreshLabDetail()
  }

  return (
    <>
      <PageHeader
        title="Security Overview"
        subtitle={activeLab ? `Lab ${activeLab.name} · ${activeLab.environment} · target ${activeLab.target}` : 'No lab yet'}
        actions={<button className="btn" onClick={refresh} disabled={data.refreshing}><RefreshCw size={14} className={data.refreshing ? 'spin' : ''} /> Refresh</button>}
      />

      <div className="stack">
        <Panel title="Environment health" flush actions={<a className="btn btn-sm btn-ghost" href="#/labs">Manage lab →</a>}>
          <HealthStatusPanel lab={activeLab} detail={labDetail} backend={backend} />
        </Panel>

        {data.error && !d && <Panel><ErrorState message={data.error} onRetry={data.reload} /></Panel>}

        <div className="grid grid-6">
          <MetricCard label="Experiment runs" icon={FlaskConical} value={d ? d.runs.length : '…'} sub="recorded in the audit log" onClick={() => navigate('runs')} />
          <MetricCard label="Active runs" icon={Activity} value={stats ? stats.activeRuns : '…'} sub={labDetail?.active_run ? labDetail.active_run.scenario_id : 'none in progress'} accent={stats?.activeRuns ? 'info' : undefined} />
          <MetricCard label="Total alerts" icon={ShieldAlert} value={d ? d.alerts.length : '…'} sub={stats ? `${stats.bySeverity.high || 0} high · ${stats.bySeverity.medium || 0} medium` : ''} onClick={() => navigate('alerts')} />
          <MetricCard label="Open incidents" icon={Siren} value={stats ? stats.open.length : '…'} sub={stats ? `of ${stats.incidents.length} total` : ''} accent={stats?.open.length ? 'high' : undefined} onClick={() => navigate('incidents')} />
          <MetricCard label="Critical / high incidents" icon={AlertTriangle} value={stats ? stats.highAll : '…'} sub={stats ? `${stats.highOpen} still open · by alert severity` : ''} accent={stats?.highOpen ? 'critical' : undefined} />
          <MetricCard label="Pending approvals" icon={ShieldCheck} value={attention.loaded ? attention.pending.length : '…'} sub="response actions" accent={attention.pending.length ? 'high' : undefined} onClick={() => navigate('responses')} />
        </div>

        <div className="grid grid-main-side">
          <Panel title="Experiment activity" icon={Gauge} meta={span}>
            {!d ? <SkeletonRows rows={4} /> : d.runs.length === 0 ? (
              <EmptyState compact title="No runs yet" message="Run a scenario to see activity here." />
            ) : (
              <>
                <StackedBarChart buckets={buckets} series={presentRunSeries} height={170} />
                <Legend items={presentRunSeries.map((s) => ({ label: humanize(s), colour: SERIES_COLOURS[s] }))} />
              </>
            )}
          </Panel>
          <Panel title="Alert severity distribution">
            {!d ? <SkeletonRows rows={4} /> : d.alerts.length === 0 ? <EmptyState compact title="No alerts yet" /> : (
              <div className="row" style={{ gap: 20, flexWrap: 'nowrap', alignItems: 'center' }}>
                <Donut
                  sublabel="alerts"
                  segments={SEVERITIES.filter((s) => stats.bySeverity[s]).map((s) => ({ label: s, value: stats.bySeverity[s], colour: SERIES_COLOURS[s] }))}
                />
                <div className="stack-sm" style={{ flex: 1 }}>
                  {SEVERITIES.filter((s) => stats.bySeverity[s]).map((s) => (
                    <div key={s} className="row-between">
                      <SeverityBadge severity={s} />
                      <span className="strong">{stats.bySeverity[s]} <span className="dim small">{Math.round((stats.bySeverity[s] / d.alerts.length) * 100)}%</span></span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Panel>
        </div>

        <div className="grid grid-2">
          <Panel title="Recent incidents" icon={Siren} flush actions={<a className="btn btn-sm btn-ghost" href="#/incidents">View all</a>}>
            {!stats ? <SkeletonRows /> : stats.incidents.length === 0 ? <EmptyState compact title="No incidents" /> : (
              [...stats.incidents].sort((a, b) => time(b.created_at) - time(a.created_at)).slice(0, 6).map((i) => (
                <ListRow
                  key={i.id}
                  onClick={() => navigate('incidents', i.id)}
                  left={i.severity ? <SeverityBadge severity={i.severity} /> : <span className="badge tone-neutral">—</span>}
                  title={i.title}
                  sub={`${i.status} · risk ${i.risk_score ?? 'not scored'}`}
                  side={fmtAgo(i.created_at, now)}
                />
              ))
            )}
          </Panel>
          <Panel title="Latest experiment executions" icon={FlaskConical} flush actions={<a className="btn btn-sm btn-ghost" href="#/runs">View all</a>}>
            {!d ? <SkeletonRows /> : d.runs.length === 0 ? <EmptyState compact title="No runs yet" action={<a className="btn btn-sm" href="#/scenarios">Run a scenario</a>} /> : (
              d.runs.slice(0, 6).map((r) => (
                <ListRow
                  key={r.id}
                  onClick={() => navigate('runs', r.id)}
                  left={<StatusBadge tone={runStatusTone(r.status)}>{r.status}</StatusBadge>}
                  title={r.scenario_id}
                  sub={`${shortId(r.id)} · ${r.alerts.length} alert${r.alerts.length === 1 ? '' : 's'}`}
                  side={fmtAgo(r.started_at, now)}
                />
              ))
            )}
          </Panel>
          <Panel title="Recent alerts" icon={ShieldAlert} flush actions={<a className="btn btn-sm btn-ghost" href="#/alerts">View all</a>}>
            {!d ? <SkeletonRows /> : d.alerts.length === 0 ? <EmptyState compact title="No alerts" /> : (
              [...d.alerts].sort((a, b) => time(b.created_at) - time(a.created_at)).slice(0, 6).map((a) => (
                <ListRow
                  key={a.id}
                  onClick={() => navigate('alerts', a.id)}
                  left={<SeverityBadge severity={a.severity} />}
                  title={a.title}
                  sub={`${a.rule_id} · ${a.event_count} events`}
                  side={fmtAgo(a.created_at, now)}
                />
              ))
            )}
          </Panel>
          <Panel title="Recent defensive actions" icon={ShieldCheck} flush actions={<a className="btn btn-sm btn-ghost" href="#/responses">Response Center</a>}>
            {!d ? <SkeletonRows /> : d.responseAudit.length === 0 ? <EmptyState compact title="No response activity" /> : (
              d.responseAudit.slice(0, 6).map((row) => (
                <ListRow
                  key={row.id}
                  onClick={() => navigate('responses')}
                  left={<code>{row.action.replace('response.', '')}</code>}
                  title={auditDetail(row) || row.action}
                  sub={`response ${shortId(row.object_id)} · ${row.actor}`}
                  side={fmtAgo(row.timestamp, now)}
                />
              ))
            )}
          </Panel>
        </div>
      </div>
    </>
  )
}
