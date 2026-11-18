// Formatting helpers. The backend sends UTC ISO 8601; these show local time.

export function fmt(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

// Timeline entries and events can be milliseconds apart, so show them.
export function fmtTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour12: false })}.${String(date.getMilliseconds()).padStart(3, '0')}`
}

// 24-hour clock time with milliseconds, e.g. 19:27:00.354.
export function fmtClock(value) {
  if (!value) return '—'
  const date = new Date(value)
  return `${date.toLocaleTimeString([], { hour12: false })}.${String(date.getMilliseconds()).padStart(3, '0')}`
}

// "3 min ago". `now` is passed in so render stays pure.
export function fmtAgo(value, now) {
  if (!value || !now) return '—'
  const seconds = Math.round((now - new Date(value).getTime()) / 1000)
  if (seconds < 0) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  return `${Math.floor(seconds / 86400)} d ago`
}

// Seconds between two timestamps, or null when either is missing.
export function secondsBetween(start, end) {
  if (!start || !end) return null
  return (new Date(end).getTime() - new Date(start).getTime()) / 1000
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—'
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return `${minutes} min ${rest} s`
}

export function shortId(id) {
  return id ? String(id).slice(0, 8) : '—'
}

export function humanize(value) {
  if (!value) return '—'
  return String(value).replaceAll('_', ' ')
}

export function score(value) {
  return value === null || value === undefined ? 'not scored' : `${value} / 100`
}
