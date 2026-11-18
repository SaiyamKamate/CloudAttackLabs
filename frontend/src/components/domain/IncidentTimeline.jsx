import { useState } from 'react'
import {
  Activity, Ban, CheckCircle2, CirclePlay, CircleStop, ExternalLink, FileWarning, Lock, RotateCcw,
  ShieldAlert, ShieldCheck, ShieldQuestion, Siren, Trash2, XCircle, Zap,
} from 'lucide-react'
import { fmtClock, humanize } from '../../lib/format.js'

// Icon and style per timeline entry type (the backend's TimelineEntry.type).
const TYPES = {
  scenario_started: { icon: CirclePlay, cls: 'run', label: 'Scenario started' },
  scenario_finished: { icon: CircleStop, cls: 'run', label: 'Scenario finished' },
  event: { icon: Activity, cls: 'event', label: 'Telemetry event' },
  alert_fired: { icon: ShieldAlert, cls: 'alert', label: 'Alert fired' },
  incident_opened: { icon: Siren, cls: 'alert', label: 'Incident opened' },
  response_requested: { icon: ShieldQuestion, cls: 'response', label: 'Response requested' },
  response_approved: { icon: ShieldCheck, cls: 'response', label: 'Response approved' },
  response_rejected: { icon: Ban, cls: 'event', label: 'Response rejected' },
  response_executed: { icon: Zap, cls: 'response', label: 'Response executed' },
  response_verified: { icon: CheckCircle2, cls: 'ok', label: 'Response verified' },
  response_failed: { icon: XCircle, cls: 'crit', label: 'Response failed' },
  response_reverted: { icon: RotateCcw, cls: 'event', label: 'Response reverted' },
  cleanup: { icon: Trash2, cls: 'event', label: 'Cleanup' },
  incident_closed: { icon: Lock, cls: 'ok', label: 'Incident closed' },
}

// Collapse runs of consecutive telemetry events so the attack's shape stays readable.
function group(entries) {
  const out = []
  for (const entry of entries) {
    const last = out[out.length - 1]
    if (entry.type === 'event' && last && last.type === 'event-group') last.items.push(entry)
    else if (entry.type === 'event') out.push({ type: 'event-group', items: [entry] })
    else out.push(entry)
  }
  return out
}

function Entry({ entry, onEvidence }) {
  const meta = TYPES[entry.type] || { icon: FileWarning, cls: 'event', label: humanize(entry.type) }
  const Icon = meta.icon
  const evidence = entry.evidence
  return (
    <li className="timeline-item">
      <span className="tl-time" title={new Date(entry.time).toLocaleString()}>{fmtClock(entry.time)}</span>
      <span className={`tl-icon ${meta.cls}`}><Icon size={12} /></span>
      <div className="tl-body">
        <div className="tl-head"><span className="tl-type">{meta.label}</span></div>
        <div className="tl-desc">{entry.description}</div>
        {evidence && (
          <div className="tl-evidence">
            {['event', 'alert', 'run'].includes(evidence.kind) ? (
              <a href="#" onClick={(e) => { e.preventDefault(); onEvidence(evidence) }}>open {evidence.kind}</a>
            ) : <span>{evidence.kind}</span>}
            {evidence.source && <span className="badge tone-neutral" style={{ height: 17 }}>{evidence.source}</span>}
            {evidence.rule_id && <span className="mono">{evidence.rule_id}</span>}
            {evidence.raw_event_reference && <code>{evidence.raw_event_reference}</code>}
            {evidence.href && (
              <a href={evidence.href} target="_blank" rel="noreferrer" title="Open the evidence in the API" className="row" style={{ gap: 3 }}>
                API <ExternalLink size={10} />
              </a>
            )}
          </div>
        )}
      </div>
    </li>
  )
}

function EventGroup({ items, onEvidence }) {
  const [open, setOpen] = useState(items.length <= 3)
  if (open) return items.map((entry, i) => <Entry key={i} entry={entry} onEvidence={onEvidence} />)
  const first = items[0]
  const last = items[items.length - 1]
  const sources = [...new Set(items.map((e) => e.evidence?.source).filter(Boolean))]
  return (
    <>
      <Entry entry={first} onEvidence={onEvidence} />
      <li className="timeline-item">
        <span className="tl-time">…</span>
        <span className="tl-icon event"><Activity size={12} /></span>
        <div className="tl-body">
          <button className="tl-group-toggle" onClick={() => setOpen(true)}>
            Show {items.length - 2} more telemetry event{items.length - 2 === 1 ? '' : 's'}
            {sources.length > 0 && ` (${sources.join(', ')})`} until {fmtClock(last.time)}
          </button>
        </div>
      </li>
      <Entry entry={last} onEvidence={onEvidence} />
    </>
  )
}

// The incident's chronological timeline as the backend built it.
export default function IncidentTimeline({ entries, onEvidence }) {
  const grouped = group(entries)
  return (
    <ol className="timeline">
      {grouped.map((item, i) => item.type === 'event-group'
        ? <EventGroup key={`g${i}`} items={item.items} onEvidence={onEvidence} />
        : <Entry key={i} entry={item} onEvidence={onEvidence} />)}
    </ol>
  )
}
