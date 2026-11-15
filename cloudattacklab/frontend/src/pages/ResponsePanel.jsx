import { useEffect, useState } from 'react'
import { api } from '../api.js'

// Statuses after which a new response may be requested.
const CLOSED = ['rejected', 'failed', 'reverted']

// The response action for one incident: request it, approve or reject it, then
// read its status, the verification result and its audit trail.
export default function ResponsePanel({ incidentId }) {
  const [response, setResponse] = useState(null) // the incident's latest response, if any
  const [audit, setAudit] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      const incident = await api.getIncident(incidentId)
      const latest = incident.responses.length ? incident.responses[incident.responses.length - 1] : null
      setResponse(latest)
      setAudit(latest ? await api.listAudit({ object_type: 'response_action', object_id: latest.id }) : [])
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    // Fetch when the selected incident changes. State is set after the await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidentId])

  async function act(call) {
    setBusy(true)
    setError(null)
    try {
      await call()
    } catch (err) {
      setError(err.message)
    } finally {
      await load()
      setBusy(false)
    }
  }

  const canRequest = !response || CLOSED.includes(response.status)

  return (
    <div style={{ border: '1px solid #ccc', padding: '0.75rem', margin: '1rem 0' }}>
      <h4 style={{ marginTop: 0 }}>Response</h4>
      {error && <p style={{ color: 'crimson' }}>Error: {error}</p>}

      {!response && <p>No response has been requested for this incident.</p>}

      {response && (
        <>
          <p>
            <strong>{response.action}</strong> ({describeParameters(response)}) — status:{' '}
            <strong>{response.status}</strong>
          </p>
          <p>{inPlainWords(response)}</p>
          {response.verification_result?.received?.statuses && (
            <p>
              Follow-up logins sent: {response.verification_result.sent.attempts}. ShopCloud answered:{' '}
              <code>{response.verification_result.received.statuses.map((s) => s ?? 'no answer').join(', ')}</code>
            </p>
          )}
        </>
      )}

      {response?.status === 'pending_approval' && (
        <p>
          <button disabled={busy} onClick={() => act(() => api.approveResponse(response.id))}>Approve</button>{' '}
          <button disabled={busy} onClick={() => act(() => api.rejectResponse(response.id))}>Reject</button>
          {busy && ' Working…'}
        </p>
      )}

      {canRequest && (
        <button disabled={busy} onClick={() => act(() => api.requestResponse(incidentId))}>
          Request response
        </button>
      )}{' '}
      <button disabled={busy} onClick={load}>Refresh</button>

      {audit.length > 0 && (
        <>
          <p style={{ marginBottom: '0.25rem' }}>Audit trail for this response:</p>
          <table border="1" cellPadding="6" style={{ borderCollapse: 'collapse' }}>
            <thead>
              <tr><th align="left">Time</th><th align="left">Action</th><th align="left">Actor</th><th align="left">Detail</th></tr>
            </thead>
            <tbody>
              {[...audit].reverse().map((row) => (
                <tr key={row.id}>
                  <td>{new Date(row.timestamp).toLocaleString()}</td>
                  <td>{row.action}</td>
                  <td>{row.actor}</td>
                  <td>{auditDetail(row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

function describeParameters(response) {
  const { max_attempts: max, window_seconds: seconds } = response.parameters
  return `at most ${max} login attempts per ${seconds} seconds`
}

function blockedSentence(result) {
  return `${result.received.blocked_429} of ${result.sent.attempts} follow-up logins were blocked with 429`
}

// The status and verification result as one plain sentence.
function inPlainWords(response) {
  const result = response.verification_result
  switch (response.status) {
    case 'pending_approval':
      return 'Waiting for approval. Nothing has been changed on ShopCloud.'
    case 'rejected':
      return `Rejected by ${response.decided_by}. ShopCloud was not changed.`
    case 'approved':
      return `Approved by ${response.decided_by}; it is being applied.`
    case 'executed':
      return 'The rate limit is applied on ShopCloud; it has not been verified yet.'
    case 'verified':
      return `Verified: ${blockedSentence(result)}.`
    case 'failed':
      if (result?.received) {
        return `Failed: ${blockedSentence(result)}; the ${result.received.expected_blocked} sent beyond the limit should all have been.`
      }
      return `Failed while trying to ${result?.stage === 'execute' ? 'apply the rate limit' : 'verify it'}: ${result?.error ?? 'unknown error'}`
    case 'reverted':
      return (
        'Reverted: the rate limit is off again.' +
        (result?.received ? ` Before that it was verified: ${blockedSentence(result)}.` : '')
      )
    default:
      return ''
  }
}

function auditDetail(row) {
  const detail = row.detail || {}
  switch (row.action) {
    case 'response.requested':
      return `from scenario ${detail.scenario_id}`
    case 'response.approved':
    case 'response.rejected':
      return `decided by ${detail.decided_by}`
    case 'response.executed':
      return `${detail.request} ${JSON.stringify(detail.sent)}`
    case 'response.verified':
    case 'response.failed':
      return detail.received ? blockedSentence(detail) : detail.error ?? ''
    case 'response.reverted':
      return `by ${detail.trigger}`
    default:
      return ''
  }
}
