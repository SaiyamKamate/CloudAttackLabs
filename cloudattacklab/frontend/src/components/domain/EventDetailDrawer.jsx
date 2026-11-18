import { api } from '../../api.js'
import { useApi } from '../../lib/hooks.js'
import { fmtTime, shortId } from '../../lib/format.js'
import { href } from '../../lib/router.js'
import Drawer from '../ui/Drawer.jsx'
import { JsonBlock, KeyValue, SectionTitle } from '../ui/Layout.jsx'
import { SeverityBadge } from '../ui/Badges.jsx'
import { ErrorState, LoadingState } from '../ui/States.jsx'

// One normalized event and its raw record (GET /events/{id}).
export default function EventDetailDrawer({ eventId, onClose }) {
  const event = useApi(() => api.getEvent(eventId), [eventId])
  const e = event.data

  return (
    <Drawer kicker="Event" title={e ? `${e.event_type} · ${e.source}` : 'Event'} onClose={onClose}>
      {event.loading && <LoadingState />}
      {event.error && !e && <ErrorState message={event.error} onRetry={event.reload} />}
      {e && (
        <div className="stack">
          <div className="row">
            <SeverityBadge severity={e.severity} />
            <span className="badge tone-neutral">{e.source}</span>
            <span className="badge tone-neutral mono-badge">{e.event_type}</span>
          </div>
          <div>{e.message}</div>
          <KeyValue rows={[
            ['Timestamp', <span key="t" className="mono">{fmtTime(e.timestamp)}</span>],
            ['Event ID', <code key="i">{e.event_id}</code>],
            ['Resource', e.resource],
            ['Source IP', e.source_ip ?? '—'],
            ['Container', e.container_id ? <code key="c">{e.container_id}</code> : '—'],
            ['Username', e.username ?? '—'],
            ['Falco rule', e.rule_id ?? '—'],
            ['Scenario', e.scenario_id ?? '—'],
            ['Run', e.run_id ? <a key="r" href={href('runs', e.run_id)} onClick={onClose}>{shortId(e.run_id)}</a> : '—'],
            ['Correlation key', e.correlation_key ? <code key="k">{e.correlation_key}</code> : '—'],
            ['Raw event reference', <code key="raw">{e.raw_event_reference}</code>],
            ['Schema version', e.schema_version],
          ]} />
          <div>
            <SectionTitle>Raw record</SectionTitle>
            {e.raw ? <JsonBlock value={e.raw} maxHeight={360} /> : <div className="muted">The backend kept no raw record for this event.</div>}
          </div>
        </div>
      )}
    </Drawer>
  )
}
