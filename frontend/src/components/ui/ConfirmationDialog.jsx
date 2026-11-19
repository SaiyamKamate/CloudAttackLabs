import { useEffect, useRef } from 'react'
import { AlertTriangle, HelpCircle } from 'lucide-react'

// A modal asking the user to confirm a sensitive operation.
export default function ConfirmationDialog({ title, children, confirmLabel = 'Confirm', danger = false, onConfirm, onCancel }) {
  const cancelRef = useRef(null)

  useEffect(() => {
    cancelRef.current?.focus()
    const onKey = (event) => { if (event.key === 'Escape') onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <>
      <div className="backdrop dialog-backdrop" onClick={onCancel} />
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dialog-title">
        <div className="dialog-header">
          <div className={`dialog-icon${danger ? ' danger' : ''}`}>
            {danger ? <AlertTriangle size={16} /> : <HelpCircle size={16} />}
          </div>
          <h2 id="dialog-title" style={{ paddingTop: 6 }}>{title}</h2>
        </div>
        <div className="dialog-body">{children}</div>
        <div className="dialog-footer">
          <button ref={cancelRef} className="btn" onClick={onCancel}>Cancel</button>
          <button className={`btn ${danger ? 'btn-danger-solid' : 'btn-primary'}`} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </>
  )
}
