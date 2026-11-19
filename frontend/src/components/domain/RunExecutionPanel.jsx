import { CheckCircle2, FileText, Loader2, Trash2, XCircle } from 'lucide-react'
import { verificationText } from '../../lib/describe.js'
import { fmt, fmtDuration, secondsBetween } from '../../lib/format.js'
import { runStatusTone } from '../../lib/describe.js'
import { TERMINAL_RUN } from '../../lib/data.js'
import { Callout, JsonBlock, KeyValue } from '../ui/Layout.jsx'
import { StatusBadge } from '../ui/Badges.jsx'

const STEPS = ['pending', 'running', 'finished', 'cleaned_up']

function stepIndex(status) {
  if (status === 'pending') return 0
  if (status === 'running') return 1
  if (status === 'cleaned_up') return 3
  return 2
}

// The state of one run as GET /runs/{id} reports it, with Cleanup and View report.
// `cleanupBlockedBy` is another active run in the same lab, if any.
export default function RunExecutionPanel({ run, busy, onCleanup, onReport, cleanupBlockedBy }) {
  const running = !TERMINAL_RUN.includes(run.status)
  const verification = run.summary?.verification
  const summary = run.summary ? Object.fromEntries(Object.entries(run.summary).filter(([k]) => k !== 'verification')) : null
  const current = stepIndex(run.status)

  return (
    <div className="stack-sm">
      <div className="row-between">
        <div className="row">
          {running && <Loader2 size={14} style={{ animation: 'spin 0.9s linear infinite', color: 'var(--accent)' }} />}
          <span className="strong">{run.scenario_id}</span>
          <StatusBadge tone={runStatusTone(run.status)}>{run.status}</StatusBadge>
        </div>
        <span className="dim small mono">{run.id}</span>
      </div>

      <div className="phases" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
        {STEPS.map((step, i) => {
          const failed = i === 2 && ['failed', 'incomplete'].includes(run.status)
          const state = i < current || (i === current && !running) ? (failed ? 'fail' : 'done') : i === current ? 'pending' : 'none'
          const label = i === 2 && current >= 2 ? (run.status === 'cleaned_up' ? 'finished' : run.status) : step
          return (
            <div key={step} className={`phase ${state}`}>
              <div className="ph-step">Step {i + 1}</div>
              <div className="ph-value" style={{ textTransform: 'capitalize' }}>{label.replace('_', ' ')}</div>
            </div>
          )
        })}
      </div>
      {running && <div className="progress" />}

      <KeyValue rows={[
        ['Started', fmt(run.started_at)],
        ['Finished', fmt(run.finished_at)],
        ['Duration', fmtDuration(secondsBetween(run.started_at, run.finished_at))],
        ['Scenario version', run.scenario_version],
      ]} />

      {run.error && (
        <Callout tone={run.status === 'incomplete' ? 'warn' : 'crit'}>
          {run.error.startsWith('Run incomplete') ? run.error : `Run error: ${run.error}`}
        </Callout>
      )}

      {verification && (
        <div className="callout" style={{ alignItems: 'center' }}>
          {verification.passed ? <CheckCircle2 size={15} className="check-ok" /> : <XCircle size={15} className="check-fail" />}
          <span>{verificationText(verification)}</span>
        </div>
      )}

      {summary && Object.keys(summary).length > 0 && (
        <details>
          <summary className="small muted" style={{ cursor: 'pointer', marginBottom: 6 }}>Execution summary reported by the script</summary>
          <JsonBlock value={summary} maxHeight={240} />
        </details>
      )}

      <div className="row" style={{ marginTop: 4 }}>
        <button className="btn" onClick={onCleanup} disabled={Boolean(busy) || running || Boolean(cleanupBlockedBy)}>
          <Trash2 size={14} /> Cleanup
        </button>
        {!running && onReport && <button className="btn" onClick={onReport}><FileText size={14} /> View report</button>}
        {run.status === 'cleaned_up' && <span className="small muted">Cleanup done.</span>}
      </div>
      {cleanupBlockedBy && (
        <div className="small muted">
          Cleanup is disabled while run {cleanupBlockedBy.scenario_id} ({cleanupBlockedBy.status}) is in progress: cleanup resets ShopCloud and would break that run.
        </div>
      )}
    </div>
  )
}
