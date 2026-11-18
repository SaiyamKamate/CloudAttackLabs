import { useMemo } from 'react'
import { BarChart3, FileText, FlaskConical, RefreshCw } from 'lucide-react'
import { api } from '../api.js'
import { useApi } from '../lib/hooks.js'
import { TERMINAL_RUN, cleanupOutcome, detectionOutcome, groupBy, loadRuns } from '../lib/data.js'
import { runStatusTone } from '../lib/describe.js'
import { fmt, fmtDuration, secondsBetween, shortId } from '../lib/format.js'
import { navigate } from '../lib/router.js'
import { Callout, MetricCard, PageHeader, Panel } from '../components/ui/Layout.jsx'
import { StatusBadge } from '../components/ui/Badges.jsx'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States.jsx'
import { HBarList, Legend } from '../components/ui/Charts.jsx'
import { SERIES_COLOURS } from '../lib/colours.js'
import DataTable from '../components/ui/DataTable.jsx'
import ReportPage from './ReportPage.jsx'

export default function ReportsPage({ selectedId }) {
  if (selectedId) return <ReportPage runId={selectedId} onClose={() => navigate('reports')} />
  return <ReportsOverview />
}

const OUTCOMES = ['completed', 'cleaned_up', 'incomplete', 'failed']

function mean(values) {
  const known = values.filter((v) => v !== null && v !== undefined)
  return known.length ? known.reduce((a, b) => a + b, 0) / known.length : null
}

// Analytics over every recorded run. All numbers are counted from runs,
// alerts and cleanup records the backend returned.
function ReportsOverview() {
  const data = useApi(() => Promise.all([loadRuns(), api.listScenarios()]), [])
  const [runs, scenarios] = data.data ?? [[], []]

  const perScenario = useMemo(() => {
    const byScenario = groupBy(runs, (r) => r.scenario_id)
    const types = Object.fromEntries(scenarios.map((s) => [s.id, s]))
    return Object.entries(byScenario).map(([id, list]) => {
      const finished = list.filter((r) => TERMINAL_RUN.includes(r.status))
      const observation = types[id]?.type === 'observation'
      const withAlerts = finished.filter((r) => r.alerts.length > 0).length
      const verifications = finished.map((r) => r.summary?.verification).filter(Boolean)
      return {
        id,
        name: types[id]?.name ?? id,
        observation,
        total: list.length,
        statuses: Object.fromEntries(OUTCOMES.map((s) => [s, list.filter((r) => r.status === s).length])),
        finished: finished.length,
        withAlerts,
        alerts: finished.reduce((sum, r) => sum + r.alerts.length, 0),
        verificationPassed: verifications.filter((v) => v.passed).length,
        verificationTotal: verifications.length,
        cleaned: list.filter((r) => cleanupOutcome(r).label === 'cleaned up').length,
        meanDuration: mean(finished.map((r) => secondsBetween(r.started_at, r.finished_at))),
      }
    }).sort((a, b) => b.total - a.total)
  }, [runs, scenarios])

  const observationIds = new Set(scenarios.filter((s) => s.type === 'observation').map((s) => s.id))
  const attackRuns = runs.filter((r) => !observationIds.has(r.scenario_id) && TERMINAL_RUN.includes(r.status))
  const detected = attackRuns.filter((r) => r.alerts.length > 0).length
  const observationRuns = runs.filter((r) => observationIds.has(r.scenario_id) && TERMINAL_RUN.includes(r.status))
  const falsePositives = observationRuns.reduce((sum, r) => sum + r.alerts.length, 0)

  if (data.loading) return <><PageHeader title="Reports & Analytics" /><LoadingState label="Loading run history…" /></>
  if (!data.data) return <><PageHeader title="Reports & Analytics" /><Panel><ErrorState message={data.error} onRetry={data.reload} /></Panel></>

  return (
    <>
      <PageHeader
        title="Reports & Analytics"
        subtitle="Outcomes across every recorded experiment run, and the full report for each run."
        actions={<button className="btn" onClick={data.reload} disabled={data.refreshing}><RefreshCw size={14} className={data.refreshing ? 'spin' : ''} /> Refresh</button>}
      />
      <div className="stack">
        <div className="grid grid-4">
          <MetricCard label="Recorded runs" icon={FlaskConical} value={runs.length} sub={`${perScenario.length} scenario${perScenario.length === 1 ? '' : 's'}`} />
          <MetricCard
            label="Attack runs with an alert"
            value={attackRuns.length ? `${detected} / ${attackRuns.length}` : '—'}
            sub={attackRuns.length ? `${Math.round((detected / attackRuns.length) * 100)}% of finished attack runs` : 'no finished attack runs'}
            accent="ok"
          />
          <MetricCard
            label="Observation-run alerts"
            value={observationRuns.length ? falsePositives : '—'}
            sub={observationRuns.length ? `false positives in ${observationRuns.length} baseline run${observationRuns.length === 1 ? '' : 's'}` : 'no finished baseline runs'}
            accent={falsePositives ? 'high' : undefined}
          />
          <MetricCard label="Runs cleaned up" value={runs.filter((r) => cleanupOutcome(r).label === 'cleaned up').length} sub="successful cleanup recorded" />
        </div>

        <div className="grid grid-2">
          <Panel title="Run outcomes by scenario" icon={BarChart3}>
            {perScenario.length === 0 ? <EmptyState compact title="No runs yet" /> : (
              <div className="stack-sm">
                {perScenario.map((s) => (
                  <div key={s.id}>
                    <div className="row-between small" style={{ marginBottom: 4 }}>
                      <span className="strong" style={{ fontWeight: 500 }}>{s.name}</span>
                      <span className="dim">{s.total} run{s.total === 1 ? '' : 's'}</span>
                    </div>
                    <div style={{ display: 'flex', height: 10, borderRadius: 2, overflow: 'hidden', background: 'var(--surface-sunken)' }}>
                      {OUTCOMES.map((o) => s.statuses[o] ? (
                        <span key={o} title={`${o.replace('_', ' ')}: ${s.statuses[o]}`} style={{ width: `${(s.statuses[o] / s.total) * 100}%`, background: SERIES_COLOURS[o] }} />
                      ) : null)}
                    </div>
                  </div>
                ))}
                <Legend items={OUTCOMES.map((o) => ({ label: o.replace('_', ' '), colour: SERIES_COLOURS[o] }))} />
              </div>
            )}
          </Panel>
          <Panel title="Alerts raised per scenario">
            {perScenario.length === 0 ? <EmptyState compact title="No runs yet" /> : (
              <HBarList items={perScenario.map((s) => ({ label: s.id, value: s.alerts, colour: s.observation ? 'var(--high)' : 'var(--accent)' }))} />
            )}
            <div className="small dim mt-8">Orange: observation scenario, where every alert is a false positive.</div>
          </Panel>
        </div>

        <Panel title="Scenario results" flush>
          <DataTable
            rows={perScenario}
            rowKey={(s) => s.id}
            columns={[
              { key: 'name', header: 'Scenario', render: (s) => <><div className="cell-title">{s.name}</div><div className="cell-sub mono">{s.id}</div></> },
              { key: 'total', header: 'Runs', num: true },
              { key: 'completed', header: 'Completed', num: true, render: (s) => s.statuses.completed },
              { key: 'incomplete', header: 'Incomplete', num: true, render: (s) => s.statuses.incomplete },
              { key: 'failed', header: 'Failed', num: true, render: (s) => s.statuses.failed },
              {
                key: 'detection',
                header: 'Detection',
                render: (s) => s.observation
                  ? <span>{s.alerts} false positive{s.alerts === 1 ? '' : 's'}</span>
                  : <span>{s.withAlerts} / {s.finished} runs alerted</span>,
              },
              { key: 'verification', header: 'Post-run verification', render: (s) => (s.verificationTotal ? `${s.verificationPassed} / ${s.verificationTotal} passed` : <span className="dim">not defined</span>) },
              { key: 'cleaned', header: 'Cleaned up', num: true },
              { key: 'duration', header: 'Mean duration', num: true, render: (s) => fmtDuration(s.meanDuration) },
            ]}
            footer="Cleanup replaces a run’s status with cleaned_up, so cleaned-up runs are not counted under Completed, Incomplete or Failed."
          />
        </Panel>

        <Panel title="Experiment history" icon={FileText} flush meta={`${runs.length}`}>
          <DataTable
            rows={runs}
            rowKey={(r) => r.id}
            onRowClick={(r) => navigate('reports', r.id)}
            initialSort={{ key: 'started', dir: 'desc' }}
            pageSize={25}
            columns={[
              { key: 'id', header: 'Run', render: (r) => <span className="mono small">{shortId(r.id)}</span> },
              { key: 'scenario', header: 'Scenario', render: (r) => r.scenario_id, sortValue: (r) => r.scenario_id },
              { key: 'started', header: 'Started', render: (r) => <span className="nowrap">{fmt(r.started_at)}</span>, sortValue: (r) => r.started_at },
              { key: 'status', header: 'Status', render: (r) => <StatusBadge tone={runStatusTone(r.status)}>{r.status}</StatusBadge> },
              { key: 'detection', header: 'Detection', render: (r) => { const d = detectionOutcome(r, observationIds.has(r.scenario_id)); return <StatusBadge tone={d.tone}>{d.label}</StatusBadge> } },
              { key: 'verification', header: 'Verification', render: (r) => r.summary?.verification ? <StatusBadge tone={r.summary.verification.passed ? 'ok' : 'crit'}>{r.summary.verification.passed ? 'passed' : 'failed'}</StatusBadge> : <span className="dim">—</span> },
              { key: 'cleanup', header: 'Cleanup', render: (r) => { const c = cleanupOutcome(r); return <StatusBadge tone={c.tone}>{c.label}</StatusBadge> } },
              { key: 'open', header: '', render: () => <span className="small">Open report →</span> },
            ]}
          />
        </Panel>

        <Callout tone="info" title="Export formats">
          Each run report can be downloaded as JSON (the exact API response) from its report page. PDF and CSV export are not implemented: the backend only produces JSON reports.
        </Callout>
      </div>
    </>
  )
}
