import { useCallback, useMemo, useRef, useState } from 'react'
import { UIContext } from '../../lib/context.js'
import ConfirmationDialog from './ConfirmationDialog.jsx'
import ToastStack from './ToastNotification.jsx'

// Provides notify() for toasts and confirm() for promise-based confirmation dialogs.
export default function UIProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const [dialog, setDialog] = useState(null)
  const idRef = useRef(0)

  const dismiss = useCallback((id) => setToasts((list) => list.filter((t) => t.id !== id)), [])

  const notify = useCallback((kind, title, message) => {
    const id = ++idRef.current
    setToasts((list) => [...list.slice(-3), { id, kind, title, message }])
    setTimeout(() => dismiss(id), kind === 'error' ? 9000 : 5000)
  }, [dismiss])

  const confirm = useCallback((options) => new Promise((resolve) => {
    setDialog({ ...options, resolve })
  }), [])

  const close = (answer) => {
    dialog?.resolve(answer)
    setDialog(null)
  }

  const value = useMemo(() => ({ notify, confirm }), [notify, confirm])

  return (
    <UIContext.Provider value={value}>
      {children}
      <ToastStack toasts={toasts} onDismiss={dismiss} />
      {dialog && (
        <ConfirmationDialog
          title={dialog.title}
          confirmLabel={dialog.confirmLabel}
          danger={dialog.danger}
          onConfirm={() => close(true)}
          onCancel={() => close(false)}
        >
          {dialog.message}
        </ConfirmationDialog>
      )}
    </UIContext.Provider>
  )
}
