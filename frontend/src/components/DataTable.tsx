import { useMemo, useState, type ReactNode } from 'react'

export interface Column<T> {
  key: string
  header: string
  numeric?: boolean
  render: (row: T) => ReactNode
  sortValue?: (row: T) => number | string
  csv?: (row: T) => string | number
}

interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  onRowClick?: (row: T) => void
  rowAccent?: (row: T) => string | undefined
  initialSort?: { key: string; dir: 'asc' | 'desc' }
  emptyLabel?: string
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  rowAccent,
  initialSort,
  emptyLabel = 'No rows',
}: DataTableProps<T>) {
  const [sort, setSort] = useState(initialSort)

  const sorted = useMemo(() => {
    if (!sort) return rows
    const col = columns.find((c) => c.key === sort.key)
    if (!col?.sortValue) return rows
    const dir = sort.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const av = col.sortValue!(a)
      const bv = col.sortValue!(b)
      if (av < bv) return -1 * dir
      if (av > bv) return 1 * dir
      return 0
    })
  }, [rows, sort, columns])

  const toggleSort = (key: string) =>
    setSort((s) =>
      s?.key === key
        ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: 'desc' },
    )

  if (rows.length === 0) {
    return (
      <div className="rounded-xl bg-surface-2 px-4 py-10 text-center text-sm text-ink-muted">
        {emptyLabel}
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-xl ring-1 ring-[var(--color-ring)]">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-surface-2">
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={`px-3 py-2 font-medium text-ink-soft ${
                  c.numeric ? 'text-right' : 'text-left'
                } ${c.sortValue ? 'cursor-pointer select-none' : ''}`}
                onClick={c.sortValue ? () => toggleSort(c.key) : undefined}
              >
                {c.header}
                {sort?.key === c.key && (sort.dir === 'asc' ? ' ▲' : ' ▼')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const accent = rowAccent?.(row)
            return (
              <tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={`border-t border-[var(--color-hairline)] ${
                  onRowClick ? 'cursor-pointer hover:bg-surface-2' : ''
                }`}
                style={
                  accent
                    ? { boxShadow: `inset 3px 0 0 0 ${accent}` }
                    : undefined
                }
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={`px-3 py-2 align-top ${
                      c.numeric ? 'text-right tabnum' : 'text-left'
                    }`}
                  >
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
