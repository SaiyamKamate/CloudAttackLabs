const PREREQ_LABELS = {
  http_health: 'ShopCloud answers its health check',
  test_account: 'Test account can log in',
  container_running: 'Test container is running',
}

// What the dashboard can see about whether a scenario may run now. The lab
// state comes from GET /labs/{id}. The backend still runs its own preflight
// and safety checks when the run is requested and refuses with 409 if any fail;
// these checks only explain the state in advance.
export function readiness({ definition, labDetail, activeLab, runActive }) {
  const status = labDetail?.status ?? activeLab?.status
  const checks = []

  checks.push({
    label: 'Lab is running',
    state: !activeLab ? 'fail' : status === 'running' ? 'ok' : 'fail',
    detail: !activeLab ? 'No lab exists' : `Lab ${activeLab.name} is ${status}`,
  })
  checks.push({
    label: 'Target URL points into the lab',
    state: !labDetail?.target_url ? 'unknown' : labDetail.target_url.allowed ? 'ok' : 'fail',
    detail: labDetail?.target_url ? labDetail.target_url.url : 'Not known yet',
  })
  const activeRun = labDetail?.active_run
  checks.push({
    label: 'No other run in progress',
    state: activeRun || runActive ? 'fail' : 'ok',
    detail: activeRun ? `${activeRun.scenario_id} is ${activeRun.status}` : runActive ? 'A run started here is still active' : 'The lab is free',
  })

  for (const prerequisite of definition?.prerequisites ?? []) {
    const label = PREREQ_LABELS[prerequisite.check] || prerequisite.check
    let state = 'unknown'
    let detail = 'Checked by the backend preflight when the run starts'
    if (prerequisite.check === 'http_health' && labDetail?.target_health) {
      state = labDetail.target_health.healthy ? 'ok' : 'fail'
      detail = labDetail.target_health.healthy ? 'Healthy at the last lab refresh' : (labDetail.target_health.error || `HTTP ${labDetail.target_health.status_code}`)
    }
    if (prerequisite.check === 'container_running' && labDetail) {
      const container = labDetail.containers?.find((c) => c.service === (prerequisite.detail || 'test_container'))
      state = container?.state === 'running' ? 'ok' : 'fail'
      detail = container ? `${container.service}: ${container.state}` : 'No test container in the lab'
    }
    checks.push({ label: `${label}${prerequisite.detail ? ` (${prerequisite.detail})` : ''}`, state, detail, preflight: true })
  }

  const blocking = checks.filter((c) => !c.preflight && c.state === 'fail')
  const canRun = blocking.length === 0
  let availability = { label: 'ready', tone: 'ok' }
  if (!activeLab || status !== 'running') availability = { label: 'lab not running', tone: 'neutral' }
  else if (labDetail?.target_url && !labDetail.target_url.allowed) availability = { label: 'target refused', tone: 'crit' }
  else if (activeRun || runActive) availability = { label: 'lab busy', tone: 'warn' }

  return { checks, canRun, reason: blocking[0]?.detail, availability }
}
