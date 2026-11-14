import { useState } from 'react'
import LabPage from './pages/LabPage.jsx'
import ScenariosPage from './pages/ScenariosPage.jsx'
import AlertsPage from './pages/AlertsPage.jsx'

const PAGES = {
  lab: { label: 'Lab', Component: LabPage },
  scenarios: { label: 'Scenarios', Component: ScenariosPage },
  alerts: { label: 'Alerts', Component: AlertsPage },
}

export default function App() {
  const [page, setPage] = useState('lab')
  const Current = PAGES[page].Component

  return (
    <div style={{ maxWidth: 900, margin: '1rem auto', padding: '0 1rem', fontFamily: 'system-ui, sans-serif' }}>
      <h1>CloudAttackLab</h1>
      <nav style={{ marginBottom: '1rem', borderBottom: '1px solid #ccc', paddingBottom: '0.5rem' }}>
        {Object.entries(PAGES).map(([key, { label }]) => (
          <button
            key={key}
            onClick={() => setPage(key)}
            disabled={key === page}
            style={{ marginRight: '0.5rem', fontWeight: key === page ? 'bold' : 'normal' }}
          >
            {label}
          </button>
        ))}
      </nav>
      <Current />
    </div>
  )
}
