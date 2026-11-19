import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from 'lucide-react'
import { EmptyState, SkeletonRows } from './States.jsx'

// A sortable, paginated table.
// columns: [{ key, header, render(row), sortValue(row), num, wrap, width }]
export default function DataTable({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
  loading = false,
  empty,
  pageSize = 50,
  initialSort = null, // { key, dir: 'asc' | 'desc' }
  footer,
}) {
  const [sort, setSort] = useState(initialSort)
  const [page, setPage] = useState(0)

  const sorted = useMemo(() => {
    if (!sort) return rows
    const column = columns.find((c) => c.key === sort.key)
    if (!column?.sortValue) return rows
    const factor = sort.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const x = column.sortValue(a)
      const y = column.sortValue(b)
      if (x === y) return 0
      if (x === null || x === undefined) return 1
      if (y === null || y === undefined) return -1
      return x > y ? factor : -factor
    })
  }, [rows, sort, columns])

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize))
  const current = Math.min(page, pages - 1)
  const visible = sorted.slice(current * pageSize, (current + 1) * pageSize)

  function toggleSort(column) {
    if (!column.sortValue) return
    setSort((s) => (s?.key === column.key ? { key: column.key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: column.key, dir: 'desc' }))
  }

  if (loading) return <SkeletonRows rows={6} />

  return (
    <>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  className={`${column.num ? 'num' : ''} ${column.sortValue ? 'sortable' : ''}`}
                  style={column.width ? { width: column.width } : undefined}
                  onClick={() => toggleSort(column)}
                >
                  {column.header}
                  {sort?.key === column.key && (
                    <span className="sort-ind">{sort.dir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => {
              const key = rowKey(row)
              return (
                <tr
                  key={key}
                  className={`${onRowClick ? 'clickable' : ''} ${selectedKey === key ? 'selected' : ''}`}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                >
                  {columns.map((column) => (
                    <td key={column.key} className={`${column.num ? 'num' : ''} ${column.wrap ? 'wrap' : ''}`}>
                      {column.render ? column.render(row) : row[column.key]}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && (empty || <EmptyState compact title="No rows" />)}
      {(pages > 1 || footer) && (
        <div className="table-footer">
          <span>{footer}</span>
          {pages > 1 && (
            <div className="row">
              <span>
                {current * pageSize + 1}–{Math.min(sorted.length, (current + 1) * pageSize)} of {sorted.length}
              </span>
              <button className="btn btn-sm btn-ghost" disabled={current === 0} onClick={() => setPage(current - 1)} aria-label="Previous page">
                <ChevronLeft size={14} />
              </button>
              <button className="btn btn-sm btn-ghost" disabled={current >= pages - 1} onClick={() => setPage(current + 1)} aria-label="Next page">
                <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>
      )}
    </>
  )
}
