import { humanize } from '../../lib/format.js'

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info']

export function SeverityBadge({ severity }) {
  const value = SEVERITIES.includes(severity) ? severity : 'neutral'
  return <span className={`badge sev-${value}`}>{severity || 'unknown'}</span>
}

// A coloured badge for a status. tone: ok | warn | crit | info | neutral
export function StatusBadge({ tone = 'neutral', children, mono = false }) {
  return <span className={`badge tone-${tone}${mono ? ' mono-badge' : ''}`}>{typeof children === 'string' ? humanize(children) : children}</span>
}

// A dot and a label, for health.
export function StatusIndicator({ tone = 'neutral', label, pulse = false }) {
  return (
    <span className="status-ind">
      <span className={`dot ${tone}${pulse ? ' pulse' : ''}`} />
      {label}
    </span>
  )
}

export function SeverityBar({ severity }) {
  const colour = { critical: 'var(--critical)', high: 'var(--high)', medium: 'var(--medium)', low: 'var(--low)' }[severity] || 'var(--text-dim)'
  return <span className="sev-bar" style={{ background: colour }} />
}
