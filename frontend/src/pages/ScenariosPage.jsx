import { useMemo } from 'react'
import { Crosshair, FlaskConical, RefreshCw } from 'lucide-react'
import { api } from '../api.js'
import { usePlatform, useUI } from '../lib/context.js'
import { useApi } from '../lib/hooks.js'
import { useAction } from '../lib/useAction.js'
import { TERMINAL_RUN } from '../lib/data.js'
import { readiness as computeReadiness } from '../lib/scenario.js'
import { navigate } from '../lib/router.js'
import { Callout, PageHeader, Panel } from '../components/ui/Layout.jsx'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States.jsx'
import Drawer from '../components/ui/Drawer.jsx'
import ScenarioCard from '../components/domain/ScenarioCard.jsx'
import ScenarioDetailPanel from '../components/domain/ScenarioDetailPanel.jsx'
import RunExecutionPanel from '../components/domain/RunExecutionPanel.jsx'

// The scenario catalogue. Selecting a scenario opens its definition, the
// environment checks and the Run button; the run is followed until it ends.
export default function ScenariosPage({ selectedId }) {
  const platform = usePlatform()
  const { activeLab, labDetail, run, setRun, runActive, refreshLabDetail } = platform
  const { confirm } = useUI()
  const [busy, act] = useAction()
  const scenarios = useApi(() => api.listScenarios(), [])
  const definition = useApi(() => (selectedId ? api.getScenario(selectedId) : Promise.resolve(null)), [selectedId])

  const selected = scenarios.data?.find((s) => s.id === selectedId) || null
  const ready = useMemo(
    () => computeReadiness({ definition: definition.data, labDetail, activeLab, runActive }),
    [definition.data, labDetail, activeLab, runActive],
  )
  const baseAvailability = computeReadiness({ definition: null, labDetail, activeLab, runActive }).availability

  const external = activeLab?.environment === 'external'

  // Another run (not the one shown here) is still active in the lab.
  const activeRun = labDetail?.active_run ?? null
  const otherActive = activeRun && (!run || activeRun.id !== run.id) ? activeRun : null

  async function runScenario() {
    if (!activeLab || !selected) return
    const ok = await confirm({
      title: `Run “${selected.name}”?`,
      message: (
        <>
          The scenario runs against lab <b>{activeLab.name}</b>
          {selected.type === 'container' ? ' (the lab’s test container).' : ` (${labDetail?.target_url?.url ?? selected.target}).`}
          {selected.type === 'observation'
            ? ' It sends no attack traffic; it observes the lab for its full duration.'
            : ' It sends the bounded, predefined test activity described in the scenario file.'}
        </>
      ),
      confirmLabel: 'Run scenario',
    })
    if (!ok) return
    const outcome = await act('Run', () => api.startRun(activeLab.id, selected.id), {
      success: (started) => ['Run started', `${started.scenario_id} · ${started.id}`],
    })
    if (outcome.ok) {
      setRun(outcome.result)
      refreshLabDetail()
    }
  }

  async function cleanup() {
    const ok = await confirm({
      title: 'Clean up this run?',
      message: 'Cleanup runs the scenario’s cleanup steps (for example disabling the rate limit, resetting ShopCloud state or recreating the test container) and reverts responses they undo.',
      confirmLabel: 'Run cleanup',
      danger: true,
    })
    if (!ok) return
    const outcome = await act('Cleanup', () => api.cleanupRun(run.id), { success: (r) => ['Cleanup finished', `Run is now ${r.status}.`] })
    if (outcome.ok) {
      setRun(outcome.result)
      refreshLabDetail()
    }
  }

  const close = () => navigate('scenarios')
  const runPanel = run && (
    <RunExecutionPanel
      run={run}
      busy={busy}
      onCleanup={cleanup}
      onReport={() => navigate('reports', run.id)}
      cleanupBlockedBy={otherActive}
    />
  )

  return (
    <>
      <PageHeader
        title="Attack Scenarios"
        subtitle="Predefined, bounded test activity against the lab. Each scenario states what telemetry, detection and response it expects."
        actions={<button className="btn" onClick={() => { scenarios.reload(); refreshLabDetail() }}><RefreshCw size={14} /> Refresh</button>}
      />

      <div className="grid grid-main-side">
        <div className="stack">
          {external && (
            <Callout tone="info" title={`Target: ${activeLab.target} (registered, runs outside CloudAttackLab)`}>
              Runs go to {labDetail?.target_url?.url ?? 'its registered URL'} and need an active authorization that names the scenario.
              The backend re-checks the URL and the handshake before every run. Manage it in <a href="#/targets">Target Management</a>.
            </Callout>
          )}
          {(!activeLab || (labDetail?.status ?? activeLab.status) !== 'running') && (
            external ? (
              <Callout tone="info" title="The target is not verified">
                Verify it in <a href="#/targets">Target Management</a> before running a scenario.
              </Callout>
            ) : (
              <Callout tone="info" title="The active lab is not running">
                Start it on the <a href="#/labs">Labs & Environments</a> page before running a scenario.
              </Callout>
            )
          )}
          <Panel title="Scenario catalogue" icon={Crosshair} meta={scenarios.data ? `${scenarios.data.length}` : undefined}>
            {scenarios.loading && <LoadingState compact />}
            {scenarios.error && !scenarios.data && <ErrorState compact message={scenarios.error} onRetry={scenarios.reload} />}
            {scenarios.data?.length === 0 && <EmptyState compact title="No scenarios" message="The backend found no valid scenario files." />}
            {scenarios.data?.length > 0 && (
              <div className="grid grid-2">
                {scenarios.data.map((s) => (
                  <ScenarioCard
                    key={s.id}
                    scenario={s}
                    selected={s.id === selectedId}
                    availability={baseAvailability}
                    onOpen={() => navigate('scenarios', s.id)}
                  />
                ))}
              </div>
            )}
          </Panel>
        </div>

        <Panel title="Current execution" icon={FlaskConical} meta={run ? (TERMINAL_RUN.includes(run.status) ? 'finished' : 'live') : undefined}>
          {run ? runPanel : (
            <EmptyState compact title="No run started in this session" message="Open a scenario and run it. Past runs are on the Experiment Runs page." action={<a className="btn btn-sm" href="#/runs">View experiment runs</a>} />
          )}
          {otherActive && !run && (
            <div className="mt-8"><Callout tone="info">Run {otherActive.scenario_id} ({otherActive.status}) is active in this lab.</Callout></div>
          )}
        </Panel>
      </div>

      {selectedId && (
        <Drawer kicker="Attack scenario" title={selected?.name ?? selectedId} onClose={close} wide>
          {!selected && scenarios.loading && <LoadingState />}
          {!selected && !scenarios.loading && <ErrorState title="Scenario not found" message={`No scenario with id ${selectedId}.`} />}
          {selected && (
            <ScenarioDetailPanel
              summary={selected}
              definition={definition.data}
              definitionError={definition.error}
              onRetry={definition.reload}
              readiness={ready}
              busy={busy}
              onRun={runScenario}
            >
              {run && run.scenario_id === selected.id && (
                <Panel title="Current execution" icon={FlaskConical}>{runPanel}</Panel>
              )}
            </ScenarioDetailPanel>
          )}
        </Drawer>
      )}
    </>
  )
}
