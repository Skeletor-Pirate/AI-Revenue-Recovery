import type { Column } from '../components/DataTable'

export function toCsv<T>(columns: Column<T>[], rows: T[]): string {
  const cols = columns.filter((c) => c.csv)
  const head = cols.map((c) => c.header).join(',')
  const body = rows
    .map((r) =>
      cols
        .map((c) => {
          const v = String(c.csv!(r)).replace(/"/g, '""')
          return /[",\n]/.test(v) ? `"${v}"` : v
        })
        .join(','),
    )
    .join('\n')
  return `${head}\n${body}`
}

export function downloadCsv(filename: string, csv: string) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
