import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'

// A panel that slides in from the right for investigating one record.
export default function Drawer({ kicker, title, actions, onClose, wide = false, children }) {
  const layerRef = useRef(null)

  useEffect(() => {
    const onKey = (event) => {
      // Escape closes only the topmost drawer, and leaves an open confirmation dialog alone.
      const layers = document.querySelectorAll('.drawer-layer')
      const topmost = layers[layers.length - 1] === layerRef.current
      if (event.key === 'Escape' && topmost && !document.querySelector('.dialog')) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="drawer-layer" ref={layerRef}>
      <div className="backdrop" onClick={onClose} />
      <aside className={`drawer${wide ? ' wide' : ''}`} role="dialog" aria-modal="true">
        <div className="drawer-header">
          <div style={{ minWidth: 0 }}>
            {kicker && <div className="drawer-kicker">{kicker}</div>}
            <h2>{title}</h2>
          </div>
          <div className="row" style={{ flexWrap: 'nowrap' }}>
            {actions}
            <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={16} /></button>
          </div>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  )
}
