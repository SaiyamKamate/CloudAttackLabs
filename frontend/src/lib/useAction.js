import { useCallback, useState } from 'react'
import { useUI } from './context.js'

// Run a backend operation with a busy label, a success toast and an error
// toast that carries the backend's own error text. Returns { ok, result, error }.
export function useAction() {
  const { notify } = useUI()
  const [busy, setBusy] = useState(null)

  const run = useCallback(async (label, fn, { success, quiet = false } = {}) => {
    setBusy(label)
    try {
      const result = await fn()
      if (success) {
        const text = typeof success === 'function' ? success(result) : success
        if (Array.isArray(text)) notify('success', text[0], text[1])
        else notify('success', text)
      }
      return { ok: true, result }
    } catch (err) {
      if (!quiet) notify('error', `${label} failed`, err.code ? `${err.message} (${err.code})` : err.message)
      return { ok: false, error: err }
    } finally {
      setBusy(null)
    }
  }, [notify])

  return [busy, run]
}
