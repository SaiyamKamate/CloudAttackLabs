import { useEffect, useState } from 'react'
import { api } from '../api.js'

// The lab's status and ShopCloud's health, with Start / Stop / Reset.
export default function LabPage() {
  const [lab, setLab] = useState(null)       // the lab summary (first lab, if any)
  const [detail, setDetail] = useState(null) // containers + target health
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

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
  const container = detail?.containers?.[0]

  return (
    <section>
      <h2>Lab</h2>
      {error && <p style={{ color: 'crimson' }}>Error: {error}</p>}

      <table>
        <tbody>
          <tr><th align="left">Name</th><td>{lab.name}</td></tr>
          <tr><th align="left">Status</th><td><b>{lab.status}</b></td></tr>
          <tr>
            <th align="left">Container</th>
            <td>{container ? `${container.service}: ${container.state} (${container.health ?? 'no health check'})` : '—'}</td>
          </tr>
          <tr>
            <th align="left">ShopCloud health</th>
            <td>{health ? (health.healthy ? 'healthy' : health.reachable ? `unhealthy (HTTP ${health.status_code})` : `unreachable: ${health.error}`) : '—'}</td>
          </tr>
        </tbody>
      </table>

      <p style={{ marginTop: '1rem' }}>
        <button onClick={start} disabled={busy}>Start</button>{' '}
        <button onClick={stop} disabled={busy}>Stop</button>{' '}
        <button onClick={reset} disabled={busy}>Reset</button>{' '}
        <button onClick={load} disabled={busy}>Refresh</button>
      </p>
      {busy && <p>Working… (start, stop and reset can take a little while)</p>}
    </section>
  )
}
