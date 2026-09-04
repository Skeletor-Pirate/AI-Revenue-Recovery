export const toNum = (s: string | number | null | undefined): number => {
  if (s == null) return 0
  const n = typeof s === 'number' ? s : Number(s)
  return Number.isFinite(n) ? n : 0
}

export const formatINR = (n: string | number): string =>
  '₹' +
  new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(toNum(n))

export const formatINRPrecise = (n: string | number): string =>
  '₹' +
  new Intl.NumberFormat('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(toNum(n))

export const formatPct = (rate: number): string => `${Math.round(rate * 100)}%`

export const formatHours = (h: number): string => {
  if (h < 1) return '<1h'
  if (h < 48) return `${Math.round(h)}h`
  return `${(h / 24).toFixed(1)} days`
}

export const formatDateTime = (iso: string): string => {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}
