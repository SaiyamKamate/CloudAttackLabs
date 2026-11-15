import { useState } from 'react'
import LabPage from './pages/LabPage.jsx'
import ScenariosPage from './pages/ScenariosPage.jsx'
import AlertsPage from './pages/AlertsPage.jsx'
import IncidentsPage from './pages/IncidentsPage.jsx'
import ReportPage from './pages/ReportPage.jsx'

const PAGES = {
  lab: { label: 'Lab', Component: LabPage },
  scenarios: { label: 'Scenarios', Component: ScenariosPage },
  alerts: { label: 'Alerts', Component: AlertsPage },
  incidents: { label: 'Incidents', Component: IncidentsPage },
}

export default function App() {
  const [page, setPage] = useState('lab')
  // The run started on the Scenarios page. Kept here so it (and its Cleanup
  // button) is still there after a visit to another page.
  const [run, setRun] = useState(null)
  // The run whose report is open. While set, the report replaces the page.
  const [reportRunId, setReportRunId] = useState(null)
  const Current = PAGES[page].Component

  return (
    <div style={{ maxWidth: 900, margin: '1rem auto', padding: '0 1rem', fontFamily: 'system-ui, sans-serif' }}>
      <h1>CloudAttackLab</h1>
      <nav style={{ marginBottom: '1rem', borderBottom: '1px solid #ccc', paddingBottom: '0.5rem' }}>
        {Object.entries(PAGES).map(([key, { label }]) => (
          <button
            key={key}
            onClick={() => { setPage(key); setReportRunId(null) }}
            disabled={key === page && !reportRunId}
            style={{ marginRight: '0.5rem', fontWeight: key === page ? 'bold' : 'normal' }}
          >
            {label}
          </button>
        ))}
      </nav>
      {reportRunId
        ? <ReportPage runId={reportRunId} onClose={() => setReportRunId(null)} />
        : <Current run={run} setRun={setRun} openReport={setReportRunId} />}
    </div>
  )
}
