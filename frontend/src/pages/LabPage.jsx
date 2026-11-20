import { useState } from 'react'
import { Boxes, ExternalLink, Play, Plus, RefreshCw, RotateCcw, Server, Square, Target } from 'lucide-react'
import { api } from '../api.js'
import { usePlatform, useUI } from '../lib/context.js'
import { useAction } from '../lib/useAction.js'
import { containerTone, isUnusedFalco, labStatusTone } from '../lib/describe.js'
import { fmt, humanize, shortId } from '../lib/format.js'
import { navigate } from '../lib/router.js'
import { Callout, KeyValue, PageHeader, Panel } from '../components/ui/Layout.jsx'
import { StatusBadge, StatusIndicator } from '../components/ui/Badges.jsx'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States.jsx'
import DataTable from '../components/ui/DataTable.jsx'
import HealthStatusPanel from '../components/domain/HealthStatusPanel.jsx'

const BUSY_TEXT = {
  Start: 'Starting the lab. The backend answers once every lab container is healthy; this can take a minute.',
  Stop: 'Stopping the lab containers…',
  Reset: 'Resetting: recreating every lab container, resetting ShopCloud and reverting applied responses…',
  'Create lab': 'Creating the lab record…',
}

// The selected lab's status and health, with Start / Stop / Reset.
export default function LabPage() {
  const { labs, labsError, refreshLabs, activeLab, selectLab, labDetail, labDetailError, refreshLabDetail, backend } = usePlatform()
  const { confirm } = useUI()
  const [busy, run] = useAction()
  const [lastOp, setLastOp] = useState(null) // { action, at, ok, status, error }
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  async function operate(label, call) {
    const outcome = await run(label, call, { success: (lab) => [`${label} succeeded`, `Lab ${lab.name} is now ${lab.status}.`] })
    setLastOp({ action: label, at: new Date().toISOString(), ok: outcome.ok, status: outcome.result?.status, error: outcome.error?.message, code: outcome.error?.code })
    await refreshLabs()
    await refreshLabDetail()
  }

  async function refresh() {
    setRefreshing(true)
    await refreshLabs()
    await refreshLabDetail()
    setRefreshing(false)
  }

  async function createLab(event) {
    event.preventDefault()
    const name = newName.trim() || 'default'
    const outcome = await run('Create lab', () => api.createLab(name), { success: (lab) => [`Lab ${lab.name} created`, 'Start it to bring up ShopCloud.'] })
    if (outcome.ok) {
      selectLab(outcome.result.id)
      setNewName('')
      setCreating(false)
      await refreshLabs()
    }
  }

  async function stop() {
    const ok = await confirm({
      title: `Stop lab “${activeLab.name}”?`,
      message: 'The lab containers (ShopCloud and the test container) are stopped. Runs cannot start until the lab is started again.',
      confirmLabel: 'Stop lab',
      danger: true,
    })
    if (ok) operate('Stop', () => api.stopLab(activeLab.id))
  }

  async function reset() {
    const ok = await confirm({
      title: `Reset lab “${activeLab.name}”?`,
      message: 'Every lab container is recreated, ShopCloud’s state is reset through /lab/reset, and applied response actions are reverted. Incidents and reports are kept.',
      confirmLabel: 'Reset lab',
      danger: true,
    })
    if (ok) operate('Reset', () => api.resetLab(activeLab.id))
  }

  const createForm = (
    <form onSubmit={createLab} className="row">
      <input className="input" placeholder="Lab name (default)" value={newName} onChange={(e) => setNewName(e.target.value)} maxLength={100} aria-label="Lab name" />
      <button className="btn btn-primary" type="submit" disabled={Boolean(busy)}><Plus size={14} /> Create lab</button>
      {labs?.length > 0 && <button type="button" className="btn btn-ghost" onClick={() => setCreating(false)}>Cancel</button>}
    </form>
  )

  if (!labs && !labsError) return <><PageHeader title="Labs & Environments" /><Panel><LoadingState /></Panel></>
  if (!labs && labsError) {
    return <><PageHeader title="Labs & Environments" /><Panel><ErrorState message={labsError} onRetry={refresh} /></Panel></>
  }
  if (labs.length === 0) {
    return (
      <>
        <PageHeader title="Labs & Environments" subtitle="A lab is the isolated Docker environment (ShopCloud and a test container) that scenarios run against." />
        <Panel>
          <EmptyState icon={Server} title="No lab yet" message="Create a lab, then start it. Starting brings up ShopCloud and the lab's test container on 127.0.0.1." action={createForm} />
        </Panel>
      </>
    )
  }

  const detail = labDetail
  const status = detail?.status ?? activeLab.status
  const activeRun = detail?.active_run ?? null
  const targetAllowed = detail?.target_url?.allowed
  const containers = detail?.containers ?? []
  // A registered target's lab: the application runs outside CloudAttackLab, so
  // there is nothing to start, stop or reset here, and no containers.
  const external = activeLab.environment === 'external'

  return (
    <>
      <PageHeader
        title="Labs & Environments"
        subtitle="Lifecycle and health of the selected lab environment. Every value is read live from the backend."
        actions={
          <>
            <button className="btn" onClick={refresh} disabled={refreshing || Boolean(busy)}>
              <RefreshCw size={14} className={refreshing ? 'spin' : ''} /> Refresh status
            </button>
            <button className="btn" onClick={() => setCreating((c) => !c)}><Plus size={14} /> New lab</button>
          </>
        }
      />

      <div className="stack">
        {creating && <Panel title="Create a lab">{createForm}</Panel>}

        {external && (
          <Callout tone="info" title="Registered target" action={<button className="btn btn-sm" onClick={() => navigate('targets')}><Target size={13} /> Target Management</button>}>
            This lab belongs to an application that runs on its own, outside CloudAttackLab. It is never started, stopped or reset from here.
            Verify it and authorize experiments in Target Management.
          </Callout>
        )}
        {!external && detail?.target_url && !targetAllowed && (
          <Callout tone="crit" title="Target URL refused">
            {detail.target_url.error} Runs, start and reset are refused until SHOPCLOUD_URL in .env points at the lab
            (for example http://127.0.0.1:8081) and the backend is restarted.
          </Callout>
        )}
        {labDetailError && <Callout tone="crit" title="Could not read the lab">{labDetailError}</Callout>}

        <Panel
          title={activeLab.name}
          icon={Server}
          meta={<StatusBadge tone={labStatusTone(status)}>{status}</StatusBadge>}
          actions={external ? (
            targetAllowed && detail?.target_url?.url
              ? <a className="btn" href={detail.target_url.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Open ShopCloud</a>
              : null
          ) : (
            <>
              <button className="btn btn-primary" onClick={() => operate('Start', () => api.startLab(activeLab.id))} disabled={Boolean(busy)}>
                <Play size={14} /> Start
              </button>
              <button className="btn btn-danger" onClick={stop} disabled={Boolean(busy)}><Square size={13} /> Stop</button>
              <button
                className="btn btn-danger"
                onClick={reset}
                disabled={Boolean(busy) || Boolean(activeRun)}
                title={activeRun ? 'Disabled while a run is in progress' : undefined}
              >
                <RotateCcw size={14} /> Reset
              </button>
              {targetAllowed && detail?.target_url?.url && ['running', 'degraded'].includes(status) && (
                <a className="btn" href={detail.target_url.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Open ShopCloud</a>
              )}
            </>
          )}
        >
          {busy && (
            <div className="stack-sm" style={{ marginBottom: 14 }}>
              <div className="progress" />
              <div className="small muted">{BUSY_TEXT[busy] || `${busy}…`}</div>
            </div>
          )}
          {activeRun && (
            <div style={{ marginBottom: 14 }}>
              <Callout tone="info" title={`Run in progress: ${activeRun.scenario_id} (${activeRun.status})`}>
                Reset is disabled while this run is in progress: a reset recreates ShopCloud and would break that run. Refresh once it has finished.
              </Callout>
            </div>
          )}
          {detail?.status_detail && (
            <div style={{ marginBottom: 14 }}>
              <Callout tone={['degraded', 'stopped', 'error'].includes(status) ? 'warn' : 'info'}>{detail.status_detail}</Callout>
            </div>
          )}
          <div className="grid grid-2">
            <KeyValue rows={[
              ['Environment', activeLab.environment],
              ['Target', activeLab.target],
              ['Lab ID', <code key="id">{activeLab.id}</code>],
            ]} />
            <KeyValue rows={[
              ['Created', fmt(activeLab.created_at)],
              ['Last updated', fmt(detail?.updated_at ?? activeLab.updated_at)],
              ['ShopCloud URL', detail?.target_url ? <span key="u" className="mono">{detail.target_url.url}</span> : '—'],
            ]} />
          </div>
        </Panel>

        {lastOp && (
          <Callout tone={lastOp.ok ? 'ok' : 'crit'} title={`Last operation: ${lastOp.action} ${lastOp.ok ? 'succeeded' : 'failed'} at ${fmt(lastOp.at)}`}>
            {lastOp.ok ? `The backend returned the lab with status “${lastOp.status}”.` : `${lastOp.error}${lastOp.code ? ` (${lastOp.code})` : ''}`}
          </Callout>
        )}

        <Panel title="Health" flush meta={detail ? undefined : 'loading…'}>
          <HealthStatusPanel lab={activeLab} detail={detail} backend={backend} />
        </Panel>

        {!external && <Panel title="Containers" icon={Boxes} flush meta={`${containers.length}`}>
          <DataTable
            rowKey={(c) => c.service}
            rows={containers}
            loading={!detail && !labDetailError}
            empty={<EmptyState compact title="No containers" message="Start the lab to create its containers." />}
            columns={[
              { key: 'service', header: 'Service', render: (c) => <span className="cell-title">{c.service}</span>, sortValue: (c) => c.service },
              { key: 'name', header: 'Container', render: (c) => <span className="mono small">{c.name ?? '—'}</span> },
              { key: 'container_id', header: 'ID', render: (c) => <span className="mono small">{c.container_id ? String(c.container_id).slice(0, 12) : '—'}</span> },
              { key: 'state', header: 'State', render: (c) => humanize(c.state), sortValue: (c) => c.state },
              {
                key: 'health',
                header: 'Health',
                render: (c) => isUnusedFalco(c, detail?.falco)
                  ? <StatusIndicator tone="neutral" label="not used (FALCO_ENABLED=false)" />
                  : <StatusIndicator tone={containerTone(c)} label={c.health ?? 'no health check'} />,
              },
            ]}
          />
        </Panel>}

        <Panel title="All labs" flush meta={`${labs.length}`}>
          <DataTable
            rowKey={(l) => l.id}
            rows={labs}
            selectedKey={activeLab.id}
            onRowClick={(l) => selectLab(l.id)}
            columns={[
              { key: 'name', header: 'Name', render: (l) => <span className="cell-title">{l.name}</span>, sortValue: (l) => l.name },
              { key: 'status', header: 'Stored status', render: (l) => <StatusIndicator tone={labStatusTone(l.status)} label={humanize(l.status)} /> },
              { key: 'environment', header: 'Environment' },
              { key: 'target', header: 'Target' },
              { key: 'id', header: 'ID', render: (l) => <span className="mono small">{shortId(l.id)}</span> },
              { key: 'created_at', header: 'Created', render: (l) => fmt(l.created_at), sortValue: (l) => l.created_at },
            ]}
            footer="Select a lab to make it the active lab. A lab's status is checked against Docker when it is selected."
          />
        </Panel>
      </div>
    </>
  )
}
