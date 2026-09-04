import { useEffect, useState } from 'react'

export interface AsyncState<T> {
  data?: T
  error?: string
  loading: boolean
}

export function useAsync<T>(
  fn: () => Promise<T>,
  deps: readonly unknown[] = [],
): AsyncState<T> & { reload: () => void } {
  const [tick, setTick] = useState(0)
  const [state, setState] = useState<AsyncState<T>>({ loading: true })

  useEffect(() => {
    let alive = true
    setState({ loading: true })
    fn()
      .then((data) => alive && setState({ data, loading: false }))
      .catch(
        (e: unknown) =>
          alive &&
          setState({ error: e instanceof Error ? e.message : String(e), loading: false }),
      )
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps])

  return { ...state, reload: () => setTick((t) => t + 1) }
}
