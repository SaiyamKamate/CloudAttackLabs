import { useCallback, useEffect, useRef, useState } from 'react'

// Load data with `loader` when `deps` change. Returns the last good data, the
// last error, whether the first answer is still outstanding, and reload().
// Errors keep the previous data so a failed refresh does not blank the page.
export function useApi(loader, deps = []) {
  const [state, setState] = useState({ data: undefined, error: null, loading: true, refreshing: false })
  const [nonce, setNonce] = useState(0)
  const loaderRef = useRef(loader)
  useEffect(() => { loaderRef.current = loader })

  useEffect(() => {
    let live = true
    Promise.resolve()
      .then(() => loaderRef.current())
      .then(
        (data) => live && setState({ data, error: null, loading: false, refreshing: false }),
        (err) => live && setState((s) => ({ data: s.data, error: err.message, loading: false, refreshing: false })),
      )
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  const reload = useCallback(() => {
    setState((s) => ({ ...s, refreshing: true }))
    setNonce((n) => n + 1)
  }, [])

  const setData = useCallback((update) => {
    setState((s) => ({ ...s, data: typeof update === 'function' ? update(s.data) : update }))
  }, [])

  return { ...state, reload, setData }
}

// Call `fn` every `ms` milliseconds while `enabled`. Skips ticks while the tab is hidden.
export function useInterval(fn, ms, enabled = true) {
  const fnRef = useRef(fn)
  useEffect(() => { fnRef.current = fn })
  useEffect(() => {
    if (!enabled) return undefined
    const id = setInterval(() => {
      if (document.visibilityState !== 'hidden') fnRef.current()
    }, ms)
    return () => clearInterval(id)
  }, [ms, enabled])
}

// The current time, updated every `ms`. Keeps "x min ago" labels pure in render.
export function useNow(ms = 30000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}

// A value kept in localStorage. Storage can be unavailable; then it is in memory only.
export function useStoredState(key, initial) {
  const [value, setValue] = useState(() => {
    try {
      const stored = localStorage.getItem(key)
      return stored === null ? initial : JSON.parse(stored)
    } catch {
      return initial
    }
  })
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage unavailable */ }
  }, [key, value])
  return [value, setValue]
}
