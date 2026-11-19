import { useEffect, useRef, useState } from 'react'
import { Bell, ChevronDown, ChevronRight, Menu, RefreshCw, Server, ShieldCheck, Siren, User } from 'lucide-react'
import { usePlatform } from '../../lib/context.js'
import { backendLabel, backendTone } from '../../lib/nav.js'
import { href, navigate } from '../../lib/router.js'
import { labStatusTone } from '../../lib/describe.js'
import { humanize, shortId } from '../../lib/format.js'
import { StatusIndicator } from '../ui/Badges.jsx'

// Close a popover on outside click or Escape.
function useDismiss(open, setOpen) {
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open, setOpen])
  return ref
}

export function EnvironmentSelector() {
  const { labs, activeLab, selectLab, labDetail } = usePlatform()
  const [open, setOpen] = useState(false)
  const ref = useDismiss(open, setOpen)

  if (!labs) return <span className="conn"><Server size={13} /> <span className="env-select-label">Loading labs…</span></span>
  if (labs.length === 0) {
    return <a className="conn" href={href('labs')}><Server size={13} /> <span className="env-select-label">No lab — create one</span></a>
  }
  const status = labDetail?.status ?? activeLab?.status

  return (
    <div className="popover-anchor" ref={ref}>
      <button className="btn" onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open} title="Active lab">
        <StatusIndicator tone={labStatusTone(status)} />
        <span className="env-select-label dim">Lab</span>
        <span className="strong" style={{ fontWeight: 500 }}>{activeLab.name}</span>
        <ChevronDown size={13} className="muted" />
      </button>
      {open && (
        <div className="popover" role="listbox" style={{ left: 0, right: 'auto' }}>
          <div className="popover-header">Active lab</div>
          {labs.map((lab) => (
            <button
              key={lab.id}
              role="option"
              aria-selected={lab.id === activeLab.id}
              className={`menu-item${lab.id === activeLab.id ? ' selected' : ''}`}
              onClick={() => { selectLab(lab.id); setOpen(false) }}
            >
              <span style={{ paddingTop: 4 }}><StatusIndicator tone={labStatusTone(lab.status)} /></span>
              <span style={{ minWidth: 0 }}>
                <div>{lab.name}</div>
                <div className="menu-sub">{humanize(lab.status)} · {lab.environment} · {lab.target} · {shortId(lab.id)}</div>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Notifications() {
  const { attention, refreshAttention, run, runActive } = usePlatform()
  const [open, setOpen] = useState(false)
  const ref = useDismiss(open, setOpen)
  const count = attention.pending.length + (attention.openIncidents || 0)

  const go = (page, id) => { setOpen(false); navigate(page, id) }

  return (
    <div className="popover-anchor" ref={ref}>
      <button className="icon-btn" onClick={() => { setOpen((o) => !o); if (!open) refreshAttention() }} aria-label="Notifications" title="Notifications">
        <Bell size={16} />
        {count > 0 && <span className="pip">{count > 99 ? '99+' : count}</span>}
      </button>
      {open && (
        <div className="popover" style={{ width: 340 }}>
          <div className="popover-header">Needs attention</div>
          {runActive && (
            <button className="menu-item" onClick={() => go('scenarios', run.scenario_id)}>
              <RefreshCw size={14} className="spin" style={{ marginTop: 2, color: 'var(--accent)', animation: 'spin 1.2s linear infinite' }} />
              <span><div>Run in progress</div><div className="menu-sub">{run.scenario_id} · {run.status}</div></span>
            </button>
          )}
          {attention.pending.map((response) => (
            <button key={response.id} className="menu-item" onClick={() => go('incidents', response.incident_id)}>
              <ShieldCheck size={14} style={{ marginTop: 2, color: 'var(--medium)' }} />
              <span><div>Response awaiting approval</div><div className="menu-sub">{humanize(response.action)} · incident {shortId(response.incident_id)}</div></span>
            </button>
          ))}
          {attention.openIncidents > 0 && (
            <button className="menu-item" onClick={() => go('incidents')}>
              <Siren size={14} style={{ marginTop: 2, color: 'var(--high)' }} />
              <span><div>{attention.openIncidents} open incident{attention.openIncidents > 1 ? 's' : ''}</div><div className="menu-sub">Review on the Incidents page</div></span>
            </button>
          )}
          {count === 0 && !runActive && <div className="menu-item muted" style={{ cursor: 'default' }}>Nothing needs attention.</div>}
        </div>
      )}
    </div>
  )
}

function ProfileMenu() {
  const [open, setOpen] = useState(false)
  const ref = useDismiss(open, setOpen)
  return (
    <div className="popover-anchor" ref={ref}>
      <button className="icon-btn" onClick={() => setOpen((o) => !o)} aria-label="User menu" title="User">
        <span className="avatar" style={{ width: 24, height: 24 }}><User size={12} /></span>
      </button>
      {open && (
        <div className="popover" style={{ width: 280 }}>
          <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)' }}>
            <div className="strong">anonymous</div>
            <div className="small muted">The backend has no login. Every action is audited with actor “anonymous”.</div>
          </div>
          <button className="menu-item" onClick={() => { setOpen(false); navigate('settings') }}>Settings and platform info</button>
        </div>
      )}
    </div>
  )
}

export default function TopNavigation({ title, crumbs = [], onOpenMobileNav }) {
  const { backend } = usePlatform()
  return (
    <header className="topbar">
      <button className="icon-btn mobile-only" onClick={onOpenMobileNav} aria-label="Open navigation"><Menu size={16} /></button>
      <div className="topbar-title">
        {crumbs.length > 0 && (
          <div className="breadcrumbs">
            {crumbs.map((crumb, i) => (
              <span key={i} className="row" style={{ gap: 4, flexWrap: 'nowrap' }}>
                {crumb.href ? <a href={crumb.href}>{crumb.label}</a> : <span>{crumb.label}</span>}
                <ChevronRight size={11} />
              </span>
            ))}
          </div>
        )}
        <h1>{title}</h1>
      </div>
      <div className="topbar-spacer" />
      <div className="topbar-actions">
        <EnvironmentSelector />
        <span className="conn" title={backend.error || backendLabel(backend.state)}>
          <StatusIndicator tone={backendTone(backend.state)} pulse={backend.state === 'checking'} />
          <span className="conn-text">{backend.state === 'online' ? 'API' : backendLabel(backend.state)}</span>
        </span>
        <Notifications />
        <ProfileMenu />
      </div>
    </header>
  )
}
