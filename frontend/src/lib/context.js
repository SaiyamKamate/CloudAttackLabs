import { createContext, useContext } from 'react'

// Contexts live here (not in the provider files) so the provider files only
// export components, as React Fast Refresh expects.

// Toasts and confirmations: { notify(kind, title, message), confirm(options) -> Promise<boolean> }
export const UIContext = createContext(null)

// Shared platform state: labs, the selected lab, backend connectivity, the run
// started from the Scenarios page, and attention counts for the bell.
export const PlatformContext = createContext(null)

export function useUI() {
  return useContext(UIContext)
}

export function usePlatform() {
  return useContext(PlatformContext)
}
