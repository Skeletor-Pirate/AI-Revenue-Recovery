import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import { labelRootCause } from '../api/actionLabels'

// RAG: the nearest already-classified cases from the knowledge base. Empty when
// the knowledge base / embeddings aren't available — the Diagnosis agent then
// classifies from rules + the LLM alone.
export function SimilarCases({ caseId }: { caseId: string }) {
  const state = useAsync(() => dataSource.getSimilar(caseId), [caseId])

  return (
    <section className="mt-5">
      <h3 className="mb-1 text-sm font-semibold text-ink">
        Similar past cases{' '}
        <span className="font-normal text-ink-muted">(retrieval-augmented diagnosis)</span>
      </h3>

      {state.loading && (
        <p className="text-sm text-ink-muted">Searching the knowledge base…</p>
      )}
      {state.error && (
        <p className="text-sm text-ink-muted">Knowledge base unavailable.</p>
      )}
      {state.data && state.data.similar.length === 0 && (
        <p className="text-sm text-ink-muted">
          No knowledge-base matches — this case was diagnosed from rules alone.
        </p>
      )}
      {state.data && state.data.similar.length > 0 && (
        <ul className="space-y-2">
          {state.data.similar.map((c, i) => (
            <li
              key={`${c.event_id}-${i}`}
              className="rounded-xl bg-surface-2 p-3 text-sm"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-ink">
                  {labelRootCause(c.root_cause)}
                </span>
                <span className="font-mono text-xs text-ink-muted">
                  {(c.similarity * 100).toFixed(0)}% match
                </span>
              </div>
              <p className="mt-1 text-ink-soft">
                {c.raw_failure_reason ?? c.case_text}
              </p>
              <p className="mt-0.5 text-xs text-ink-muted">
                {c.source === 'reference'
                  ? 'reference example'
                  : `past case ${c.event_id}`}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
