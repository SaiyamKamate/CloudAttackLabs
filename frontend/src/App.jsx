import { useEffect, useState } from 'react'
import { useRoute, href } from './lib/router.js'
import { useStoredState } from './lib/hooks.js'
import { PAGE_LABELS } from './lib/nav.js'
import UIProvider from './components/ui/UIProvider.jsx'
import PlatformProvider from './components/layout/PlatformProvider.jsx'
import AppSidebar from './components/layout/AppSidebar.jsx'
import TopNavigation from './components/layout/TopNavigation.jsx'
import OverviewPage from './pages/OverviewPage.jsx'
import LabPage from './pages/LabPage.jsx'
import ScenariosPage from './pages/ScenariosPage.jsx'
import RunsPage from './pages/RunsPage.jsx'
import EventsPage from './pages/EventsPage.jsx'
import AlertsPage from './pages/AlertsPage.jsx'
import IncidentsPage from './pages/IncidentsPage.jsx'
import ResponseCenterPage from './pages/ResponseCenterPage.jsx'
import ReportsPage from './pages/ReportsPage.jsx'
import SettingsPage from './pages/SettingsPage.jsx'
import TargetsPage from './pages/TargetsPage.jsx'

const PAGES = {
  overview: OverviewPage,
  labs: LabPage,
  targets: TargetsPage,
  scenarios: ScenariosPage,
  runs: RunsPage,
  events: EventsPage,
  alerts: AlertsPage,
  incidents: IncidentsPage,
  responses: ResponseCenterPage,
  reports: ReportsPage,
  settings: SettingsPage,
}

// Detail views that replace the page get a breadcrumb back to their list.
const DETAIL_TITLES = { incidents: 'Incident investigation', runs: 'Run detail', reports: 'Run report' }

export default function App() {
  return (
    <UIProvider>
      <PlatformProvider>
        <Shell />
      </PlatformProvider>
    </UIProvider>
  )
}

function Shell() {
  const route = useRoute()
  const [collapsed, setCollapsed] = useStoredState('cal.sidebarCollapsed', false)
  const [mobileNav, setMobileNav] = useState(false)
  const page = PAGES[route.page] ? route.page : 'overview'
  const Page = PAGES[page]

  const detailTitle = route.id && DETAIL_TITLES[page]
  const title = detailTitle || PAGE_LABELS[page]
  const crumbs = detailTitle ? [{ label: PAGE_LABELS[page], href: href(page) }] : []

  useEffect(() => {
    document.title = `${title} · CloudAttackLab`
  }, [title])

  // Scroll to the top when the page (not just a drawer) changes.
  useEffect(() => {
    document.querySelector('.workspace')?.scrollTo(0, 0)
  }, [page, detailTitle])

  return (
    <div className={`app${collapsed ? ' sidebar-collapsed' : ''}${mobileNav ? ' mobile-nav-open' : ''}`}>
      <AppSidebar page={page} collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} onNavigate={() => setMobileNav(false)} />
      {mobileNav && <div className="backdrop mobile-only" style={{ zIndex: 54 }} onClick={() => setMobileNav(false)} />}
      <div className="main">
        <TopNavigation title={title} crumbs={crumbs} onOpenMobileNav={() => setMobileNav(true)} />
        <main className="workspace">
          <Page key={page} selectedId={route.id} sidebarCollapsed={collapsed} setSidebarCollapsed={setCollapsed} />
        </main>
      </div>
    </div>
  )
}
