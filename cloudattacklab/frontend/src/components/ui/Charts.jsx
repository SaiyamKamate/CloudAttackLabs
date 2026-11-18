import { SERIES_COLOURS } from '../../lib/colours.js'

// Small SVG/CSS charts. They only draw the numbers they are given; every
// caller computes those numbers from backend responses.

// Stacked vertical bars. buckets: [{ label, values: { series: count } }]
export function StackedBarChart({ buckets, series, height = 160 }) {
  const width = 640
  const pad = { top: 8, right: 8, bottom: 22, left: 28 }
  const innerW = width - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  const totals = buckets.map((b) => series.reduce((sum, s) => sum + (b.values[s] || 0), 0))
  const max = Math.max(1, ...totals)
  const step = innerW / Math.max(1, buckets.length)
  const barW = Math.max(3, Math.min(28, step * 0.7))
  const ticks = [0, Math.ceil(max / 2), max]
  const labelEvery = Math.ceil(buckets.length / 10)

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img">
      {ticks.map((t) => {
        const y = pad.top + innerH - (t / max) * innerH
        return (
          <g key={t}>
            <line className="gridline" x1={pad.left} x2={width - pad.right} y1={y} y2={y} />
            <text x={pad.left - 6} y={y + 3} textAnchor="end">{t}</text>
          </g>
        )
      })}
      {buckets.map((bucket, i) => {
        let y = pad.top + innerH
        const x = pad.left + i * step + (step - barW) / 2
        return (
          <g key={bucket.label + i}>
            <title>{`${bucket.label}: ${series.map((s) => `${bucket.values[s] || 0} ${s.replace('_', ' ')}`).join(', ')}`}</title>
            {series.map((s) => {
              const value = bucket.values[s] || 0
              if (!value) return null
              const h = (value / max) * innerH
              y -= h
              return <rect key={s} x={x} y={y} width={barW} height={Math.max(1, h - 1)} fill={SERIES_COLOURS[s] || 'var(--accent)'} rx="1" />
            })}
            {i % labelEvery === 0 && (
              <text x={x + barW / 2} y={height - 6} textAnchor="middle">{bucket.label}</text>
            )}
          </g>
        )
      })}
      <line className="axis" x1={pad.left} x2={width - pad.right} y1={pad.top + innerH} y2={pad.top + innerH} />
    </svg>
  )
}

export function Legend({ items }) {
  return (
    <div className="legend">
      {items.map((item) => (
        <span key={item.label} className="legend-item">
          <span className="legend-swatch" style={{ background: item.colour }} />
          {item.label}{item.value !== undefined && <span className="dim">{item.value}</span>}
        </span>
      ))}
    </div>
  )
}

// Horizontal bars. items: [{ label, value, colour }]
export function HBarList({ items, total }) {
  const max = Math.max(1, ...items.map((i) => i.value))
  return (
    <div>
      {items.map((item) => (
        <div className="hbar" key={item.label}>
          <span className="hbar-label" title={item.label}>{item.label}</span>
          <span className="hbar-track">
            <span className="hbar-fill" style={{ display: 'block', width: `${(item.value / max) * 100}%`, background: item.colour || 'var(--accent)' }} />
          </span>
          <span className="hbar-value">
            {item.value}
            {total ? <span className="dim small"> {Math.round((item.value / total) * 100)}%</span> : null}
          </span>
        </div>
      ))}
    </div>
  )
}

// A ring split into segments. segments: [{ label, value, colour }]
export function Donut({ segments, size = 120, label, sublabel }) {
  const total = segments.reduce((sum, s) => sum + s.value, 0)
  const r = size / 2 - 9
  const c = 2 * Math.PI * r
  let offset = 0
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-sunken)" strokeWidth="12" />
      {total > 0 && segments.map((segment) => {
        const length = (segment.value / total) * c
        const element = (
          <circle
            key={segment.label}
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={segment.colour}
            strokeWidth="12"
            strokeDasharray={`${Math.max(0, length - 1.5)} ${c}`}
            strokeDashoffset={-offset}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          >
            <title>{`${segment.label}: ${segment.value}`}</title>
          </circle>
        )
        offset += length
        return element
      })}
      <text x="50%" y="48%" textAnchor="middle" fill="var(--text-strong)" fontSize="20" fontWeight="600">{label ?? total}</text>
      {sublabel && <text x="50%" y="63%" textAnchor="middle" fill="var(--text-dim)" fontSize="10">{sublabel}</text>}
    </svg>
  )
}
