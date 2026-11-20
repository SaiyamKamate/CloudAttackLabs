import { useState } from 'react'
import { CheckCircle2, Circle, ExternalLink, Plus, RefreshCw, ShieldCheck, ShieldOff, Target, XCircle } from 'lucide-react'
import { api } from '../api.js'
import { usePlatform, useUI } from '../lib/context.js'
import { useApi, useInterval, useNow } from '../lib/hooks.js'
import { useAction } from '../lib/useAction.js'
import { navigate } from '../lib/router.js'
import { fmt, fmtAgo, humanize } from '../lib/format.js'
import { Callout, KeyValue, PageHeader, Panel } from '../components/ui/Layout.jsx'
import { StatusBadge } from '../components/ui/Badges.jsx'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States.jsx'
import DataTable from '../components/ui/DataTable.jsx'

const EMPTY_FORM = { name: 'shopcloud-demo', base_url: 'http://127.0.0.1:8090', integration_key: '', control_token: '' }

// Applications registered by URL that run outside CloudAttackLab (V1: ShopCloud).
// Registering, verifying and authorizing are separate steps; only an active
// authorization lets a scenario send traffic to the target.
export default function TargetsPage() {
  const { refreshLabs, refreshLabDetail, selectLab } = usePlatform()
  const targets = useApi(() => api.listTargets(), [])
  const scenarios = useApi(() => api.listScenarios(), [])
  const [busy, run] = useAction()
  const [registering, setRegistering] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)

  // Authorizations expire on their own: keep the list current.
  useInterval(() => targets.reload(), 15000, Boolean(targets.data?.length))

  async function after() {
    targets.reload()
    await refreshLabs()
    await refreshLabDetail()
  }

  async function register(event) {
    event.preventDefault()
    const outcome = await run('Register target', () => api.registerTarget({ ...form, kind: 'shopcloud' }), {
      success: (t) => [`${t.name} registered`, 'Nothing was sent to it. Verify the connection next.'],
    })
    if (outcome.ok) {
      setForm(EMPTY_FORM)
      setRegistering(false)
      await after()
    }
  }

  const applicationScenarios = (scenarios.data ?? []).filter((s) => s.type === 'application' && s.target === 'shopcloud')
  const list = targets.data ?? []
  const showForm = registering || (targets.data && list.length === 0)

  return (
    <>
      <PageHeader
        title="Target Management"
        subtitle="Applications that run on their own, outside CloudAttackLab, and are connected by URL. V1 supports ShopCloud on approved local lab addresses only."
        actions={
          <>
            <button className="btn" onClick={() => targets.reload()} disabled={targets.refreshing}>
              <RefreshCw size={14} className={targets.refreshing ? 'spin' : ''} /> Refresh
            </button>
            <button className="btn btn-primary" onClick={() => setRegistering((r) => !r)}><Plus size={14} /> Register target</button>
          </>
        }
      />
      <div className="stack">
        {showForm && (
          <Panel title="Register a target" icon={Target}>
            <form onSubmit={register} className="stack-sm" autoComplete="off">
              <div className="grid grid-2">
                <Field label="Name" hint="Lower-case letters, digits and dashes.">
                  <input className="input" required pattern="[a-z0-9][a-z0-9\-]{1,39}" value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </Field>
                <Field label="Application URL" hint="An IP address or localhost with an approved port, for example http://127.0.0.1:8090.">
                  <input className="input mono" required value={form.base_url}
                    onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
                </Field>
                <Field label="Integration key" hint="ShopCloud's INTEGRATION_KEY (at least 32 characters). Never shown again.">
                  <input className="input mono" type="password" required minLength={32} value={form.integration_key}
                    onChange={(e) => setForm({ ...form, integration_key: e.target.value })} />
                </Field>
                <Field label="Control token" hint="ShopCloud's LAB_CONTROL_TOKEN, for responses and cleanup. Never shown again.">
                  <input className="input mono" type="password" required minLength={8} value={form.control_token}
                    onChange={(e) => setForm({ ...form, control_token: e.target.value })} />
                </Field>
              </div>
              <div className="row">
                <button className="btn btn-primary" type="submit" disabled={Boolean(busy)}><Plus size={14} /> Register</button>
                <span className="small muted">Registering only checks the URL against the lab policy. Nothing is sent to the target.</span>
              </div>
            </form>
          </Panel>
        )}

        {targets.loading && <Panel><LoadingState /></Panel>}
        {targets.error && !targets.data && <Panel><ErrorState message={targets.error} onRetry={targets.reload} /></Panel>}
        {targets.data && list.length === 0 && !showForm && (
          <Panel><EmptyState icon={Target} title="No registered targets" message="Register ShopCloud by its URL to begin." /></Panel>
        )}

        {list.map((t) => (
          <TargetCard
            key={t.id}
            summary={t}
            scenarios={applicationScenarios}
            busy={busy}
            run={run}
            onChanged={after}
            onUse={() => { selectLab(t.lab_id); navigate('scenarios') }}
          />
        ))}
      </div>
    </>
  )
}

function Field({ label, hint, children }) {
  return (
    <label className="stack-sm" style={{ gap: 4 }}>
      <span className="small" style={{ fontWeight: 600 }}>{label}</span>
      {children}
      {hint && <span className="small muted">{hint}</span>}
    </label>
  )
}

function Step({ state, title, detail, action }) {
  const Icon = state === 'ok' ? CheckCircle2 : state === 'fail' ? XCircle : Circle
  const colour = state === 'ok' ? 'var(--ok, #16a34a)' : state === 'fail' ? 'var(--critical)' : 'var(--text-dim)'
  return (
    <li className="row-between" style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
      <div className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap', minWidth: 0 }}>
        <Icon size={16} style={{ color: colour, flexShrink: 0, marginTop: 1 }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>{title}</div>
          <div className="small muted" style={{ overflowWrap: 'anywhere' }}>{detail}</div>
        </div>
      </div>
      {action}
    </li>
  )
}

function TargetCard({ summary, scenarios, busy, run, onChanged, onUse }) {
  const { confirm } = useUI()
  const now = useNow(15000)
  const detail = useApi(() => api.getTarget(summary.id), [summary.id, summary.status, summary.telemetry_verified_at, summary.active_authorization?.id, summary.active_authorization?.runs_used])
  const [authorizing, setAuthorizing] = useState(false)
  const [auth, setAuth] = useState({ scenario_ids: ['authentication_abuse'], duration_minutes: 30, max_runs: 3, authorized_by: '', acknowledgement: false })

  const t = detail.data ?? summary
  const verified = t.status === 'verified'
  const telemetryOk = verified && Boolean(t.telemetry_verified_at)
  const active = t.active_authorization

  async function act(label, call, success) {
    const outcome = await run(label, call, { success })
    await onChanged()
    detail.reload()
    return outcome
  }

  async function authorize(event) {
    event.preventDefault()
    const outcome = await act('Authorize', () => api.authorizeTarget(t.id, auth), (a) => [
      'Experiment authorized', `${a.scenario_ids.join(', ')} until ${fmt(a.expires_at)}, at most ${a.max_runs} run(s).`,
    ])
    if (outcome.ok) {
      setAuthorizing(false)
      setAuth((a) => ({ ...a, acknowledgement: false }))
    }
  }

  async function revoke() {
    const ok = await confirm({
      title: 'Revoke this authorization?',
      message: 'No new run or response can start against this target until it is authorized again. A run in progress finishes.',
      confirmLabel: 'Revoke',
      danger: true,
    })
    if (ok) act('Revoke', () => api.revokeAuthorization(t.id, active.id), 'Authorization revoked')
  }

  const statusTone = active ? 'ok' : verified ? 'info' : t.status === 'disabled' ? 'neutral' : 'warn'
  const statusLabel = active ? 'authorized' : telemetryOk ? 'verified' : verified ? 'connection verified' : 'registered'
  const expiresIn = active ? Math.max(0, Math.round((new Date(active.expires_at) - now) / 60000)) : null

  return (
    <Panel
      title={t.name}
      icon={Target}
      meta={<StatusBadge tone={statusTone}>{statusLabel}</StatusBadge>}
      actions={
        <>
          <a className="btn" href={t.base_url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Open ShopCloud</a>
          <button className="btn btn-primary" onClick={onUse} disabled={!active}
            title={active ? 'Select this target’s lab and go to the scenarios' : 'Authorize an experiment first'}>
            Use for a run
          </button>
        </>
      }
    >
      <div className="stack">
        {detail.data && !detail.data.url_allowed && (
          <Callout tone="crit" title="URL refused by the current policy">{detail.data.url_error} Nothing is sent to this target.</Callout>
        )}
        <div className="grid grid-2">
          <KeyValue rows={[
            ['Kind', t.kind],
            ['URL', <span key="u" className="mono">{t.base_url}</span>],
            ['Pinned address', <span key="a" className="mono">{t.resolved_ip}:{t.port}</span>],
          ]} />
          <KeyValue rows={[
            ['Instance', t.instance_id ? <span key="i" className="mono">{t.instance_id}</span> : 'not verified yet'],
            ['App version', t.app_version ?? '—'],
            ['Capabilities', t.capabilities?.join(', ') ?? '—'],
          ]} />
        </div>

        <ol style={{ listStyle: 'none', padding: 0, margin: 0 }}>
          <Step state="ok" title="1. Registered" detail={`URL approved by the lab policy ${fmtAgo(t.created_at, now)}. Nothing was sent.`} />
          <Step
            state={verified ? 'ok' : 'todo'}
            title="2. Connection verified"
            detail={verified
              ? `Health check and signed handshake passed ${fmtAgo(t.verified_at, now)}: the application holds the integration key.`
              : 'Sends GET /health and a signed handshake. The application must prove it holds the integration key.'}
            action={<button className="btn btn-sm" disabled={Boolean(busy)} onClick={() => act('Verify connection', () => api.verifyTarget(t.id), 'Connection verified')}>{verified ? 'Verify again' : 'Verify connection'}</button>}
          />
          <Step
            state={telemetryOk ? 'ok' : 'todo'}
            title="3. Telemetry verified"
            detail={telemetryOk
              ? `A test security event was read back through the signed event feed ${fmtAgo(t.telemetry_verified_at, now)}.`
              : 'Asks ShopCloud to emit one test event and reads it back through its signed event feed.'}
            action={<button className="btn btn-sm" disabled={Boolean(busy) || !verified} onClick={() => act('Verify telemetry', () => api.verifyTargetTelemetry(t.id), 'Telemetry verified')}>{telemetryOk ? 'Verify again' : 'Verify telemetry'}</button>}
          />
          <Step
            state={active ? 'ok' : 'todo'}
            title="4. Authorized for a controlled experiment"
            detail={active
              ? `${active.scenario_ids.join(', ')} · by ${active.authorized_by} · ${active.runs_used}/${active.max_runs} runs used · expires in ${expiresIn} min (${fmt(active.expires_at)})`
              : 'Explicit, time-boxed permission for named scenarios. Registration alone never allows attack traffic.'}
            action={active
              ? <button className="btn btn-sm btn-danger" disabled={Boolean(busy)} onClick={revoke}><ShieldOff size={13} /> Revoke</button>
              : <button className="btn btn-sm" disabled={Boolean(busy) || !telemetryOk} onClick={() => setAuthorizing((a) => !a)}><ShieldCheck size={13} /> Authorize</button>}
          />
        </ol>

        {authorizing && !active && (
          <form onSubmit={authorize} className="stack-sm" style={{ padding: 12, border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
            <div className="grid grid-2">
              <Field label="Scenarios">
                <div className="stack-sm" style={{ gap: 4 }}>
                  {scenarios.map((s) => (
                    <label key={s.id} className="row">
                      <input type="checkbox" checked={auth.scenario_ids.includes(s.id)}
                        onChange={(e) => setAuth({ ...auth, scenario_ids: e.target.checked ? [...auth.scenario_ids, s.id] : auth.scenario_ids.filter((x) => x !== s.id) })} />
                      {s.name} <span className="small muted mono">{s.id}</span>
                    </label>
                  ))}
                </div>
              </Field>
              <div className="stack-sm">
                <Field label="Valid for">
                  <select className="select" value={auth.duration_minutes} onChange={(e) => setAuth({ ...auth, duration_minutes: Number(e.target.value) })}>
                    {[15, 30, 60].map((m) => <option key={m} value={m}>{m} minutes</option>)}
                  </select>
                </Field>
                <Field label="Maximum runs">
                  <select className="select" value={auth.max_runs} onChange={(e) => setAuth({ ...auth, max_runs: Number(e.target.value) })}>
                    {[1, 2, 3, 5].map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </Field>
                <Field label="Authorized by">
                  <input className="input" required maxLength={64} value={auth.authorized_by} placeholder="Your name"
                    onChange={(e) => setAuth({ ...auth, authorized_by: e.target.value })} />
                </Field>
              </div>
            </div>
            <label className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap' }}>
              <input type="checkbox" checked={auth.acknowledgement} onChange={(e) => setAuth({ ...auth, acknowledgement: e.target.checked })} style={{ marginTop: 3 }} />
              <span>I confirm that <span className="mono">{t.base_url}</span> is a lab system I control, and I authorize CloudAttackLab to run the selected scenarios against it for the time and number of runs above.</span>
            </label>
            <div className="row">
              <button className="btn btn-primary" type="submit" disabled={Boolean(busy) || !auth.acknowledgement || auth.scenario_ids.length === 0}>
                <ShieldCheck size={14} /> Authorize experiment
              </button>
              <button className="btn btn-ghost" type="button" onClick={() => setAuthorizing(false)}>Cancel</button>
            </div>
          </form>
        )}

        {detail.data?.authorizations?.length > 0 && (
          <DataTable
            rowKey={(a) => a.id}
            rows={detail.data.authorizations}
            columns={[
              { key: 'status', header: 'Authorization', render: (a) => <StatusBadge tone={a.status === 'active' ? 'ok' : 'neutral'}>{a.status}</StatusBadge> },
              { key: 'scenario_ids', header: 'Scenarios', render: (a) => a.scenario_ids.join(', ') },
              { key: 'authorized_by', header: 'By' },
              { key: 'runs', header: 'Runs', render: (a) => `${a.runs_used}/${a.max_runs}` },
              { key: 'created_at', header: 'Granted', render: (a) => fmt(a.created_at) },
              { key: 'expires_at', header: 'Expires', render: (a) => (a.revoked_at ? `revoked ${fmt(a.revoked_at)}` : fmt(a.expires_at)) },
            ]}
            footer={`Every step is in the audit trail (target ${humanize(t.status)}).`}
          />
        )}
      </div>
    </Panel>
  )
}
