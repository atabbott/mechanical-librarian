// Following the Library's selected case to the case that replaces it: the
// lineage check a subject-less Library update starts, the successor candidates
// Library updates name, and the move to the one that targets the same item.
// While a move is under way the selected case's absence is that move, not news.
// Moved out of the Library route under the route-size rule (#1903).
import { useQuery } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import type { UseNavigateResult } from '@tanstack/react-router'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

import { ApiError } from './api/http'
import type { CaseDetailView, LibraryView, LibraryWorkView, ReviewHttp } from './api/http'
import { coalesceMilliseconds } from './liveInvalidation'
import { queryWorkClass } from './queryWork'
import { detailQuery } from './router'
import type { RouterContext } from './router'

// Live Library invalidation batches the documented fifty-work burst for 120 ms.
// Keep each distinct subject from that bounded batch long enough to compare it
// with the next bounded page read, but never treat a frame as selection
// authority.
const maxCoalescedSuccessorCandidates = 50

export const useCaseSuccessor = ({
  preview,
  http,
  updates,
  queryClient,
  navigate,
  data,
  retained,
  works,
  detailData,
  ownerField,
  noticeMovedOwner,
}: {
  /** The URL's selected case. */
  preview: string | undefined
  http: ReviewHttp
  updates: RouterContext['updates']
  queryClient: QueryClient
  navigate: UseNavigateResult<'/library'>
  data: LibraryView | undefined
  retained: boolean
  works: readonly LibraryWorkView[]
  /** The selected case's read, when it has one. */
  detailData: CaseDetailView | undefined
  /** The destination of the selected field when it has owner controls. */
  ownerField: RefObject<string | null>
  /**
   * Tells the owner, on the successor `caseId`, that the work changed under the
   * open owner control for `destination`, and gives its field heading focus.
   */
  noticeMovedOwner: (caseId: string, destination: string, text: string) => void
}) => {
  const selectedTargetRef = useRef<{
    caseId: string
    connection: string
    library: string
    item: string
  } | null>(null)
  const selectedPreviewRef = useRef<string | undefined>(undefined)
  const pendingSuccessorRef = useRef(new Set<string>())
  const lineageGenerationRef = useRef(0)
  const [reconciledSuccessorIdentity, setReconciledSuccessorIdentity] = useState<string | null>(
    null,
  )
  const [activeLineageGeneration, setActiveLineageGeneration] = useState<number | null>(null)
  const [lineageReconciliation, setLineageReconciliation] = useState<{
    predecessor: string
    selection: string
    generation: number
  } | null>(null)
  const [successorGeneration, setSuccessorGeneration] = useState(0)
  // The case being left for its successor: while it is, its own failed read
  // and its absence from Library are the move, not something to report.
  const [movingFrom, setMovingFrom] = useState<string | null>(null)
  // The selected case once any path knows a successor is pending for it (a
  // lineage check, a Library update naming one, or a stale command): until
  // the move settles, its absence from Library is that move, not news. It is
  // set where the successor becomes known and cleared only where a move ends,
  // since a move can start before a lineage check's own update lands.
  const [successorPending, setSuccessorPending] = useState<string | null>(null)
  // The case whose owner form was open, or whose owner control had focus,
  // when the Library said it changed; the refetch that follows can unmount it.
  const ownerEngaged = useRef<string | null>(null)
  useEffect(() => {
    if (selectedPreviewRef.current !== preview) {
      setActiveLineageGeneration(null)
      setLineageReconciliation(null)
      setSuccessorPending(null)
    }
    selectedPreviewRef.current = preview
    pendingSuccessorRef.current.clear()
  }, [preview])
  const activeLineageReconciliation =
    lineageReconciliation !== null &&
    lineageReconciliation.predecessor === preview &&
    lineageReconciliation.selection === preview &&
    activeLineageGeneration === lineageReconciliation.generation
  const lineageSuccessor = useQuery({
    queryKey: [
      'review-case-successor',
      lineageReconciliation?.predecessor ?? null,
      lineageReconciliation?.generation ?? null,
    ],
    queryFn: (queryContext) =>
      http.getCaseSuccessor(
        lineageReconciliation?.predecessor ?? '',
        queryContext.signal,
        queryWorkClass(queryContext),
      ),
    enabled: activeLineageReconciliation,
    retry: (failureCount) => activeLineageReconciliation && failureCount < 1,
    retryDelay: coalesceMilliseconds,
    retryOnMount: false,
    refetchInterval: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
  })
  useEffect(() => {
    if (preview && detailData?.id === preview) {
      selectedTargetRef.current = { caseId: preview, ...detailData.target }
      if (pendingSuccessorRef.current.size) {
        setSuccessorPending(preview)
        setSuccessorGeneration((generation) => generation + 1)
      }
    }
  }, [detailData, preview])
  useLayoutEffect(() => {
    const reconciliation = lineageReconciliation
    if (
      !lineageSuccessor.isSuccess ||
      reconciliation === null ||
      reconciliation.selection !== preview ||
      selectedPreviewRef.current !== reconciliation.selection ||
      activeLineageGeneration !== reconciliation.generation
    )
      return
    // A successful null is authoritative; only the query's single bounded
    // retry treats transport failure as pending reconciliation.
    const named = lineageSuccessor.data
    const following =
      named !== null && pendingSuccessorRef.current.size < maxCoalescedSuccessorCandidates
    queueMicrotask(() => {
      setActiveLineageGeneration(null)
      setLineageReconciliation(null)
      if (following && selectedTargetRef.current?.caseId === reconciliation.selection)
        setSuccessorPending(reconciliation.selection)
    })
    if (following) {
      pendingSuccessorRef.current.add(named)
      setSuccessorGeneration((generation) => generation + 1)
    }
  }, [
    activeLineageGeneration,
    lineageReconciliation,
    lineageSuccessor.data,
    lineageSuccessor.isSuccess,
    preview,
  ])
  useLayoutEffect(() => {
    const reconciliation = lineageReconciliation
    if (
      !lineageSuccessor.isError ||
      lineageSuccessor.failureCount < 2 ||
      reconciliation === null ||
      reconciliation.selection !== preview ||
      activeLineageGeneration !== reconciliation.generation
    )
      return
    queueMicrotask(() => {
      setActiveLineageGeneration(null)
      setLineageReconciliation(null)
    })
  }, [
    activeLineageGeneration,
    lineageReconciliation,
    lineageSuccessor.failureCount,
    lineageSuccessor.isError,
    preview,
  ])
  useEffect(() => {
    if (!updates || !preview) return
    return updates.subscribe((update) => {
      if (update.topic !== 'library') return
      if (
        document.querySelector(
          '.owner-controls form, .owner-controls button:focus, .owner-controls input:focus',
        )
      )
        ownerEngaged.current = preview
      if (update.subject === null) {
        const predecessor = preview
        if (!predecessor) return
        const generation = ++lineageGenerationRef.current
        setActiveLineageGeneration(generation)
        setLineageReconciliation({ predecessor, selection: predecessor, generation })
        return
      }
      if (update.subject === preview) return
      if (
        pendingSuccessorRef.current.size < maxCoalescedSuccessorCandidates &&
        !pendingSuccessorRef.current.has(update.subject)
      ) {
        pendingSuccessorRef.current.add(update.subject)
        if (selectedTargetRef.current?.caseId === preview) setSuccessorPending(preview)
        // Query structural sharing can make the next bounded Library read
        // referentially identical after a no-match batch. Candidate arrival is
        // itself the reason to reconcile that read once more.
        setSuccessorGeneration((generation) => generation + 1)
      }
    })
  }, [http, preview, updates])
  useEffect(() => {
    const selectedTarget = selectedTargetRef.current
    const successorIds = [...pendingSuccessorRef.current]
    if (
      !data ||
      retained ||
      !selectedTarget ||
      !successorIds.length ||
      selectedTarget.caseId !== preview ||
      works.some((work) => work.reviewCaseId === preview)
    )
      return
    pendingSuccessorRef.current.clear()
    setMovingFrom(selectedTarget.caseId)
    void (async () => {
      try {
        const predecessor = await http.getCaseDetail(selectedTarget.caseId)
        if (
          predecessor.current ||
          predecessor.target.connection !== selectedTarget.connection ||
          predecessor.target.library !== selectedTarget.library ||
          predecessor.target.item !== selectedTarget.item
        ) {
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        }
      } catch (error) {
        // The server refuses a superseded case's read as stale; that is the
        // case no longer being current. Each successor's target is checked.
        if (!(error instanceof ApiError && error.code === 'stale')) {
          // The check itself failed, so the case is read again while what the
          // owner has open stays; a failed read then drops it (#1365).
          void queryClient.refetchQueries({
            queryKey: detailQuery(http, selectedTarget.caseId).queryKey,
            exact: true,
          })
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        }
      }
      for (const successorId of successorIds) {
        try {
          const successor = await http.getCaseDetail(successorId)
          if (
            !successor.current ||
            successor.target.connection !== selectedTarget.connection ||
            successor.target.library !== selectedTarget.library ||
            successor.target.item !== selectedTarget.item
          )
            continue
          setReconciledSuccessorIdentity(successor.id)
          const destination = ownerField.current
          if (destination && ownerEngaged.current === selectedTarget.caseId) {
            ownerEngaged.current = null
            noticeMovedOwner(successor.id, destination, 'The work changed. Review it again.')
          }
          await navigate({
            replace: true,
            search: (current) =>
              current.preview === selectedTarget.caseId
                ? { ...current, preview: successor.id }
                : current,
          })
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        } catch {
          // A case can disappear again between the bounded page read and its
          // authoritative detail read. The remaining coalesced candidates may
          // still name its replacement.
        }
      }
      // A move that ends nowhere leaves a predecessor its own read refused, so
      // its retained case read is read again rather than shown as current.
      void queryClient.resetQueries({
        queryKey: detailQuery(http, selectedTarget.caseId).queryKey,
        exact: true,
      })
      setMovingFrom(null)
      setSuccessorPending(null)
    })()
  }, [
    data,
    retained,
    works,
    http,
    navigate,
    queryClient,
    preview,
    successorGeneration,
    ownerField,
    noticeMovedOwner,
  ])
  return {
    activeLineageReconciliation,
    reconciledSuccessorIdentity,
    setReconciledSuccessorIdentity,
    movingFrom,
    setMovingFrom,
    successorPending,
    setSuccessorPending,
  }
}
