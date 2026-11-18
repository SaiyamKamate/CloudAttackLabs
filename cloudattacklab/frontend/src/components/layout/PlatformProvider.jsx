import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api.js'
import { PlatformContext, useUI } from '../../lib/context.js'
import { TERMINAL_RUN, loadPendingApprovals } from '../../lib/data.js'
import { useInterval, useStoredState } from '../../lib/hooks.js'

const LIVE_LAB = ['running', 'starting', 'degraded']

// Shared platform state, polled from the backend:
// - backend connectivity (GET /health)
// - the labs and the selected lab's detail (GET /labs, GET /labs/{id})
// - the run started from the Scenarios page, followed until it finishes
// - attention counts for the notification bell (open incidents, pending approvals)
export default function PlatformProvider({ children }) {
  const { notify } = useUI()
  const [backend, setBackend] = useState({ state: 'checking', error: null, checkedAt: null })
  const [labs, setLabs] = useState(null)
  const [labsError, setLabsError] = useState(null)
  const [storedLabId, setStoredLabId] = useStoredState('cal.activeLab', null)
  const [labDetail, setLabDetail] = useState(null)
  const [labDetailError, setLabDetailError] = useState(null)
  const [run, setRun] = useState(null)
  const [attention, setAttention] = useState({ openIncidents: null, pending: [], loaded: false })
  const runRef = useRef(null)

  // The selected lab: the stored choice if it still exists, else a live lab, else the first.
  const activeLab = useMemo(() => {
    if (!labs || labs.length === 0) return null
    return labs.find((l) => l.id === storedLabId) || labs.find((l) => LIVE_LAB.includes(l.status)) || labs[0]
  }, [labs, storedLabId])
  const activeLabId = activeLab?.id ?? null

  const checkHealth = useCallback(async () => {
    try {
      await api.getHealth()
      setBackend({ state: 'online', error: null, checkedAt: Date.now() })
    } catch (err) {
      setBackend({ state: err.status === 503 ? 'degraded' : 'offline', error: err.message, checkedAt: Date.now() })
    }
  }, [])

  const refreshLabs = useCallback(async () => {
    try {
      const list = await api.listLabs()
      setLabs(list)
      setLabsError(null)
      return list
    } catch (err) {
      setLabsError(err.message)
      return null
    }
  }, [])

  const refreshLabDetail = useCallback(async (labId = activeLabId) => {
    if (!labId) { setLabDetail(null); return null }
    try {
      const detail = await api.getLab(labId)
      setLabDetail(detail)
      setLabDetailError(null)
      // GET /labs/{id} corrects the stored status; keep the list in step.
      setLabs((list) => list && list.map((l) => (l.id === detail.id ? { ...l, status: detail.status, updated_at: detail.updated_at } : l)))
      return detail
    } catch (err) {
      setLabDetailError(err.message)
      return null
    }
  }, [activeLabId])

  const refreshAttention = useCallback(async () => {
    try {
      const [incidents, pending] = await Promise.all([api.listIncidents(), loadPendingApprovals()])
      setAttention({ openIncidents: incidents.filter((i) => i.status === 'open').length, pending, loaded: true })
    } catch {
      setAttention((a) => ({ ...a, loaded: true }))
    }
  }, [])

  // Initial load. State is set after each await, not synchronously.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    checkHealth()
    refreshLabs()
    refreshAttention()
  }, [checkHealth, refreshLabs, refreshAttention])

  useEffect(() => {
    // Load the selected lab's detail whenever the selection changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshLabDetail(activeLabId)
  }, [activeLabId, refreshLabDetail])

  useInterval(checkHealth, 15000)
  useInterval(() => refreshLabDetail(), 20000, Boolean(activeLabId))
  useInterval(refreshAttention, 30000)

  // Follow the current run until it reaches a terminal status.
  useEffect(() => { runRef.current = run }, [run])
  const runActive = Boolean(run && !TERMINAL_RUN.includes(run.status))
  useInterval(async () => {
    const current = runRef.current
    if (!current) return
    try {
      const latest = await api.getRun(current.id)
      setRun(latest)
      if (TERMINAL_RUN.includes(latest.status)) {
        const tone = latest.status === 'completed' ? 'success' : latest.status === 'failed' ? 'error' : 'info'
        notify(tone, `Run ${latest.status}`, `${latest.scenario_id} finished with status ${latest.status}.`)
        refreshLabDetail()
        refreshAttention()
      }
    } catch (err) {
      notify('error', 'Could not follow the run', err.message)
    }
  }, 1500, runActive)

  const selectLab = useCallback((labId) => setStoredLabId(labId), [setStoredLabId])

  const value = useMemo(() => ({
    backend, checkHealth,
    labs, labsError, refreshLabs,
    activeLab, activeLabId, selectLab,
    labDetail: labDetail && labDetail.id === activeLabId ? labDetail : null,
    labDetailError, refreshLabDetail,
    run, setRun, runActive,
    attention, refreshAttention,
  }), [backend, checkHealth, labs, labsError, refreshLabs, activeLab, activeLabId, selectLab, labDetail, labDetailError, refreshLabDetail, run, runActive, attention, refreshAttention])

  return <PlatformContext.Provider value={value}>{children}</PlatformContext.Provider>
}
