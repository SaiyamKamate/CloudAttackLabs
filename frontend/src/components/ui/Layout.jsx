import { AlertTriangle, CheckCircle2, Info, XOctagon } from 'lucide-react'

export function PageHeader({ title, subtitle, actions }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <div className="subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </div>
  )
}

export function Panel({ title, icon: Icon, meta, actions, flush = false, children, className = '', style }) {
  return (
    <section className={`panel ${className}`} style={style}>
      {(title || actions) && (
        <div className="panel-header">
          <h2>{Icon && <Icon size={14} className="muted" />}{title}{meta !== undefined && <span className="panel-meta">{meta}</span>}</h2>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      <div className={`panel-body${flush ? ' flush' : ''}`}>{children}</div>
    </section>
  )
}

export function MetricCard({ label, value, sub, icon: Icon, accent, onClick, unavailable = false }) {
  return (
    <div
      className={`metric${accent ? ` accent-${accent}` : ''}${onClick ? ' clickable' : ''}`}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === 'Enter') onClick() } : undefined}
    >
      <div className="metric-label">{Icon && <Icon size={13} />}{label}</div>
      <div className={`metric-value${unavailable ? ' unavailable' : ''}`}>{unavailable ? 'Unavailable' : value}</div>
      {sub && <div className="metric-sub">{sub}</div>}
    </div>
  )
}

const CALLOUT_ICONS = { crit: XOctagon, warn: AlertTriangle, info: Info, ok: CheckCircle2 }

export function Callout({ tone = 'info', title, children, action }) {
  const Icon = CALLOUT_ICONS[tone] || Info
  return (
    <div className={`callout ${tone}`}>
      <Icon size={15} />
      <div style={{ flex: 1, minWidth: 0 }}>
        {title && <div className="callout-title">{title}</div>}
        {children && <div>{children}</div>}
      </div>
      {action}
    </div>
  )
}

export function KeyValue({ rows }) {
  return (
    <dl className="kv">
      {rows.filter(Boolean).map(([label, value]) => (
        <div key={label} style={{ display: 'contents' }}>
          <dt>{label}</dt>
          <dd>{value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          role="tab"
          aria-selected={active === tab.key}
          className={`tab${active === tab.key ? ' active' : ''}`}
          onClick={() => onChange(tab.key)}
        >
          {tab.icon && <tab.icon size={13} />}
          {tab.label}
          {tab.count !== undefined && <span className="tab-count">{tab.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function Segmented({ options, value, onChange }) {
  return (
    <div className="seg" role="group">
      {options.map((option) => (
        <button key={option.value} className={value === option.value ? 'active' : ''} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function SectionTitle({ icon: Icon, children, right }) {
  return (
    <div className="row-between" style={{ marginBottom: 8 }}>
      <div className="section-title" style={{ marginBottom: 0 }}>{Icon && <Icon size={12} />}{children}</div>
      {right}
    </div>
  )
}

export function JsonBlock({ value, maxHeight }) {
  return <pre className="codeblock" style={maxHeight ? { maxHeight } : undefined}>{JSON.stringify(value, null, 2)}</pre>
}
