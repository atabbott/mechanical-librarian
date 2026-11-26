import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef } from 'react'
import type { RefObject } from 'react'

import { ApiError, refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { ReviewHttp, UpgradeNotice as UpgradeNoticeData, UpgradeNoticeView } from '../api/http'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { Button } from './Button'

/**
 * After an upgrade regenerated the derived rows, Activity and the Attention
 * rail say how many of the owner's answers were kept and how many are asked
 * again, until the owner dismisses it (ADR-0108, #2039). Both places read one
 * query, so a dismissal in either hides it in both.
 */
const upgradeNoticeKey = ['upgrade-notice'] as const

/** Whether a regeneration that kept every answer and asked none again is said. */
export const SHOW_ALL_KEPT_NOTICE = true

const count = (value: number) => value.toLocaleString('en-US')

// Every sentence the notice says, in one place (draft copy, #2039 page v5).
const upgradeNoticeCopy = {
  askedAgainOneOfSome: () => '1 of your answers is asked again after the upgrade.',
  askedAgainSomeOfSome: (asked: number) =>
    `${count(asked)} of your answers are asked again after the upgrade. Each one says why.`,
  otherKeptOne: () => 'The other one was kept.',
  otherKeptSome: (kept: number) => `The other ${count(kept)} were kept.`,
  askedAgainOnlyOne: () => 'Your 1 answer is asked again after the upgrade.',
  askedAgainAll: (asked: number) =>
    `All ${count(asked)} of your answers are asked again after the upgrade. Each one says why.`,
  keptOnlyOne: () => 'After the upgrade, your 1 answer was kept.',
  keptAll: (kept: number) => `After the upgrade, all ${count(kept)} of your answers were kept.`,
  preparing: () => 'Preparing your library after the upgrade.',
  soFar: (kept: number, asked: number) =>
    asked === 0
      ? `So far ${count(kept)} of your answers ${kept === 1 ? 'was' : 'were'} kept.`
      : kept === 0
        ? `So far ${count(asked)} of your answers ${asked === 1 ? 'was' : 'were'} asked again.`
        : `So far ${count(kept)} of your answers ${kept === 1 ? 'was' : 'were'} kept and ${count(asked)} ${asked === 1 ? 'was' : 'were'} asked again.`,
  dismiss: 'Dismiss',
  notChanged: 'Nothing was changed. Try again.',
  noAnswer: 'No answer came back. Check whether the change took effect before pressing again.',
}

// The notice's sentences, or null when it says nothing.
const noticeText = (notice: UpgradeNoticeData, showAllKept: boolean): string | null => {
  const { asked_again: asked, kept } = notice
  const copy = upgradeNoticeCopy
  if (notice.preparing)
    return asked + kept >= 1 ? `${copy.preparing()} ${copy.soFar(kept, asked)}` : copy.preparing()
  if (asked >= 1 && kept >= 1)
    return `${asked === 1 ? copy.askedAgainOneOfSome() : copy.askedAgainSomeOfSome(asked)} ${kept === 1 ? copy.otherKeptOne() : copy.otherKeptSome(kept)}`
  if (asked >= 1) return asked === 1 ? copy.askedAgainOnlyOne() : copy.askedAgainAll(asked)
  if (kept >= 1 && showAllKept) return kept === 1 ? copy.keptOnlyOne() : copy.keptAll(kept)
  return null
}

export const UpgradeNotice = ({
  http,
  focusAfterDismiss,
  showAllKept = SHOW_ALL_KEPT_NOTICE,
}: {
  http: ReviewHttp
  /** The surface's heading, which takes focus when a dismissal removes the notice. */
  focusAfterDismiss?: RefObject<HTMLElement | null>
  showAllKept?: boolean
}) => {
  const queryClient = useQueryClient()
  const section = useRef<HTMLElement>(null)
  const query = useQuery({
    queryKey: upgradeNoticeKey,
    queryFn: (context) => http.getUpgradeNotice(context.signal, queryWorkClass(context)),
    retry: false,
    // While the first new cases are checked, the counts move.
    refetchInterval: (current) => (current.state.data?.notice?.preparing ? 15_000 : false),
  })
  const dismissal = useMutation({
    mutationFn: (regenerationId: string) => http.dismissUpgradeNotice(regenerationId),
    retry: false,
    onSuccess: () => {
      // The dismissed notice takes its focused button with it; focus moved
      // elsewhere while the request ran is left where it is.
      const active = document.activeElement
      const hadFocus = active === document.body || !!section.current?.contains(active)
      queryClient.setQueryData<UpgradeNoticeView>(upgradeNoticeKey, (current) =>
        current ? { ...current, notice: null } : current,
      )
      if (hadFocus) focusAfterDismiss?.current?.focus()
    },
    // A notice dismissed elsewhere, or replaced by a newer regeneration, is
    // read again: it goes, or says the newer counts.
    onError: () => queryClient.invalidateQueries({ queryKey: upgradeNoticeKey }),
  })
  const notice = query.data?.notice
  if (!notice) return null
  const text = noticeText(notice, showAllKept)
  if (text === null) return null
  const stale = dismissal.error instanceof ApiError && dismissal.error.code === 'stale'
  return (
    // No live region: the counts re-read every 15 s while preparing and are
    // not announced again; only the dismissal's error is, as an alert.
    <section ref={section} className="cm-library-handoff cm-upgrade-notice">
      <div>
        <p>{text}</p>
      </div>
      <div className="cm-library-handoff-action">
        <Button
          type="button"
          aria-disabled={dismissal.isPending || undefined}
          onClick={() => {
            if (!dismissal.isPending) dismissal.mutate(notice.regeneration_id)
          }}
        >
          {upgradeNoticeCopy.dismiss}
        </Button>
      </div>
      {dismissal.isError && !stale ? (
        <p role="alert">
          {uncertainWriteOutcome(dismissal.error)
            ? upgradeNoticeCopy.noAnswer
            : refusalCopy(dismissal.error, upgradeNoticeCopy.notChanged)}
        </p>
      ) : null}
    </section>
  )
}
