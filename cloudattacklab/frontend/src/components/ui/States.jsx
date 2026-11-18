import { AlertOctagon, Inbox, Loader2, RefreshCw } from 'lucide-react'

export function LoadingState({ label = 'Loading…', compact = false }) {
  return (
    <div className={`state${compact ? ' compact' : ''}`}>
      <Loader2 size={18} className="spin" style={{ animation: 'spin 0.9s linear infinite' }} />
      <span>{label}</span>
    </div>
  )
}

export function ErrorState({ title = 'Could not load data', message, onRetry, compact = false }) {
  return (
    <div className={`state error${compact ? ' compact' : ''}`}>
      <AlertOctagon size={20} />
      <div className="state-title">{title}</div>
      {message && <div className="small" style={{ maxWidth: 520 }}>{message}</div>}
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}><RefreshCw size={13} /> Retry</button>
      )}
    </div>
  )
}

export function EmptyState({ title = 'Nothing here yet', message, icon: Icon = Inbox, action, compact = false }) {
  return (
    <div className={`state${compact ? ' compact' : ''}`}>
      <Icon size={20} />
      <div className="state-title">{title}</div>
      {message && <div className="small" style={{ maxWidth: 520 }}>{message}</div>}
      {action}
    </div>
  )
}

export function SkeletonRows({ rows = 5 }) {
  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - (i % 3) * 15}%` }} />
      ))}
    </div>
  )
}
