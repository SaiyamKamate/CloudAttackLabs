import { PanelLeftClose, PanelLeftOpen, Settings, Shield, User } from 'lucide-react'
import { NAV, backendLabel, backendTone } from '../../lib/nav.js'
import { href } from '../../lib/router.js'
import { usePlatform } from '../../lib/context.js'
import { StatusIndicator } from '../ui/Badges.jsx'

export default function AppSidebar({ page, collapsed, onToggle, onNavigate }) {
  const { backend, attention, runActive } = usePlatform()
  const counts = {
    incidents: attention.openIncidents ? { value: attention.openIncidents, attn: false } : null,
    responses: attention.pending.length ? { value: attention.pending.length, attn: true } : null,
    scenarios: runActive ? { value: 'live', attn: false } : null,
  }

  return (
    <aside className="sidebar" aria-label="Main navigation">
      <div className="sidebar-brand">
        <div className="brand-mark"><Shield size={14} /></div>
        <div className="brand-text">
          <div className="brand-name">CloudAttackLab</div>
          <div className="brand-sub">Security operations</div>
        </div>
      </div>

      <nav className="sidebar-nav">
        {NAV.map((item) => item.section
          ? <div key={item.section} className="nav-section-label">{item.section}</div>
          : (
            <a
              key={item.key}
              href={href(item.key)}
              className={`nav-item${page === item.key ? ' active' : ''}`}
              title={collapsed ? item.label : undefined}
              onClick={onNavigate}
              aria-current={page === item.key ? 'page' : undefined}
            >
              <item.icon size={16} />
              <span className="nav-label">{item.label}</span>
              {counts[item.key] && (
                <span className={`nav-count${counts[item.key].attn ? ' attn' : ''}`}>{counts[item.key].value}</span>
              )}
            </a>
          ))}
      </nav>

      <div className="sidebar-footer">
        <a
          href={href('settings')}
          className={`nav-item${page === 'settings' ? ' active' : ''}`}
          title={collapsed ? 'Settings' : undefined}
          onClick={onNavigate}
        >
          <Settings size={16} />
          <span className="nav-label">Settings</span>
        </a>
        <div className="nav-item" style={{ cursor: 'default' }} title={backendLabel(backend.state)}>
          <span style={{ width: 16, display: 'grid', placeItems: 'center' }}>
            <StatusIndicator tone={backendTone(backend.state)} pulse={backend.state === 'checking'} />
          </span>
          <span className="nav-label small">{backendLabel(backend.state)}</span>
        </div>
        <div className="sidebar-user">
          <div className="avatar"><User size={13} /></div>
          <div className="sidebar-user-text" style={{ minWidth: 0 }}>
            <div className="small" style={{ color: 'var(--text)' }}>anonymous</div>
            <div className="dim" style={{ fontSize: 11 }}>Local lab operator</div>
          </div>
        </div>
        <button className="nav-item desktop-only" onClick={onToggle} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          <span className="nav-label">Collapse</span>
        </button>
      </div>
    </aside>
  )
}
