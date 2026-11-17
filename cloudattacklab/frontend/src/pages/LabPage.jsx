import { useEffect, useState } from 'react'
import { api } from '../api.js'

// The lab's status and ShopCloud's health, with Start / Stop / Reset.
export default function LabPage() {
  const [lab, setLab] = useState(null)       // the lab summary (first lab, if any)
  const [detail, setDetail] = useState(null) // containers + target health
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false) // false until the first answer (or error) arrives

  async function load() {
    try {
      const labs = await api.listLabs()
      setError(null)
      if (labs.length === 0) {
        setLab(null)
        setDetail(null)
        return
      }
      setLab(labs[0])
      setDetail(await api.getLab(labs[0].id))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoaded(true)
    }
  }

  useEffect(() => {
    // Fetch on mount. State is set after the await, not synchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [])

  async function act(fn) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const createLab = () => act(() => api.createLab('default'))
  const start = () => act(() => api.startLab(lab.id))
  const stop = () => act(() => api.stopLab(lab.id))
  const reset = () => act(() => api.resetLab(lab.id))

  if (!loaded) {
    return (
      <section>
        <h2>Lab</h2>
        <p>Loading…</p>
      </section>
    )
  }

  if (error && !lab) {
    return (
      <section>
        <h2>Lab</h2>
        <p style={{ color: 'crimson' }}>Error: {error}</p>
        <button onClick={load}>Retry</button>
      </section>
    )
  }

  if (!lab) {
    return (
      <section>
        <h2>Lab</h2>
        <p>No lab yet.</p>
        <button onClick={createLab} disabled={busy}>Create lab</button>
      </section>
    )
  }

  const health = detail?.target_health
  const containers = detail?.containers ?? []
  const activeRun = detail?.active_run ?? null

  return (
    <section>
      <h2>Lab</h2>
      {error && <p style={{ color: 'crimson' }}>Error: {error}</p>}
      {detail?.target_url && !detail.target_url.allowed && (
        <p style={{ color: 'crimson', border: '1px solid crimson', padding: '0.5rem' }}>
          <b>Target URL refused.</b> {detail.target_url.error} Runs, start and reset are refused until
          SHOPCLOUD_URL in .env points at the lab (for example http://127.0.0.1:8081) and the backend is restarted.
        </p>
      )}

      <table>
        <tbody>
          <tr><th align="left">Name</th><td>{lab.name}</td></tr>
          <tr>
            <th align="left">Status</th>
            <td>
              <b>{detail?.status ?? lab.status}</b>
              {detail?.status_detail && (
                <div style={{ color: ['degraded', 'stopped'].includes(detail.status) ? 'crimson' : undefined }}>
                  {detail.status_detail}
                </div>
              )}
            </td>
          </tr>
          <tr>
            <th align="left">Containers</th>
            <td>
              {containers.length === 0 ? '—' : containers.map((c) => (
                <div key={c.service}>{`${c.service}: ${c.state} (${c.health ?? 'no health check'})`}</div>
              ))}
            </td>
          </tr>
          <tr>
            <th align="left">ShopCloud URL</th>
            <td>{detail?.target_url ? `${detail.target_url.url} (${detail.target_url.allowed ? 'inside the lab' : 'REFUSED'})` : '—'}</td>
          </tr>
          <tr>
            <th align="left">ShopCloud health</th>
            <td>{health ? (health.healthy ? 'healthy' : health.reachable ? `unhealthy (HTTP ${health.status_code})` : `unreachable: ${health.error}`) : '—'}</td>
          </tr>
          <tr>
            <th align="left">Falco</th>
            <td>{falcoText(detail?.falco)}</td>
          </tr>
          <tr>
            <th align="left">Telemetry source</th>
            <td>{telemetryText(detail?.telemetry)}</td>
          </tr>
        </tbody>
      </table>

      <p style={{ marginTop: '1rem' }}>
        <button onClick={start} disabled={busy}>Start</button>{' '}
        <button onClick={stop} disabled={busy}>Stop</button>{' '}
        <button onClick={reset} disabled={busy || Boolean(activeRun)}>Reset</button>{' '}
        <button onClick={load} disabled={busy}>Refresh</button>
      </p>
      {activeRun && (
        <p><i>
          Reset is disabled while run {activeRun.scenario_id} ({activeRun.status}) is in progress: a reset
          recreates ShopCloud and would break that run. Refresh once it has finished.
        </i></p>
      )}
      {busy && <p>Working… (start, stop and reset can take a little while)</p>}
    </section>
  )
}

// FALCO_ENABLED and the Falco container's state.
function falcoText(falco) {
  if (!falco) return '—'
  if (!falco.enabled) return 'off (FALCO_ENABLED=false): container scenarios use Docker events only'
  if (falco.healthy) return 'on and healthy'
  const state = falco.state ? `${falco.state}${falco.health ? `, ${falco.health}` : ''}` : 'no Falco container; start or reset the lab'
  return <span style={{ color: 'crimson' }}>on but NOT healthy ({state})</span>
}

// TELEMETRY_SOURCE as the backend reports it, with Loki's reachability.
function telemetryText(telemetry) {
  if (!telemetry) return '—'
  if (telemetry.source !== 'loki') return `${telemetry.source} (the lab container's logs)`
  if (telemetry.reachable) return `loki at ${telemetry.loki_url} (reachable)`
  return <span style={{ color: 'crimson' }}>loki at {telemetry.loki_url}: NOT reachable. {telemetry.error}</span>
}
