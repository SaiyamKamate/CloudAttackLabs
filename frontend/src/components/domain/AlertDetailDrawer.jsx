import { useState } from 'react'
import { FileText, ListTree, Siren } from 'lucide-react'
import { api } from '../../api.js'
import { useApi } from '../../lib/hooks.js'
import { fmt, fmtDuration, secondsBetween, shortId } from '../../lib/format.js'
import { href, navigate } from '../../lib/router.js'
import { countBy } from '../../lib/data.js'
import Drawer from '../ui/Drawer.jsx'
import { KeyValue, SectionTitle } from '../ui/Layout.jsx'
import { SeverityBadge } from '../ui/Badges.jsx'
import { ErrorState, LoadingState } from '../ui/States.jsx'
import EventTable from './EventTable.jsx'
import EventDetailDrawer from './EventDetailDrawer.jsx'
import ResponsePanel from './ResponsePanel.jsx'

// Investigate one alert (GET /alerts/{id}): the rule, the events that caused
// it, where they came from, and the incident and response it led to.
export default function AlertDetailDrawer({ alertId, onClose }) {
  const alert = useApi(() => api.getAlert(alertId), [alertId])
  const [eventId, setEventId] = useState(null)
  const a = alert.data

  const sources = a ? countBy(a.events, (e) => e.source) : {}
  const origins = a ? countBy(a.events, (e) => e.source_ip ?? (e.container_id ? `container ${e.container_id}` : null)) : {}

  return (
    <>
      <Drawer
        kicker={a ? `Alert · ${a.rule_id}` : 'Alert'}
        title={a?.title ?? 'Alert'}
        onClose={onClose}
        wide
        actions={a?.run_id && (
          <button className="btn btn-sm" onClick={() => navigate('reports', a.run_id)}><FileText size={13} /> Run report</button>
        )}
      >
        {alert.loading && <LoadingState />}
        {alert.error && !a && <ErrorState message={alert.error} onRetry={alert.reload} />}
        {a && (
          <div className="stack">
            <div className="row">
              <SeverityBadge severity={a.severity} />
              <code>{a.rule_id}</code>
              <span className="muted small">{a.event_count} event{a.event_count === 1 ? '' : 's'}</span>
            </div>
            <div>{a.description}</div>

            <div className="grid grid-2">
              <KeyValue rows={[
                ['Triggered rule', <code key="r">{a.rule_id}</code>],
                ['First event', fmt(a.first_event_at)],
                ['Last event', fmt(a.last_event_at)],
                ['Event span', fmtDuration(secondsBetween(a.first_event_at, a.last_event_at))],
              ]} />
              <KeyValue rows={[
                ['Alert raised', fmt(a.created_at)],
                ['Run', a.run_id ? <a key="r" href={href('runs', a.run_id)}>{shortId(a.run_id)}</a> : '—'],
                ['Incident', a.incident_id ? <a key="i" href={href('incidents', a.incident_id)}>{shortId(a.incident_id)}</a> : 'none'],
                ['Alert ID', <code key="id">{a.id}</code>],
              ]} />
            </div>

            <div>
              <SectionTitle icon={ListTree}>Source information</SectionTitle>
              <div className="row">
                {Object.entries(sources).map(([source, n]) => <span key={source} className="badge tone-neutral">{source}: {n}</span>)}
                {Object.entries(origins).map(([origin, n]) => <span key={origin} className="badge tone-neutral mono-badge">{origin} × {n}</span>)}
              </div>
            </div>

            <div>
              <SectionTitle icon={ListTree} right={<span className="dim small">click an event for its raw record</span>}>Supporting events ({a.events.length})</SectionTitle>
              <div className="panel">
                <EventTable events={a.events} onOpen={setEventId} pageSize={25} compact />
              </div>
            </div>

            {a.incident_id ? (
              <div>
                <SectionTitle icon={Siren} right={<a href={href('incidents', a.incident_id)} className="small">Open incident investigation</a>}>
                  Related incident and response
                </SectionTitle>
                <ResponsePanel key={a.incident_id} incidentId={a.incident_id} />
              </div>
            ) : <div className="muted">This alert is not linked to an incident.</div>}
          </div>
        )}
      </Drawer>
      {eventId && <EventDetailDrawer eventId={eventId} onClose={() => setEventId(null)} />}
    </>
  )
}
