import { useEffect, useState } from 'react'
import { api } from '../api.js'
import ResponsePanel from './ResponsePanel.jsx'

const TABLE = { borderCollapse: 'collapse', marginTop: '0.5rem' }

// A list of incidents with status and risk score; clicking one shows its risk
// explanation, timeline, linked alerts and the response controls.
export default function IncidentsPage({ openReport }) {
  const [incidents, setIncidents] = useState([])
  const [selected, setSelected] = useState(null) // the incident detail
  const [error, setError] = useState(null)
  const [loaded, setLoaded] = useState(false) // false until the first answer (or error) arrives

  async function load() {
    try {
      const data = await api.listIncidents()
      setIncidents([...data].reverse()) // newest first
      setError(null)
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

  async function open(incidentId) {
    setError(null)
    try {
      setSelected(await api.getIncident(incidentId))
    } catch (err) {
      setError(err.message)
    }
  }

  // After a response step the risk score and the timeline have changed.
  async function refreshSelected() {
    await open(selected.id)
    await load()
  }

  return (
    <section>
      <h2>Incidents</h2>
      {error && <p style={{ color: 'crimson' }}>Error: {error}</p>}
      <button onClick={load}>Refresh</button>

      <table border="1" cellPadding="6" style={TABLE}>
        <thead>
          <tr>
            <th align="left">Incident</th><th align="left">Status</th><th align="right">Risk score</th>
            <th align="left">Opened</th><th></th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => (
            <tr key={i.id} style={{ fontWeight: selected?.id === i.id ? 'bold' : 'normal' }}>
              <td>{i.title}</td>
              <td>{i.status}</td>
              <td align="right">{score(i.risk_score)}</td>
              <td>{fmt(i.created_at)}</td>
              <td><button onClick={() => open(i.id)}>View</button></td>
            </tr>
          ))}
          {!loaded && <tr><td colSpan="5">Loading…</td></tr>}
          {loaded && incidents.length === 0 && <tr><td colSpan="5">{error ? 'Could not load incidents.' : 'No incidents yet.'}</td></tr>}
        </tbody>
      </table>

      {selected && (
        <div style={{ marginTop: '1rem' }}>
          <h3>{selected.title}</h3>
          <p>
            Status: <b>{selected.status}</b>. Correlation key: <code>{selected.correlation_key ?? '—'}</code>{' '}
            {selected.run_id && <button onClick={() => openReport(selected.run_id)}>View report</button>}
          </p>

          <h4>Risk score: {score(selected.risk_score)}</h4>
          {selected.risk_explanation
            ? <p style={{ whiteSpace: 'pre-line' }}>{selected.risk_explanation}</p>
            : <p><i>This incident was created before risk scoring existed, so it has no score.</i></p>}

          <ResponsePanel key={selected.id} incidentId={selected.id} onChange={refreshSelected} />

          <h4>Linked alerts ({selected.alerts.length})</h4>
          <table border="1" cellPadding="6" style={TABLE}>
            <thead>
              <tr>
                <th align="left">Rule</th><th align="left">Severity</th><th align="right">Events</th>
                <th align="left">Fired</th><th align="left">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {selected.alerts.map((a) => (
                <tr key={a.id}>
                  <td>{a.rule_id}</td>
                  <td>{a.severity}</td>
                  <td align="right">{a.event_count}</td>
                  <td>{fmt(a.created_at)}</td>
                  <td><a href={`/api/v1/alerts/${a.id}`} target="_blank" rel="noreferrer">alert and its events</a></td>
                </tr>
              ))}
            </tbody>
          </table>

          <h4>Timeline ({selected.timeline.length} entries)</h4>
          <table border="1" cellPadding="6" style={TABLE}>
            <thead>
              <tr>
                <th align="left">Time</th><th align="left">Type</th><th align="left">What happened</th>
                <th align="left">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {selected.timeline.map((entry, index) => (
                <tr key={index}>
                  <td style={{ whiteSpace: 'nowrap' }}>{fmtTime(entry.time)}</td>
                  <td>{entry.type}</td>
                  <td>{entry.description}</td>
                  <td>
                    <a href={entry.evidence.href} target="_blank" rel="noreferrer">{entry.evidence.kind}</a>
                    {entry.evidence.raw_event_reference && (
                      <><br /><code>{entry.evidence.raw_event_reference}</code></>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function score(value) {
  return value === null || value === undefined ? 'not scored' : `${value} / 100`
}

function fmt(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

// Timeline entries can be milliseconds apart, so show them.
function fmtTime(value) {
  const date = new Date(value)
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}.${String(date.getMilliseconds()).padStart(3, '0')}`
}
