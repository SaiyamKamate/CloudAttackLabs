import { useMemo, useState } from 'react'
import { Clock, RefreshCw, Search, ShieldCheck, ShieldX, XCircle, CheckCircle2, Zap, Undo2 } from 'lucide-react'
import { usePlatform } from '../lib/context.js'
import { useApi } from '../lib/hooks.js'
import { countBy, loadResponses } from '../lib/data.js'
import { responseStatusTone, responseTarget, responseVerification } from '../lib/describe.js'
import { fmt, shortId } from '../lib/format.js'
import { href } from '../lib/router.js'
import { MetricCard, PageHeader, Panel, Tabs } from '../components/ui/Layout.jsx'
import { StatusBadge } from '../components/ui/Badges.jsx'
import { EmptyState, ErrorState } from '../components/ui/States.jsx'
import DataTable from '../components/ui/DataTable.jsx'
import Drawer from '../components/ui/Drawer.jsx'
import ResponsePanel from '../components/domain/ResponsePanel.jsx'
import RiskBreakdown from '../components/domain/RiskBreakdown.jsx'

const GROUPS = [
  { key: 'all', label: 'All', statuses: null },
  { key: 'pending', label: 'Pending approval', statuses: ['pending_approval'], icon: Clock },
  { key: 'approved', label: 'Approved', statuses: ['approved'], icon: ShieldCheck },
  { key: 'executed', label: 'Executed', statuses: ['executed'], icon: Zap },
  { key: 'verified', label: 'Verified', statuses: ['verified'], icon: CheckCircle2 },
  { key: 'failed', label: 'Failed', statuses: ['failed'], icon: XCircle },
  { key: 'rejected', label: 'Rejected', statuses: ['rejected'], icon: ShieldX },
  { key: 'reverted', label: 'Reverted', statuses: ['reverted'], icon: Undo2 },
]

function approvalText(r) {
  if (r.status === 'pending_approval') return 'awaiting decision'
  if (r.status === 'rejected') return `rejected by ${r.decided_by ?? '—'}`
  return r.decided_by ? `approved by ${r.decided_by}` : '—'
}

// Every response action across incidents. There is no GET /responses; the
// list comes from each incident's detail (see lib/data.js). Decisions are made
// in the shared ResponsePanel, which confirms before calling the backend.
export default function ResponseCenterPage() {
  const { refreshAttention } = usePlatform()
  const responses = useApi(() => loadResponses(), [])
  const [group, setGroup] = useState('all')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState(null)

  const all = useMemo(() => responses.data ?? [], [responses.data])
  const byStatus = countBy(all, (r) => r.status)
  const statuses = GROUPS.find((g) => g.key === group)?.statuses
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return all.filter((r) =>
      (!statuses || statuses.includes(r.status)) &&
      (!term || [r.action, r.incident.title, r.id, r.incident_id].some((v) => v && String(v).toLowerCase().includes(term))),
    )
  }, [all, statuses, search])

  function close() {
    setSelected(null)
    responses.reload()
    refreshAttention()
  }

  return (
    <>
      <PageHeader
        title="Response Center"
        subtitle="Defensive response actions across all incidents. Nothing is changed in the lab until a response is approved; statuses are shown only as the backend reports them."
        actions={<button className="btn" onClick={responses.reload} disabled={responses.refreshing}><RefreshCw size={14} className={responses.refreshing ? 'spin' : ''} /> Refresh</button>}
      />

      <div className="grid grid-6" style={{ marginBottom: 16 }}>
        {GROUPS.slice(1, 7).map((g) => {
          const value = g.statuses.reduce((sum, s) => sum + (byStatus[s] || 0), 0)
          return (
            <MetricCard
              key={g.key}
              label={g.label}
              icon={g.icon}
              value={responses.data ? value : '…'}
              accent={g.key === 'pending' && value ? 'high' : g.key === 'failed' && value ? 'critical' : undefined}
              onClick={() => setGroup(g.key)}
            />
          )
        })}
      </div>

      <Panel flush>
        <div className="panel-body" style={{ paddingBottom: 0 }}>
          <Tabs
            active={group}
            onChange={setGroup}
            tabs={GROUPS.map((g) => ({ key: g.key, label: g.label, count: g.statuses ? g.statuses.reduce((s, st) => s + (byStatus[st] || 0), 0) : all.length }))}
          />
        </div>
        <div className="filters" style={{ borderTop: 0 }}>
          <div className="search">
            <Search size={14} />
            <input className="input" placeholder="Search action, incident, ID…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search responses" />
          </div>
          <span className="filter-count">{rows.length} of {all.length}</span>
        </div>
        {responses.error && !responses.data ? <ErrorState message={responses.error} onRetry={responses.reload} /> : (
          <DataTable
            loading={responses.loading}
            rows={rows}
            rowKey={(r) => r.id}
            onRowClick={setSelected}
            initialSort={{ key: 'requested', dir: 'desc' }}
            empty={<EmptyState compact icon={ShieldCheck} title={all.length ? 'No responses in this group' : 'No responses requested yet'} message={all.length ? undefined : 'Request a response from an incident.'} />}
            columns={[
              {
                key: 'incident',
                header: 'Incident',
                wrap: true,
                render: (r) => <><div className="cell-title">{r.incident.title}</div><div className="cell-sub mono">{shortId(r.incident_id)}</div></>,
                sortValue: (r) => r.incident.title,
              },
              { key: 'action', header: 'Action type', render: (r) => <code>{r.action}</code>, sortValue: (r) => r.action },
              { key: 'target', header: 'Target', render: (r) => <span className="small">{responseTarget(r)}</span> },
              { key: 'requested', header: 'Requested', render: (r) => <span className="nowrap">{fmt(r.requested_at)}</span>, sortValue: (r) => r.requested_at },
              { key: 'approval', header: 'Approval', render: (r) => <span className="small">{approvalText(r)}</span> },
              { key: 'executed', header: 'Executed', render: (r) => <span className="nowrap small">{r.executed_at ? fmt(r.executed_at) : '—'}</span>, sortValue: (r) => r.executed_at },
              { key: 'verification', header: 'Verification', render: (r) => { const v = responseVerification(r); return <StatusBadge tone={v.tone}>{v.label}</StatusBadge> } },
              { key: 'status', header: 'Status', render: (r) => <StatusBadge tone={responseStatusTone(r.status)}>{r.status}</StatusBadge>, sortValue: (r) => r.status },
            ]}
          />
        )}
      </Panel>

      {selected && (
        <Drawer kicker={`Response · ${selected.action}`} title={selected.incident.title} onClose={close}>
          <div className="stack">
            <div className="row">
              <a className="btn btn-sm" href={href('incidents', selected.incident_id)}>Open incident investigation</a>
              {selected.incident.run_id && <a className="btn btn-sm" href={href('runs', selected.incident.run_id)}>Open run</a>}
            </div>
            <ResponsePanel key={selected.id} incidentId={selected.incident_id} closed={selected.incident.status === 'closed'} onChange={refreshAttention} />
            <Panel title="Incident risk">
              <RiskBreakdown score={selected.incident.risk_score} explanation={selected.incident.risk_explanation} />
            </Panel>
            {selected.incident.responses.length > 1 && (
              <div className="small muted">
                This incident has {selected.incident.responses.length} responses; the panel shows the latest one, as the incident page does.
              </div>
            )}
          </div>
        </Drawer>
      )}
    </>
  )
}
