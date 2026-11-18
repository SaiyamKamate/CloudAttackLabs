import DataTable from '../ui/DataTable.jsx'
import { SeverityBadge } from '../ui/Badges.jsx'
import { EmptyState } from '../ui/States.jsx'
import { fmtTime } from '../../lib/format.js'

// The common event columns, shared by the explorer, alerts and incidents.
export default function EventTable({ events, onOpen, loading, pageSize = 50, empty, footer, compact = false, newestFirst = false }) {
  const columns = [
    { key: 'timestamp', header: 'Timestamp', render: (e) => <span className="mono small nowrap">{fmtTime(e.timestamp)}</span>, sortValue: (e) => e.timestamp },
    { key: 'source', header: 'Source', render: (e) => <span className="badge tone-neutral">{e.source}</span>, sortValue: (e) => e.source },
    { key: 'event_type', header: 'Event type', render: (e) => <code>{e.event_type}</code>, sortValue: (e) => e.event_type },
    { key: 'severity', header: 'Severity', render: (e) => <SeverityBadge severity={e.severity} />, sortValue: (e) => SEV_RANK[e.severity] ?? 0 },
    !compact && { key: 'resource', header: 'Resource', sortValue: (e) => e.resource },
    {
      key: 'origin',
      header: 'Source IP / container',
      render: (e) => <span className="mono small">{e.source_ip ?? (e.container_id ? `container ${e.container_id}` : '—')}</span>,
      sortValue: (e) => e.source_ip ?? e.container_id,
    },
    { key: 'rule_id', header: 'Falco rule', render: (e) => e.rule_id ?? <span className="dim">—</span> },
    { key: 'raw', header: 'Raw evidence reference', render: (e) => <code className="trunc" title={e.raw_event_reference}>{e.raw_event_reference}</code> },
  ].filter(Boolean)

  return (
    <DataTable
      columns={columns}
      rows={events}
      rowKey={(e) => e.event_id}
      onRowClick={onOpen ? (e) => onOpen(e.event_id) : undefined}
      loading={loading}
      pageSize={pageSize}
      empty={empty || <EmptyState compact title="No events" />}
      footer={footer}
      initialSort={newestFirst ? { key: 'timestamp', dir: 'desc' } : null}
    />
  )
}

const SEV_RANK = { info: 0, low: 1, medium: 2, high: 3, critical: 4 }
