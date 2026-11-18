import {
  Activity, BarChart3, Crosshair, FlaskConical, LayoutDashboard,
  Server, ShieldAlert, ShieldCheck, Siren,
} from 'lucide-react'

// The navigation, in sidebar order. Page keys are the hash routes.
export const NAV = [
  { section: 'Operations' },
  { key: 'overview', label: 'Overview', icon: LayoutDashboard },
  { key: 'labs', label: 'Labs & Environments', icon: Server },
  { section: 'Simulation' },
  { key: 'scenarios', label: 'Attack Scenarios', icon: Crosshair },
  { key: 'runs', label: 'Experiment Runs', icon: FlaskConical },
  { section: 'Detection' },
  { key: 'events', label: 'Events & Telemetry', icon: Activity },
  { key: 'alerts', label: 'Detection & Alerts', icon: ShieldAlert },
  { key: 'incidents', label: 'Incidents', icon: Siren },
  { section: 'Response' },
  { key: 'responses', label: 'Response Center', icon: ShieldCheck },
  { key: 'reports', label: 'Reports & Analytics', icon: BarChart3 },
]

export const PAGE_LABELS = Object.fromEntries(NAV.filter((n) => n.key).map((n) => [n.key, n.label]).concat([['settings', 'Settings']]))

export function backendTone(state) {
  return { online: 'ok', degraded: 'warn', offline: 'crit' }[state] || 'neutral'
}

export function backendLabel(state) {
  return { online: 'Backend connected', degraded: 'Database unavailable', offline: 'Backend offline', checking: 'Checking backend…' }[state]
}
