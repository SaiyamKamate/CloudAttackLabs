import { useSyncExternalStore } from 'react'

// A small hash router: "#/incidents/<id>" -> { page: 'incidents', id: '<id>' }.
// Hash routes work behind both Vite and nginx without server changes.

function subscribe(callback) {
  window.addEventListener('hashchange', callback)
  return () => window.removeEventListener('hashchange', callback)
}

function snapshot() {
  return window.location.hash
}

export function parseHash(hash) {
  const [path, queryString] = hash.replace(/^#\/?/, '').split('?')
  const [page, id] = path.split('/').filter(Boolean).map(decodeURIComponent)
  return { page: page || 'overview', id: id || null, query: new URLSearchParams(queryString || '') }
}

export function useRoute() {
  return parseHash(useSyncExternalStore(subscribe, snapshot))
}

export function navigate(page, id = null, query = null) {
  let hash = `#/${page}${id ? `/${encodeURIComponent(id)}` : ''}`
  if (query) {
    const params = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== null && v !== undefined && v !== ''))
    const text = params.toString()
    if (text) hash += `?${text}`
  }
  if (window.location.hash !== hash) window.location.hash = hash
}

export function href(page, id = null) {
  return `#/${page}${id ? `/${encodeURIComponent(id)}` : ''}`
}
