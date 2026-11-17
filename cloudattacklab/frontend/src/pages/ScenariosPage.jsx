import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

const TERMINAL = ['completed', 'failed', 'cleaned_up']

// List scenarios and run one against the running lab, then follow the run.
export default function ScenariosPage({ run, setRun, openReport }) {
  const [scenarios, setScenarios] = useState([])
  const [lab, setLab] = useState(null)   // a running lab, if there is one
  const [activeRun, setActiveRun] = useState(null) // the lab's unfinished run, from the backend
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const pollRef = useRef(null)

  async function load() {
    try {
      const [list, labs] = await Promise.all([api.listScenarios(), api.listLabs()])
      const running = labs.find((l) => l.status === 'running') || null
      setScenarios(list)
      setLab(running)
      setActiveRun(running ? (await api.getLab(running.id)).active_run : null)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    // Fetch on mount. State is set after the await, not synchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
    // Coming back to this page: pick the run up again where it is now.
    if (run) follow(run.id)
    return () => clearInterval(pollRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Poll the run until it reaches a terminal status.
  function follow(runId) {
    clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const latest = await api.getRun(runId)
        setRun(latest)
        if (TERMINAL.includes(latest.status)) {
          clearInterval(pollRef.current)
          load() // the lab has no active run any more
        }
      } catch (err) {
        setError(err.message)
        clearInterval(pollRef.current)
      }
    }, 1500)
  }

  async function runScenario(scenarioId) {
    if (!lab) return
    setBusy(true)
    setError(null)
    try {
      const started = await api.startRun(lab.id, scenarioId)
      setRun(started)
      follow(started.id)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function cleanup() {
    setBusy(true)
    setError(null)
    try {
      setRun(await api.cleanupRun(run.id))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const running = run && !TERMINAL.includes(run.status)
  // Another run (not the one shown here) is still active in the lab.
  const otherActive = activeRun && (!run || activeRun.id !== run.id) ? activeRun : null

  return (
    <section>
      <h2>Scenarios</h2>
      {error && <p style={{ color: 'crimson' }}>Error: {error}</p>}
      {!lab && <p><i>The lab is not running. Start it on the Lab page before running a scenario.</i></p>}

      <table border="1" cellPadding="6" style={{ borderCollapse: 'collapse' }}>
        <thead>
          <tr><th align="left">Scenario</th><th align="left">Category</th><th align="left">Target</th><th></th></tr>
        </thead>
        <tbody>
          {scenarios.map((s) => (
            <tr key={s.id}>
              <td>{s.name}<br /><code>{s.id}</code></td>
              <td>{s.category}</td>
              <td>{s.target}</td>
              <td><button onClick={() => runScenario(s.id)} disabled={!lab || busy || running || Boolean(otherActive)}>Run</button></td>
            </tr>
          ))}
          {scenarios.length === 0 && <tr><td colSpan="4">No scenarios.</td></tr>}
        </tbody>
      </table>

      {run && (
        <div style={{ marginTop: '1rem' }}>
          <h3>Run {run.scenario_id}</h3>
          <p>Status: <b>{run.status}</b> {running && '(updating…)'}</p>
          {run.error && <p style={{ color: 'crimson' }}>Run error: {run.error}</p>}
          {run.summary?.verification && <p>{verificationText(run.summary.verification)}</p>}
          {run.summary && <pre>{JSON.stringify(run.summary, null, 2)}</pre>}
          <button onClick={cleanup} disabled={busy || running || Boolean(otherActive)}>Cleanup</button>{' '}
          {!running && <button onClick={() => openReport(run.id)}>View report</button>}
          {otherActive && (
            <p><i>
              Cleanup is disabled while run {otherActive.scenario_id} ({otherActive.status}) is in progress:
              cleanup resets ShopCloud and would break that run.
            </i></p>
          )}
        </div>
      )}
    </section>
  )
}

// The check the engine repeated after the run, in plain words. Works for any
// check; the "still denied" wording is used when the result carries that field.
function verificationText(v) {
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
