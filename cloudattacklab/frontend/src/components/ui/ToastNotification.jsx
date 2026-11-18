import { CheckCircle2, Info, X, XCircle } from 'lucide-react'

const ICONS = { success: CheckCircle2, error: XCircle, info: Info }

export default function ToastStack({ toasts, onDismiss }) {
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((toast) => {
        const Icon = ICONS[toast.kind] || Info
        return (
          <div key={toast.id} className={`toast ${toast.kind}`}>
            <Icon size={16} style={{ flexShrink: 0, marginTop: 1 }} />
            <div style={{ minWidth: 0 }}>
              <div className="toast-title">{toast.title}</div>
              {toast.message && <div className="toast-msg">{toast.message}</div>}
            </div>
            <button className="icon-btn toast-close" style={{ width: 22, height: 22 }} onClick={() => onDismiss(toast.id)} aria-label="Dismiss">
              <X size={13} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
