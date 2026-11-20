import { Activity, Boxes, Database, Eye, Globe, Server, ShoppingCart } from 'lucide-react'
import { StatusIndicator } from '../ui/Badges.jsx'
import { containerTone, falcoState, isUnusedFalco, labStatusTone, targetHealthState, telemetryState } from '../../lib/describe.js'
import { backendLabel, backendTone } from '../../lib/nav.js'
import { humanize } from '../../lib/format.js'

function Tile({ icon: Icon, label, tone, value, detail }) {
  return (
    <div className="health-tile">
      <div className="ht-label"><Icon size={12} />{label}</div>
      <div className="ht-value"><StatusIndicator tone={tone} label={value} /></div>
      <div className="ht-detail" title={typeof detail === 'string' ? detail : undefined}>{detail}</div>
    </div>
  )
}

// One tile per health signal the backend reports for a lab. Every value comes
// from GET /labs/{id} (and GET /health for the backend tile).
export default function HealthStatusPanel({ lab, detail, backend }) {
  const allContainers = detail?.containers ?? []
  const containers = allContainers.filter((c) => !isUnusedFalco(c, detail?.falco))
  const skipped = allContainers.length - containers.length
  const healthyContainers = containers.filter((c) => containerTone(c) === 'ok').length
  const target = targetHealthState(detail?.target_health)
  const falco = falcoState(detail?.falco)
  const external = lab?.environment === 'external'
  // A registered target's telemetry is its signed event feed, whatever TELEMETRY_SOURCE says.
  const telemetry = external
    ? { tone: 'ok', label: 'integration feed', detail: 'The target\u2019s signed security-event feed (checked in Target Management)' }
    : telemetryState(detail?.telemetry)
  const status = detail?.status ?? lab?.status

  return (
    <div className="health-grid">
      {backend && (
        <Tile
          icon={Database}
          label="Backend API"
          tone={backendTone(backend.state)}
          value={backend.state}
          detail={backend.error || backendLabel(backend.state)}
        />
      )}
      <Tile
        icon={Server}
        label="Lab"
        tone={lab ? labStatusTone(status) : 'neutral'}
        value={lab ? humanize(status) : 'no lab'}
        detail={lab ? (detail?.status_detail || `${lab.name} · ${lab.environment}`) : 'Create a lab to begin'}
      />
      <Tile icon={ShoppingCart} label="ShopCloud" tone={detail ? target.tone : 'neutral'} value={detail ? target.label : '—'} detail={detail ? target.detail : 'No data'} />
      <Tile
        icon={Boxes}
        label="Containers"
        tone={!detail ? 'neutral' : containers.length === 0 ? 'neutral' : healthyContainers === containers.length ? 'ok' : 'warn'}
        value={detail ? `${healthyContainers}/${containers.length} up` : '—'}
        detail={detail ? (containers.length ? `${containers.map((c) => c.service).join(', ')}${skipped ? ' · falco not used' : ''}` : 'No containers') : 'No data'}
      />
      <Tile icon={Eye} label="Falco" tone={detail ? falco.tone : 'neutral'} value={detail ? falco.label : '—'} detail={detail ? falco.detail : 'No data'} />
      <Tile icon={Activity} label="Telemetry source" tone={detail ? telemetry.tone : 'neutral'} value={detail ? telemetry.label : '—'} detail={detail ? telemetry.detail : 'No data'} />
      {detail?.target_url && (
        <Tile
          icon={Globe}
          label="Target URL"
          tone={detail.target_url.allowed ? 'ok' : 'crit'}
          value={detail.target_url.allowed ? 'allowed' : 'refused'}
          detail={detail.target_url.url}
        />
      )}
    </div>
  )
}
