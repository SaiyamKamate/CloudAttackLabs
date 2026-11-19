// Plain-language descriptions of backend results. Moved unchanged in meaning
// from the original ResponsePanel and ScenariosPage so every page words them
// the same way.

// The check the engine repeated after the run. Works for any check; the
// "still denied" wording is used when the result carries that field.
export function verificationText(v) {
  if (!v) return null
  const request = v.sent ? `${v.sent.request} as ${v.sent.username}` : v.check
  const status = v.received?.status
  if (v.still_denied === true) {
    return `Verification passed: access is still denied (${request} returned ${status}).`
  }
  if (v.still_denied === false && status) {
    return `Verification FAILED: access is no longer denied (${request} returned ${status}).`
  }
  if (v.passed) return `Verification passed: ${v.check}.`
  return `Verification FAILED: ${v.check} could not be confirmed${v.error ? ` (${v.error})` : ''}.`
}

export function describeParameters(response) {
  if (response.action === 'stop_container') {
    return `stop the test container ${response.parameters.container_id} (${response.parameters.target})`
  }
  const { max_attempts: max, window_seconds: seconds } = response.parameters
  return `at most ${max} login attempts per ${seconds} seconds`
}

export function responseTarget(response) {
  if (response.action === 'stop_container') {
    return response.parameters.target ? `${response.parameters.target} (${response.parameters.container_id ?? '—'})` : 'test container'
  }
  return 'ShopCloud login'
}

export function blockedSentence(result) {
  if (result.check === 'container_not_running') {
    return `the test container ${result.sent.container} is ${result.received.running ? 'STILL running' : 'no longer running'}`
  }
  return `${result.received.blocked_429} of ${result.sent.attempts} follow-up logins were blocked with 429`
}

// The status and verification result as one plain sentence.
export function inPlainWords(response) {
  const result = response.verification_result
  if (response.action === 'stop_container') return stopContainerWords(response, result)
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

function stopContainerWords(response, result) {
  switch (response.status) {
    case 'pending_approval':
      return 'Waiting for approval. Nothing has been changed on the test container.'
    case 'rejected':
      return `Rejected by ${response.decided_by}. The test container was not touched.`
    case 'approved':
      return `Approved by ${response.decided_by}; the test container is being stopped.`
    case 'executed':
      return 'The test container was stopped; it has not been verified yet.'
    case 'verified':
      return `Verified: ${blockedSentence(result)}.`
    case 'failed':
      if (result?.refused) return `Refused, nothing was stopped: ${result.error}`
      if (result?.received) return `Failed: ${blockedSentence(result)}.`
      return `Failed while trying to ${result?.stage === 'execute' ? 'stop the container' : 'verify it'}: ${result?.error ?? 'unknown error'}`
    case 'reverted':
      return (
        'Reverted: cleanup recreated the test container.' +
        (result?.received ? ` Before that it was verified: ${blockedSentence(result)}.` : '')
      )
    default:
      return ''
  }
}

// Verification outcome of a response, as recorded by the backend.
export function responseVerification(response) {
  const result = response.verification_result
  if (!result || result.passed === undefined) return { label: 'not verified', tone: 'neutral' }
  return result.passed ? { label: 'passed', tone: 'ok' } : { label: 'failed', tone: 'crit' }
}

export function auditDetail(row) {
  const detail = row.detail || {}
  switch (row.action) {
    case 'response.requested':
      return `from scenario ${detail.scenario_id}`
    case 'response.approved':
    case 'response.rejected':
      return `decided by ${detail.decided_by}`
    case 'response.executed':
      return detail.container ? `${detail.request} ${detail.container} (${detail.container_id})` : `${detail.request} ${JSON.stringify(detail.sent)}`
    case 'response.verified':
    case 'response.failed':
      return detail.received ? blockedSentence(detail) : detail.error ?? ''
    case 'response.reverted':
      return `by ${detail.trigger}`
    default:
      return ''
  }
}

export function responseResult(result) {
  if (!result) return '—'
  if (result.check === 'container_not_running' && result.received) {
    return `${result.passed ? 'passed' : 'failed'}: the test container ${result.sent.container} is ${result.received.running ? 'still running' : 'no longer running'}`
  }
  if (result.received && result.sent) {
    return `${result.passed ? 'passed' : 'failed'}: ${result.received.blocked_429} of ${result.sent.attempts} follow-up logins were blocked with 429`
  }
  return result.passed ? 'passed' : `failed${result.error ? `: ${result.error}` : ''}`
}

// FALCO_ENABLED and the Falco container's state.
export function falcoState(falco) {
  if (!falco) return { tone: 'neutral', label: 'unknown', detail: 'No data' }
  if (!falco.enabled) return { tone: 'neutral', label: 'disabled', detail: 'FALCO_ENABLED=false: container scenarios use Docker events only' }
  if (falco.healthy) return { tone: 'ok', label: 'healthy', detail: 'On and healthy' }
  const state = falco.state ? `${falco.state}${falco.health ? `, ${falco.health}` : ''}` : 'no Falco container; start or reset the lab'
  return { tone: 'crit', label: 'unhealthy', detail: `On but not healthy (${state})` }
}

// TELEMETRY_SOURCE as the backend reports it, with Loki's reachability.
export function telemetryState(telemetry) {
  if (!telemetry) return { tone: 'neutral', label: 'unknown', detail: 'No data' }
  if (telemetry.source !== 'loki') return { tone: 'ok', label: telemetry.source, detail: `${telemetry.source} (the lab container's logs)` }
  if (telemetry.reachable) return { tone: 'ok', label: 'loki', detail: `Loki at ${telemetry.loki_url} (reachable)` }
  return { tone: 'crit', label: 'loki unreachable', detail: `Loki at ${telemetry.loki_url}: not reachable. ${telemetry.error ?? ''}` }
}

export function targetHealthState(health) {
  if (!health) return { tone: 'neutral', label: 'unknown', detail: 'No data' }
  if (health.healthy) return { tone: 'ok', label: 'healthy', detail: `HTTP ${health.status_code ?? 200}` }
  if (health.reachable) return { tone: 'warn', label: 'unhealthy', detail: `HTTP ${health.status_code}` }
  return { tone: 'crit', label: 'unreachable', detail: health.error ?? 'No answer' }
}

export function labStatusTone(status) {
  switch (status) {
    case 'running': return 'ok'
    case 'starting': return 'info'
    case 'degraded': return 'warn'
    case 'error': return 'crit'
    default: return 'neutral'
  }
}

export function runStatusTone(status) {
  switch (status) {
    case 'completed': return 'ok'
    case 'cleaned_up': return 'ok'
    case 'pending':
    case 'running': return 'info'
    case 'incomplete': return 'warn'
    case 'failed': return 'crit'
    default: return 'neutral'
  }
}

export function responseStatusTone(status) {
  switch (status) {
    case 'verified': return 'ok'
    case 'pending_approval': return 'warn'
    case 'approved':
    case 'executed': return 'info'
    case 'failed': return 'crit'
    default: return 'neutral'
  }
}

// Parse the backend's risk explanation ("+40: reason.") into factors. The
// explanation is the backend's own text; this only splits it into lines.
export function parseRisk(explanation) {
  if (!explanation) return { factors: [], notes: [] }
  const factors = []
  const notes = []
  for (const line of explanation.split('\n')) {
    const match = line.match(/^([+-]\d+): (.*)$/)
    if (match) factors.push({ points: Number(match[1]), reason: match[2].replace(/\.$/, '') })
    else if (line.trim() && !line.startsWith('Risk score:')) notes.push(line)
  }
  return { factors, notes }
}

export function riskTone(value) {
  if (value === null || value === undefined) return 'neutral'
  if (value >= 75) return 'critical'
  if (value >= 50) return 'high'
  if (value >= 25) return 'medium'
  return 'low'
}

// The Falco container is expected to be down when FALCO_ENABLED=false; the
// backend does not count it against the lab's status then.
export function isUnusedFalco(container, falco) {
  return container.service === 'falco' && falco && !falco.enabled
}

export function containerTone(container) {
  if (container.health === 'healthy') return 'ok'
  if (container.health === 'unhealthy') return 'crit'
  if (container.health === 'starting') return 'info'
  if (container.state === 'running') return 'ok'
  if (container.state === 'exited' || container.state === 'dead') return 'crit'
  return 'neutral'
}

const SEV_RANK = { info: 0, low: 1, medium: 2, high: 3, critical: 4 }

// The highest severity among an incident's alerts. Incidents carry no
// severity of their own; this is the same input the risk score starts from.
export function highestSeverity(alerts) {
  return alerts.reduce((top, a) => ((SEV_RANK[a.severity] ?? -1) > (SEV_RANK[top] ?? -1) ? a.severity : top), null)
}
