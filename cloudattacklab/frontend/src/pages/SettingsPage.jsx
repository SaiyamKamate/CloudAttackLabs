import { usePlatform } from '../lib/context.js'
import { backendLabel } from '../lib/nav.js'
import { falcoState, telemetryState } from '../lib/describe.js'
import { fmt } from '../lib/format.js'
import { Callout, KeyValue, PageHeader, Panel } from '../components/ui/Layout.jsx'

// Read-only platform information and the few preferences kept in this browser.
export default function SettingsPage({ sidebarCollapsed, setSidebarCollapsed }) {
  const { backend, labs, activeLab, labDetail, selectLab } = usePlatform()

  return (
    <>
      <PageHeader title="Settings" subtitle="Platform information and dashboard preferences. Backend settings are read from .env and cannot be changed here." />
      <div className="stack">
        <div className="grid grid-2">
          <Panel title="Backend">
            <KeyValue rows={[
              ['API base', <code key="b">/api/v1</code>],
              ['Connection', backendLabel(backend.state)],
              ['Last checked', backend.checkedAt ? fmt(backend.checkedAt) : '—'],
              ['Error', backend.error ?? 'none'],
              ['Authentication', 'none — every action is audited as “anonymous”'],
            ]} />
          </Panel>
          <Panel title="Active lab configuration">
            <KeyValue rows={[
              ['Lab', activeLab ? `${activeLab.name} (${activeLab.status})` : 'none'],
              ['Labs', labs ? labs.length : '—'],
              ['SHOPCLOUD_URL', labDetail?.target_url?.url ?? '—'],
              ['Telemetry source', labDetail ? telemetryState(labDetail.telemetry).detail : '—'],
              ['Falco', labDetail ? falcoState(labDetail.falco).detail : '—'],
            ]} />
          </Panel>
        </div>
        <Panel title="Dashboard preferences">
          <div className="stack-sm">
            <label className="row">
              <input type="checkbox" checked={sidebarCollapsed} onChange={(e) => setSidebarCollapsed(e.target.checked)} />
              Collapse the sidebar
            </label>
            <div className="row">
              <button className="btn btn-sm" onClick={() => selectLab(null)}>Forget the selected lab</button>
              <span className="small muted">The dashboard then picks a running lab, or the first lab.</span>
            </div>
          </div>
        </Panel>
        <Callout tone="info" title="Not implemented">
          User accounts, roles and PDF/CSV export are not part of the backend. These are listed here so they are not mistaken for working features.
        </Callout>
      </div>
    </>
  )
}
