import { CheckCircle2, CircleDashed, ClipboardList, Play, Radar, ShieldCheck, Target, Trash2, XCircle } from 'lucide-react'
import { humanize } from '../../lib/format.js'
import { KeyValue, SectionTitle } from '../ui/Layout.jsx'
import { LoadingState, ErrorState } from '../ui/States.jsx'

function CheckIcon({ state }) {
  if (state === 'ok') return <CheckCircle2 size={14} className="check-ok" />
  if (state === 'fail') return <XCircle size={14} className="check-fail" />
  return <CircleDashed size={14} className="check-unknown" />
}

// The full scenario definition (GET /scenarios/{id}), the environment checks,
// the Run button and, when it belongs to this scenario, the current run.
export default function ScenarioDetailPanel({ summary, definition, definitionError, onRetry, readiness, busy, onRun, children }) {
  const d = definition
  const observation = summary.type === 'observation'

  return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>{summary.description}</p>

      <div className="grid grid-2">
        <KeyValue rows={[
          ['Scenario ID', <code key="id">{summary.id}</code>],
          ['Category', humanize(summary.category)],
          ['Type', humanize(summary.type)],
        ]} />
        <KeyValue rows={[
          ['Target', summary.target],
          ['Version', summary.version],
          ['Timeout', d?.execution?.timeout_seconds ? `${d.execution.timeout_seconds} s` : '—'],
        ]} />
      </div>

      <div className="panel">
        <div className="panel-header">
          <h2><ClipboardList size={14} className="muted" />Required environment status</h2>
          <button className="btn btn-primary" disabled={!readiness.canRun || Boolean(busy)} onClick={onRun} title={readiness.canRun ? undefined : readiness.reason}>
            <Play size={14} /> {busy === 'Run' ? 'Starting…' : 'Run scenario'}
          </button>
        </div>
        <div className="panel-body">
          <ul className="checklist">
            {readiness.checks.map((check) => (
              <li key={check.label}>
                <CheckIcon state={check.state} />
                <span style={{ minWidth: 0 }}>
                  <span>{check.label}</span>
                  <span className="dim small"> — {check.detail}</span>
                </span>
              </li>
            ))}
          </ul>
          <div className="small dim mt-8">
            The backend repeats these checks and runs the scenario’s preflight when the run is requested; it refuses the run if any fails.
          </div>
        </div>
      </div>

      {children}

      {!d && !definitionError && <LoadingState compact label="Loading scenario definition…" />}
      {definitionError && <ErrorState compact title="Could not load the scenario definition" message={definitionError} onRetry={onRetry} />}

      {d && (
        <>
          <div>
            <SectionTitle icon={Target}>Execution</SectionTitle>
            <KeyValue rows={[
              ['Script', d.execution?.script ? <code key="s">{d.execution.script}</code> : (observation ? 'none (observation only)' : '—')],
              ['Parameters', Object.keys(d.execution?.parameters ?? {}).length
                ? <code key="p">{JSON.stringify(d.execution.parameters)}</code>
                : 'none'],
              ['Requires', (d.requires ?? []).length ? d.requires.map(humanize).join(', ') : '—'],
            ]} />
            <div className="small dim mt-8">Parameters are fixed by the scenario file; the run request carries only the scenario ID.</div>
          </div>

          <div>
            <SectionTitle icon={Radar}>Expected security behaviour</SectionTitle>
            <div className="stack-sm">
              {(d.expected_telemetry ?? []).map((t) => (
                <div key={t.source + t.event_type} className="row">
                  <span className="badge tone-neutral">{t.source}</span>
                  <span>at least <b>{t.min_count}</b> <code>{t.event_type}</code> event{t.min_count > 1 ? 's' : ''} collected</span>
                </div>
              ))}
              {d.detection ? (
                <div className="row">
                  <span className="badge tone-info">detection</span>
                  <span>rule <code>{d.detection.rule}</code> fires at {d.detection.threshold} event{d.detection.threshold > 1 ? 's' : ''} within {d.detection.window_seconds} s</span>
                </div>
              ) : (
                <div className="row">
                  <span className="badge tone-neutral">detection</span>
                  <span>Every rule is checked; any alert raised by this run is a false positive.</span>
                </div>
              )}
            </div>
          </div>

          <div>
            <SectionTitle icon={ShieldCheck}>Response</SectionTitle>
            {d.response ? (
              <KeyValue rows={[
                ['Action', <code key="a">{d.response.action}</code>],
                ['Approval', d.response.requires_approval ? 'required before anything is changed' : 'not required'],
                ['Parameters', Object.keys(d.response.parameters ?? {}).length ? <code key="p">{JSON.stringify(d.response.parameters)}</code> : 'none'],
                ['Verification', d.response.verification ? `${d.response.verification.check}: ${d.response.verification.description ?? ''}` : '—'],
              ]} />
            ) : <div className="muted">No response action is defined for this scenario.</div>}
          </div>

          <div>
            <SectionTitle icon={Trash2}>Cleanup</SectionTitle>
            <div className="muted">
              {d.cleanup?.enabled ? `Steps: ${(d.cleanup.steps ?? []).map(humanize).join(', ')}` : 'No cleanup defined.'}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
