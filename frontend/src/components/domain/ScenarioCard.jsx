import { Box, Eye, KeyRound, Lock } from 'lucide-react'
import { humanize } from '../../lib/format.js'

function ScenarioIcon({ category, size }) {
  switch (category) {
    case 'authentication': return <KeyRound size={size} />
    case 'authorization': return <Lock size={size} />
    case 'baseline': return <Eye size={size} />
    default: return <Box size={size} />
  }
}

export default function ScenarioCard({ scenario, selected, onOpen, availability }) {
  return (
    <button className={`scenario-card${selected ? ' selected' : ''}`} onClick={onOpen}>
      <div className="sc-head">
        <div className="sc-icon"><ScenarioIcon category={scenario.category} size={15} /></div>
        <div style={{ minWidth: 0 }}>
          <div className="sc-name">{scenario.name}</div>
          <div className="sc-id">{scenario.id} · v{scenario.version}</div>
        </div>
      </div>
      <div className="sc-desc">{scenario.description}</div>
      <div className="sc-meta">
        <span className="badge tone-neutral">{humanize(scenario.category)}</span>
        <span className="badge tone-neutral">{humanize(scenario.type)}</span>
        <span className="badge tone-neutral mono-badge">target: {scenario.target}</span>
        <span className={`badge tone-${availability.tone}`} style={{ marginLeft: 'auto' }}>{availability.label}</span>
      </div>
    </button>
  )
}
