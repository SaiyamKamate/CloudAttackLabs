import { useMemo, useState } from 'react'
import { FlaskConical, RefreshCw, Search } from 'lucide-react'
import { api } from '../api.js'
import { usePlatform, useUI } from '../lib/context.js'
import { useApi, useInterval } from '../lib/hooks.js'
import { useAction } from '../lib/useAction.js'
import { TERMINAL_RUN, cleanupOutcome, detectionOutcome, loadRuns } from '../lib/data.js'
import { runStatusTone } from '../lib/describe.js'
import { fmt, fmtDuration, secondsBetween, shortId } from '../lib/format.js'
import { navigate } from '../lib/router.js'
import { PageHeader, Panel } from '../components/ui/Layout.jsx'
import { StatusBadge } from '../components/ui/Badges.jsx'
import { EmptyState, ErrorState } from '../components/ui/States.jsx'
import DataTable from '../components/ui/DataTable.jsx'
import RunExecutionPanel from '../components/domain/RunExecutionPanel.jsx'
import ReportPage from './ReportPage.jsx'

export default function RunsPage({ selectedId }) {
  return selectedId ? <RunDetail runId={selectedId} /> : <RunList />
}

// Every run the backend has a record of. There is no list endpoint for runs;
// the list is rebuilt from the audit log's run.start rows (see lib/data.js).
function RunList() {
  const runs = useApi(() => Promise.all([loadRuns(), api.listScenarios()]), [])
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [scenario, setScenario] = useState('')

  const [all, scenarios] = runs.data ?? [[], []]
  const observation = useMemo(() => new Set(scenarios.filter((s) => s.type === 'observation').map((s) => s.id)), [scenarios])
  const scenarioIds = useMemo(() => [...new Set(all.map((r) => r.scenario_id))].sort(), [all])
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return all.filter((r) =>
      (!status || r.status === status) &&
      (!scenario || r.scenario_id === scenario) &&
      (!term || [r.id, r.scenario_id, r.error].some((v) => v && String(v).toLowerCase().includes(term))),
    )
  }, [all, search, status, scenario])

  return (
    <>
      <PageHeader
        title="Experiment Runs"
        subtitle="History of scenario executions with their detection and cleanup outcomes. Open a run for its full report."
        actions={<button className="btn" onClick={runs.reload} disabled={runs.refreshing}><RefreshCw size={14} className={runs.refreshing ? 'spin' : ''} /> Refresh</button>}
      />
      <Panel flush>
        <div className="filters">
          <div className="search">
            <Search size={14} />
            <input className="input" placeholder="Search run ID, scenario, error…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search runs" />
          </div>
          <select className="select" value={scenario} onChange={(e) => setScenario(e.target.value)} aria-label="Scenario">
            <option value="">All scenarios</option>
            {scenarioIds.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="select" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">All statuses</option>
            {['pending', 'running', ...TERMINAL_RUN].map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
          </select>
          <span className="filter-count">{rows.length} of {all.length}</span>
        </div>
        {runs.error && !runs.data ? <ErrorState message={runs.error} onRetry={runs.reload} /> : (
          <DataTable
            loading={runs.loading}
            rows={rows}
            rowKey={(r) => r.id}
            onRowClick={(r) => navigate('runs', r.id)}
            initialSort={{ key: 'started', dir: 'desc' }}
            empty={<EmptyState compact icon={FlaskConical} title={all.length ? 'No runs match the filters' : 'No runs yet'} message={all.length ? undefined : 'Run a scenario from the Attack Scenarios page.'} />}
            footer="Detection outcome counts the alerts linked to the run. For an observation run every alert is a false positive."
            columns={[
              { key: 'id', header: 'Run ID', render: (r) => <span className="mono small">{shortId(r.id)}</span> },
              { key: 'scenario', header: 'Scenario', render: (r) => <span className="cell-title">{r.scenario_id}</span>, sortValue: (r) => r.scenario_id },
              { key: 'started', header: 'Start time', render: (r) => <span className="nowrap">{fmt(r.started_at)}</span>, sortValue: (r) => r.started_at },
              { key: 'duration', header: 'Duration', num: true, render: (r) => fmtDuration(secondsBetween(r.started_at, r.finished_at)), sortValue: (r) => secondsBetween(r.started_at, r.finished_at) },
              { key: 'status', header: 'Status', render: (r) => <StatusBadge tone={runStatusTone(r.status)}>{r.status}</StatusBadge>, sortValue: (r) => r.status },
              {
                key: 'detection',
                header: 'Detection outcome',
                render: (r) => { const d = detectionOutcome(r, observation.has(r.scenario_id)); return <StatusBadge tone={d.tone}>{d.label}</StatusBadge> },
                sortValue: (r) => r.alerts.length,
              },
              { key: 'cleanup', header: 'Cleanup', render: (r) => { const c = cleanupOutcome(r); return <StatusBadge tone={c.tone}>{c.label}</StatusBadge> } },
            ]}
          />
        )}
      </Panel>
    </>
  )
}

// One run: its live state and actions, then the full report.
function RunDetail({ runId }) {
  const { confirm } = useUI()
  const { labDetail, setRun: setPlatformRun, run: platformRun, refreshLabDetail } = usePlatform()
  const [busy, act] = useAction()
  const run = useApi(() => api.getRun(runId), [runId])
  const active = run.data && !TERMINAL_RUN.includes(run.data.status)
  useInterval(run.reload, 2000, Boolean(active))

  const activeRun = labDetail?.active_run ?? null
  const otherActive = activeRun && activeRun.id !== runId ? activeRun : null

  async function cleanup() {
    const ok = await confirm({
      title: 'Clean up this run?',
      message: 'Cleanup runs the scenario’s cleanup steps (for example disabling the rate limit, resetting ShopCloud state or recreating the test container) and reverts responses they undo.',
      confirmLabel: 'Run cleanup',
      danger: true,
    })
    if (!ok) return
    const outcome = await act('Cleanup', () => api.cleanupRun(runId), { success: (r) => ['Cleanup finished', `Run is now ${r.status}.`] })
    if (outcome.ok) {
      run.setData(outcome.result)
      if (platformRun?.id === runId) setPlatformRun(outcome.result)
      refreshLabDetail()
    }
  }

  return (
    <ReportPage key={run.data?.status} runId={runId} onClose={() => navigate('runs')}>
      {run.data && (
        <Panel title="Execution" icon={FlaskConical}>
          <RunExecutionPanel run={run.data} busy={busy} onCleanup={cleanup} cleanupBlockedBy={otherActive} />
        </Panel>
      )}
    </ReportPage>
  )
}
