import { useEffect, useState } from 'react'
import { Check, RefreshCw, ShieldCheck, ShieldPlus, X } from 'lucide-react'
import { api } from '../../api.js'
import { usePlatform, useUI } from '../../lib/context.js'
import { auditDetail, describeParameters, inPlainWords, responseStatusTone, responseVerification } from '../../lib/describe.js'
import { fmt } from '../../lib/format.js'
import { StatusBadge } from '../ui/Badges.jsx'
import { Callout, KeyValue } from '../ui/Layout.jsx'
import { LoadingState } from '../ui/States.jsx'

// Statuses after which a new response may be requested.
const CLOSED = ['rejected', 'failed', 'reverted']

// The response action for one incident: request it, approve or reject it, then
// read its status, the verification result and its audit trail. `onChange`,
// if given, is called after each step so the page around it can refresh.
// A status is shown only as the backend returns it.
export default function ResponsePanel({ incidentId, onChange, closed = false }) {
  const { confirm, notify } = useUI()
  const { refreshAttention } = usePlatform()
  const [response, setResponse] = useState(null) // the incident's latest response, if any
  const [audit, setAudit] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [loaded, setLoaded] = useState(false) // false until the first answer (or error) arrives

  async function load() {
    try {
      const incident = await api.getIncident(incidentId)
      const latest = incident.responses.length ? incident.responses[incident.responses.length - 1] : null
      setResponse(latest)
      setAudit(latest ? await api.listAudit({ object_type: 'response_action', object_id: latest.id }) : [])
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoaded(true)
    }
  }

  useEffect(() => {
    // Fetch when the selected incident changes. State is set after the await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidentId])

  async function act(label, call, describeResult) {
    setBusy(label)
    setError(null)
    try {
      const result = await call()
      notify(result.status === 'failed' ? 'error' : 'success', describeResult(result), inPlainWords(result))
    } catch (err) {
      setError(err.message)
      notify('error', `${label} failed`, err.message)
    } finally {
      await load()
      setBusy(null)
      refreshAttention()
      if (onChange) onChange()
    }
  }

  async function approve() {
    const ok = await confirm({
      title: 'Approve and execute this response?',
      message: `The backend will ${describeParameters(response)} now, then verify it. This changes the lab.`,
      confirmLabel: 'Approve and execute',
      danger: true,
    })
    if (ok) act('Approve', () => api.approveResponse(response.id), (r) => `Response ${r.status}`)
  }

  async function reject() {
    const ok = await confirm({
      title: 'Reject this response?',
      message: 'Nothing is changed in the lab. A new response can be requested afterwards.',
      confirmLabel: 'Reject',
    })
    if (ok) act('Reject', () => api.rejectResponse(response.id), () => 'Response rejected')
  }

  async function request() {
    const ok = await confirm({
      title: 'Request a response action?',
      message: 'The action and its parameters come from the scenario. The request only creates it as “pending approval”; nothing is changed until it is approved.',
      confirmLabel: 'Request response',
    })
    if (ok) act('Request', () => api.requestResponse(incidentId), (r) => `Response requested: ${r.action}`)
  }

  // A closed incident takes no new response.
  const canRequest = !closed && (!response || CLOSED.includes(response.status))
  const verification = response ? responseVerification(response) : null

  return (
    <div className="panel">
      <div className="panel-header">
        <h2><ShieldCheck size={14} className="muted" />Response action</h2>
        <div className="row">
          {canRequest && (
            <button className="btn btn-sm" disabled={Boolean(busy)} onClick={request}><ShieldPlus size={13} /> Request response</button>
          )}
          <button className="btn btn-sm btn-ghost" disabled={Boolean(busy)} onClick={load} aria-label="Refresh response"><RefreshCw size={13} /></button>
        </div>
      </div>
      <div className="panel-body stack-sm">
        {error && <Callout tone="crit">{error}</Callout>}
        {!loaded && <LoadingState compact />}
        {loaded && !response && !error && <div className="muted">No response has been requested for this incident.</div>}

        {response && (
          <>
            <div className="row">
              <code>{response.action}</code>
              <StatusBadge tone={responseStatusTone(response.status)}>{response.status}</StatusBadge>
              <span className="muted small">verification:</span>
              <StatusBadge tone={verification.tone}>{verification.label}</StatusBadge>
            </div>
            <div className="small muted">{describeParameters(response)}</div>
            <div>{inPlainWords(response)}</div>
            {response.verification_result?.received?.statuses && (
              <div className="small">
                Follow-up logins sent: {response.verification_result.sent.attempts}. ShopCloud answered:{' '}
                <code>{response.verification_result.received.statuses.map((s) => s ?? 'no answer').join(', ')}</code>
              </div>
            )}

            {response.status === 'pending_approval' && (
              <div className="row" style={{ padding: '6px 0' }}>
                <button className="btn btn-primary" disabled={Boolean(busy)} onClick={approve}><Check size={14} /> Approve</button>
                <button className="btn btn-danger" disabled={Boolean(busy)} onClick={reject}><X size={14} /> Reject</button>
              </div>
            )}
            {busy && (
              <div className="stack-sm">
                <div className="progress" />
                <div className="small muted">{busy === 'Approve' ? 'Executing and verifying on the lab; waiting for the backend…' : 'Working…'}</div>
              </div>
            )}

            <KeyValue rows={[
              ['Requested', fmt(response.requested_at)],
              ['Decided', response.decided_at ? `${fmt(response.decided_at)} by ${response.decided_by}` : '—'],
              ['Executed', fmt(response.executed_at)],
              ['Verified', fmt(response.verified_at)],
            ]} />
          </>
        )}

        {audit.length > 0 && (
          <details>
            <summary className="small muted" style={{ cursor: 'pointer', margin: '6px 0' }}>Audit trail ({audit.length})</summary>
            <div className="table-wrap" style={{ border: '1px solid var(--border)', borderRadius: 4 }}>
              <table className="table">
                <thead><tr><th>Time</th><th>Action</th><th>Actor</th><th>Detail</th></tr></thead>
                <tbody>
                  {[...audit].reverse().map((row) => (
                    <tr key={row.id}>
                      <td className="nowrap">{fmt(row.timestamp)}</td>
                      <td><code>{row.action}</code></td>
                      <td>{row.actor}</td>
                      <td className="wrap">{auditDetail(row)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
      </div>
    </div>
  )
}
