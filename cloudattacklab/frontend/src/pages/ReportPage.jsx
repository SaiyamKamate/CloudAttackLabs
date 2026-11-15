import { useEffect, useState } from 'react'
import { api } from '../api.js'

const TABLE = { borderCollapse: 'collapse', marginTop: '0.5rem' }

// The report for one run, laid out for reading, with a JSON download.
export default function ReportPage({ runId, onClose }) {
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let current = true
    api.getRunReport(runId)
      .then((data) => current && setReport(data))
      .catch((err) => current && setError(err.message))
    return () => { current = false }
  }, [runId])

  // The file is exactly what the API returned.
  function download() {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `report-${report.scenario.id}-${report.run.id}.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  if (error) {
    return <section><p style={{ color: 'crimson' }}>Error: {error}</p><button onClick={onClose}>Back</button></section>
  }
  if (!report) return <section><p>Loading the report…</p></section>

  const { lab, scenario, run, telemetry, cleanup } = report
  const verification = report.post_run_verification

  return (
    <section>
      <p>
        <button onClick={onClose}>Back</button>{' '}
        <button onClick={download}>Download as JSON</button>
      </p>
      <h2>Run report: {scenario.name ?? scenario.id}</h2>
      <p>Generated {fmt(report.generated_at)}. Report format {report.report_version}.</p>

      <h3>Lab and scenario</h3>
      <Facts rows={[
        ['Lab', lab ? `${lab.name} (${lab.environment}, target ${lab.target}, now ${lab.status})` : '—'],
        ['Scenario', `${scenario.id}, version ${scenario.version}`],
        ['Category', scenario.category ?? '—'],
        ['Description', scenario.description ?? 'The scenario definition is no longer available.'],
      ]} />

      <h3>Run</h3>
      <Facts rows={[
        ['Run id', <code key="id">{run.id}</code>],
        ['Status', run.status],
        ['Started', fmt(run.started_at)],
        ['Finished', fmt(run.finished_at)],
        ['Duration', run.duration_seconds === null ? '—' : `${run.duration_seconds} s`],
        ['Parameters', <code key="p">{JSON.stringify(run.parameters)}</code>],
        ['Error', run.error ?? 'none'],
      ]} />

      <h3>What the script reported</h3>
      {report.script_summary
        ? <Facts rows={Object.entries(report.script_summary).map(([key, value]) => [key, String(value)])} />
        : <p>The script left no summary.</p>}

      <h3>Telemetry: expected and collected</h3>
      <table border="1" cellPadding="6" style={TABLE}>
        <thead>
          <tr>
            <th align="left">Source</th><th align="left">Event type</th><th align="right">Expected at least</th>
            <th align="right">Collected</th><th align="left">Met</th>
          </tr>
        </thead>
        <tbody>
          {telemetry.expected.map((row) => (
            <tr key={row.source + row.event_type}>
              <td>{row.source}</td><td>{row.event_type}</td>
              <td align="right">{row.expected_min_count}</td><td align="right">{row.collected_count}</td>
              <td>{row.met ? 'yes' : 'NO'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        {telemetry.collected_total} event(s) collected in total:{' '}
        {telemetry.collected_by_type.map((row) => `${row.count} ${row.event_type}`).join(', ') || 'none'}.
      </p>

      <h3>Alerts and detection latency</h3>
      {report.detection && (
        <p>
          Rule <code>{report.detection.rule}</code>: {report.detection.threshold} events within{' '}
          {report.detection.window_seconds} seconds.
        </p>
      )}
      {report.alerts.length === 0 ? <p>No alert was raised.</p> : (
        <table border="1" cellPadding="6" style={TABLE}>
          <thead>
            <tr>
              <th align="left">Rule</th><th align="left">Severity</th><th align="right">Events</th>
              <th align="left">Threshold crossed</th><th align="left">Alert raised</th><th align="right">Latency</th>
            </tr>
          </thead>
          <tbody>
            {report.alerts.map((a) => (
              <tr key={a.id}>
                <td>{a.rule_id}</td><td>{a.severity}</td><td align="right">{a.event_count}</td>
                <td>{fmt(a.threshold_crossed_at)}</td><td>{fmt(a.created_at)}</td>
                <td align="right">{a.detection_latency_seconds === null ? '—' : `${a.detection_latency_seconds} s`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p>
        <small>
          Latency is the time from the event that crossed the threshold to the alert. Detection runs when the
          scenario's script ends, so the latency includes the rest of the run.
        </small>
      </p>

      <h3>Incident and risk score</h3>
      {report.incidents.length === 0 && <p>No incident was opened.</p>}
      {report.incidents.map((incident) => (
        <div key={incident.id} style={{ border: '1px solid #ccc', padding: '0.75rem', marginBottom: '0.5rem' }}>
          <p style={{ marginTop: 0 }}>
            <b>{incident.title}</b> — status {incident.status}, {incident.alert_ids.length} alert(s), risk score{' '}
            <b>{incident.risk_score ?? 'not scored'}</b>{incident.risk_score !== null && ' / 100'}
          </p>
          {incident.risk_explanation && <p style={{ whiteSpace: 'pre-line', marginBottom: 0 }}>{incident.risk_explanation}</p>}
        </div>
      ))}

      <h3>Response and verification</h3>
      {report.responses.length === 0 && <p>No response action was requested.</p>}
      {report.responses.map((r) => (
        <Facts key={r.id} rows={[
          ['Action', <span key="a">{r.action} <code>{JSON.stringify(r.parameters)}</code></span>],
          ['Status', r.status],
          ['Requested', fmt(r.requested_at)],
          ['Decided', r.decided_at ? `${fmt(r.decided_at)} by ${r.decided_by}` : '—'],
          ['Executed', fmt(r.executed_at)],
          ['Verified', fmt(r.verified_at)],
          ['Verification result', responseResult(r.verification_result)],
        ]} />
      ))}
      {verification && (
        <p>
          Post-run check <code>{verification.check}</code>: {verification.passed ? 'passed' : 'FAILED'}
          {verification.received?.status ? ` (the target answered ${verification.received.status})` : ''}
          {verification.error ? ` (${verification.error})` : ''}.
        </p>
      )}

      <h3>Cleanup</h3>
      <p>Status: <b>{cleanup.status}</b></p>
      {cleanup.runs.map((c, index) => (
        <p key={index}>
          {fmt(c.time)} ({c.trigger}): steps done: {c.steps_done.join(', ') || 'none'}
          {Object.keys(c.steps_failed).length > 0 && `; steps failed: ${Object.keys(c.steps_failed).join(', ')}`}
          {c.responses_reverted.length > 0 && `; ${c.responses_reverted.length} response(s) reverted`}.
        </p>
      ))}
    </section>
  )
}

function Facts({ rows }) {
  return (
    <table border="1" cellPadding="6" style={TABLE}>
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label}><th align="left">{label}</th><td>{value}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

function responseResult(result) {
  if (!result) return '—'
  if (result.received && result.sent) {
    return `${result.passed ? 'passed' : 'failed'}: ${result.received.blocked_429} of ${result.sent.attempts} follow-up logins were blocked with 429`
  }
  return result.passed ? 'passed' : `failed${result.error ? `: ${result.error}` : ''}`
}

function fmt(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}
