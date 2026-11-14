// The one place the dashboard talks to the backend. No page calls fetch directly.
//
// Paths are relative ("/api/v1/..."), so in development Vite's proxy forwards
// them to the backend and in the container nginx does the same. All times are
// UTC and all bodies are JSON (see docs/api-contract.md).

const BASE = '/api/v1'

// Thrown for anything that goes wrong: a network failure or a 4xx/5xx body.
export class ApiError extends Error {
  constructor(message, { status = null, code = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function request(path, { method = 'GET', body } = {}) {
  let response
  try {
    response = await fetch(BASE + path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    // fetch only rejects when the request never got an answer.
    throw new ApiError('Cannot reach the backend. Is it running on 127.0.0.1:8000?')
  }

  let data = null
  const text = await response.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }

  if (!response.ok) {
    // The backend's error shape is { error, detail }.
    const detail = (data && (data.detail || data.error)) || `Request failed (HTTP ${response.status})`
    throw new ApiError(detail, { status: response.status, code: data && data.error })
  }
  return data
}

export const api = {
  // Labs
  listLabs: () => request('/labs'),
  createLab: (name, target = 'shopcloud') => request('/labs', { method: 'POST', body: { name, target } }),
  getLab: (labId) => request(`/labs/${labId}`),
  startLab: (labId) => request(`/labs/${labId}/start`, { method: 'POST' }),
  stopLab: (labId) => request(`/labs/${labId}/stop`, { method: 'POST' }),
  resetLab: (labId) => request(`/labs/${labId}/reset`, { method: 'POST' }),

  // Scenarios and runs
  listScenarios: () => request('/scenarios'),
  startRun: (labId, scenarioId) => request(`/labs/${labId}/runs`, { method: 'POST', body: { scenario_id: scenarioId } }),
  getRun: (runId) => request(`/runs/${runId}`),
  cleanupRun: (runId) => request(`/runs/${runId}/cleanup`, { method: 'POST' }),

  // Events and alerts
  listAlerts: (params = {}) => request('/alerts' + query(params)),
  getAlert: (alertId) => request(`/alerts/${alertId}`),
}

function query(params) {
  const pairs = Object.entries(params).filter(([, value]) => value !== undefined && value !== null)
  return pairs.length ? '?' + new URLSearchParams(pairs).toString() : ''
}
