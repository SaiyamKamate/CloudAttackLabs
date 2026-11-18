import { ArrowLeft, Download, FileJson, FileSpreadsheet, FileText } from 'lucide-react'
import { api } from '../api.js'
import { useApi } from '../lib/hooks.js'
import { responseResult, responseStatusTone, runStatusTone } from '../lib/describe.js'
import { fmt, humanize, score } from '../lib/format.js'
import { href } from '../lib/router.js'
import { Callout, JsonBlock, KeyValue, Panel } from '../components/ui/Layout.jsx'
import { SeverityBadge, StatusBadge } from '../components/ui/Badges.jsx'
import { ErrorState, LoadingState } from '../components/ui/States.jsx'

// The report for one run (GET /runs/{id}/report), laid out for reading, with
// a JSON download of exactly what the API returned.
export default function ReportPage({ runId, onClose, children }) {
  const state = useApi(() => api.getRunReport(runId), [runId])
  const report = state.data

  function download() {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `report-${report.scenario.id}-${report.run.id}.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  const back = (
    <button className="btn btn-ghost" onClick={onClose} style={{ marginBottom: 12, paddingLeft: 4 }}><ArrowLeft size={14} /> Back</button>
  )

  if (state.loading) return <>{back}<LoadingState label="Loading report…" /></>
  if (!report) return <>{back}<Panel><ErrorState message={state.error} onRetry={state.reload} /></Panel></>

  const { lab, scenario, run, telemetry, cleanup } = report
  const verification = report.post_run_verification

  return (
    <>
      {back}
      <div className="page-header">
        <div>
          <div className="row" style={{ marginBottom: 6 }}>
            <StatusBadge tone={runStatusTone(run.status)}>{run.status}</StatusBadge>
            <span className="dim small mono">{run.id}</span>
          </div>
          <h1>Run report: {scenario.name ?? scenario.id}</h1>
          <div className="subtitle">Generated {fmt(report.generated_at)} · report format {report.report_version}</div>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={download}><Download size={14} /> Download JSON</button>
          <button className="btn" disabled title="Not implemented: the backend only produces JSON reports"><FileText size={14} /> PDF</button>
          <button className="btn" disabled title="Not implemented: the backend only produces JSON reports"><FileSpreadsheet size={14} /> CSV</button>
        </div>
      </div>

      <div className="stack">
        {children}
        <div className="grid grid-2">
          <Panel title="Lab and scenario">
            <KeyValue rows={[
              ['Lab', lab ? `${lab.name} (${lab.environment}, target ${lab.target}, now ${lab.status})` : '—'],
              ['Scenario', <span key="s"><a href={href('scenarios', scenario.id)}>{scenario.id}</a>, version {scenario.version}</span>],
              ['Category', scenario.category ? humanize(scenario.category) : '—'],
              ['Description', scenario.description ?? 'The scenario definition is no longer available.'],
            ]} />
          </Panel>
          <Panel title="Run">
            <KeyValue rows={[
              ['Run ID', <code key="id">{run.id}</code>],
              ['Status', <StatusBadge key="st" tone={runStatusTone(run.status)}>{run.status}</StatusBadge>],
              ['Started', fmt(run.started_at)],
              ['Finished', fmt(run.finished_at)],
              ['Duration', run.duration_seconds === null ? '—' : `${run.duration_seconds} s`],
              ['Parameters', <code key="p">{JSON.stringify(run.parameters)}</code>],
              ['Error or reason it is not completed', run.error ?? 'none'],
            ]} />
          </Panel>
        </div>

        <Panel title="What the script reported">
          {report.script_summary
            ? <KeyValue rows={Object.entries(report.script_summary).map(([key, value]) => [key, typeof value === 'object' ? <code key={key}>{JSON.stringify(value)}</code> : String(value)])} />
            : <div className="muted">The script left no summary.</div>}
        </Panel>

        <Panel title="Telemetry: expected and collected" flush>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Source</th><th>Event type</th><th className="num">Expected at least</th><th className="num">Collected</th><th>Met</th></tr></thead>
              <tbody>
                {telemetry.expected.map((row) => (
                  <tr key={row.source + row.event_type}>
                    <td>{row.source}</td><td><code>{row.event_type}</code></td>
                    <td className="num">{row.expected_min_count}</td><td className="num">{row.collected_count}</td>
                    <td><StatusBadge tone={row.met ? 'ok' : 'crit'}>{row.met ? 'yes' : 'no'}</StatusBadge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-footer">
            {telemetry.collected_total} event(s) collected in total:{' '}
            {telemetry.collected_by_type.map((row) => `${row.count} ${row.event_type}`).join(', ') || 'none'}.
          </div>
        </Panel>

        <Panel title="Alerts and detection latency" flush>
          <div className="panel-body" style={{ paddingBottom: report.alerts.length ? 0 : undefined }}>
            {report.detection && report.detection.rule && (
              <p>Rule <code>{report.detection.rule}</code>: {report.detection.threshold} events within {report.detection.window_seconds} seconds.</p>
            )}
            {report.detection && !report.detection.rule && (
              <p>
                Observation run, checked against every rule:{' '}
                {(report.detection.rules || []).map((r) => `${r.rule} (${r.threshold} in ${r.window_seconds} s)`).join(', ')}.
                Any alert here was raised by traffic the platform did not send.
              </p>
            )}
            {report.alerts.length === 0 && <p className="muted">No alert was raised.</p>}
          </div>
          {report.alerts.length > 0 && (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Rule</th><th>Severity</th><th className="num">Events</th><th>Threshold crossed</th><th>Alert raised</th><th className="num">Threshold → alert</th></tr>
                </thead>
                <tbody>
                  {report.alerts.map((a) => (
                    <tr key={a.id}>
                      <td><a href={href('alerts', a.id)}><code>{a.rule_id}</code></a></td><td><SeverityBadge severity={a.severity} /></td>
                      <td className="num">{a.event_count}</td>
                      <td>{fmt(a.threshold_crossed_at)}</td><td>{fmt(a.created_at)}</td>
                      <td className="num">{a.detection_latency_seconds === null ? '—' : `${a.detection_latency_seconds} s`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="table-footer small">
            “Threshold → alert” is the time from the event that crossed the threshold to the alert. Detection runs once,
            when the scenario’s script ends (or an observation ends), so it mostly measures how much of the run was left
            after the threshold was crossed.
          </div>
        </Panel>

        <Panel title="Incident and risk score">
          {report.incidents.length === 0 && <div className="muted">No incident was opened.</div>}
          <div className="stack-sm">
            {report.incidents.map((incident) => (
              <div key={incident.id} className="callout" style={{ display: 'block' }}>
                <div className="row-between">
                  <a href={href('incidents', incident.id)} className="strong">{incident.title}</a>
                  <span className="small muted">status {incident.status} · {incident.alert_ids.length} alert(s) · risk <b>{score(incident.risk_score)}</b></span>
                </div>
                {incident.risk_explanation && <div className="pre-line small muted mt-8">{incident.risk_explanation}</div>}
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Response and verification">
          {report.responses.length === 0 && <div className="muted">No response action was requested.</div>}
          <div className="stack">
            {report.responses.map((r) => (
              <KeyValue key={r.id} rows={[
                ['Action', <span key="a"><code>{r.action}</code> <code>{JSON.stringify(r.parameters)}</code></span>],
                ['Status', <StatusBadge key="s" tone={responseStatusTone(r.status)}>{r.status}</StatusBadge>],
                ['Requested', fmt(r.requested_at)],
                ['Decided', r.decided_at ? `${fmt(r.decided_at)} by ${r.decided_by}` : '—'],
                ['Executed', fmt(r.executed_at)],
                ['Verified', fmt(r.verified_at)],
                ['Verification result', responseResult(r.verification_result)],
              ]} />
            ))}
          </div>
          {verification && (
            <div className="mt-8">
              <Callout tone={verification.passed ? 'ok' : 'crit'}>
                Post-run check <code>{verification.check}</code>: {verification.passed ? 'passed' : 'FAILED'}
                {verification.received?.status ? ` (the target answered ${verification.received.status})` : ''}
                {verification.error ? ` (${verification.error})` : ''}.
              </Callout>
            </div>
          )}
        </Panel>

        <Panel title="Cleanup" meta={<StatusBadge tone={cleanup.status === 'done' ? 'ok' : cleanup.status === 'partial' ? 'warn' : 'neutral'}>{cleanup.status}</StatusBadge>}>
          {cleanup.runs.length === 0 && <div className="muted">No cleanup has run for this run.</div>}
          {cleanup.runs.map((c, index) => (
            <p key={index}>
              {fmt(c.time)} ({c.trigger}): steps done: {c.steps_done.join(', ') || 'none'}
              {Object.keys(c.steps_failed).length > 0 && `; steps failed: ${Object.keys(c.steps_failed).join(', ')}`}
              {c.responses_reverted.length > 0 && `; ${c.responses_reverted.length} response(s) reverted`}.
            </p>
          ))}
        </Panel>

        <details>
          <summary className="small muted" style={{ cursor: 'pointer', marginBottom: 8 }}><FileJson size={12} style={{ verticalAlign: -2 }} /> Raw report JSON</summary>
          <JsonBlock value={report} maxHeight={520} />
        </details>
      </div>
    </>
  )
}
