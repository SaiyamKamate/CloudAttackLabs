import { useEffect, useState } from 'react'
import { api } from '../api.js'

// A table of alerts; clicking one shows the events that caused it.
export default function AlertsPage() {
  const [alerts, setAlerts] = useState([])
  const [selected, setSelected] = useState(null) // the alert detail (with events)
  const [error, setError] = useState(null)

  async function load() {
    try {
      const data = await api.listAlerts()
      setAlerts(data)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    // Fetch on mount. State is set after the await, not synchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [])

  async function open(alertId) {
    setError(null)
    try {
      setSelected(await api.getAlert(alertId))
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <h2>Alerts</h2>
      {error && <p style={{ color: 'crimson' }}>Error: {error}</p>}
      <button onClick={load}>Refresh</button>

      <table border="1" cellPadding="6" style={{ borderCollapse: 'collapse', marginTop: '0.5rem' }}>
        <thead>
          <tr>
            <th align="left">Rule</th><th align="left">Severity</th><th align="right">Events</th>
            <th align="left">First event</th><th align="left">Last event</th><th></th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((a) => (
            <tr key={a.id}>
              <td>{a.rule_id}</td>
              <td>{a.severity}</td>
              <td align="right">{a.event_count}</td>
              <td>{fmt(a.first_event_at)}</td>
              <td>{fmt(a.last_event_at)}</td>
              <td><button onClick={() => open(a.id)}>View events</button></td>
            </tr>
          ))}
          {alerts.length === 0 && <tr><td colSpan="6">No alerts yet.</td></tr>}
        </tbody>
      </table>

      {selected && (
        <div style={{ marginTop: '1rem' }}>
          <h3>{selected.title}</h3>
          <p>{selected.description}</p>
          <p>{selected.events.length} linked event(s):</p>
          <table border="1" cellPadding="6" style={{ borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th align="left">Time</th><th align="left">Type</th><th align="left">Source IP</th>
                <th align="left">Evidence (raw_event_reference)</th>
              </tr>
            </thead>
            <tbody>
              {selected.events.map((e) => (
                <tr key={e.event_id}>
                  <td>{fmt(e.timestamp)}</td>
                  <td>{e.event_type}</td>
                  <td>{e.source_ip ?? '—'}</td>
                  <td><code>{e.raw_event_reference}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function fmt(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}
