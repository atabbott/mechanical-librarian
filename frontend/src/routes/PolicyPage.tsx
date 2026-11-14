import { invalidateBackgroundQueries } from '../queryWork'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'

import {
  AmbiguousMutationError,
  ApiError,
  BuildMismatchError,
  CsrfRenewedError,
  derivePolicyDraftDisplay,
  policyAutomaticQualification,
  refusalCopy,
  sessionRefreshedCopy,
  uncertainWriteOutcome,
} from '../api/http'
import type {
  PolicyActivationRequest,
  PolicyActivationView,
  PolicyCommandBody,
  PolicyCommandPath,
  PolicyDraft,
  PolicyPreviewView,
  PolicySection,
  TargetDestination,
} from '../api/http'
import { ChangeCount } from '../components/ChangeCount'
import { Button } from '../components/Button'
import { StatusPill } from '../components/StatusPill'
import { DescriptionAdviceSettings } from '../components/DescriptionAdviceSettings'
import { MatchingSettings } from '../components/MatchingSettings'
import { Disclosure } from '../components/Disclosure'
import { Dropdown } from '../components/Dropdown'
import { FinishTray } from '../components/FinishTray'
import { SegmentedControl } from '../components/SegmentedControl'
import { destinationLabel } from '../labels'
import { useLibraryName } from '../libraryNames'
import { PolicySafety } from './PolicySafety'
import { libraryList } from '../writeConsent'
import { policyRoute, useReviewContext } from '../router'
import type { PolicyCritical } from '../router'
import {
  audiobookOperationLabels,
  automaticQualified,
  chapters,
  fieldDefaults,
  fields,
  operationScopeLabels,
  operationWords,
  policyDestinationLabels,
  policyRepresentationGuidance,
  readableCategory,
  representationConsequence,
} from '../policyFields'
import type { FieldDefinition } from '../policyFields'
import { policyImpactDisplay, policyImpactTotals } from '../policyImpact'

type PolicyStage = 'editing' | 'validating' | 'preview' | 'activation' | 'activated'
// What the page's one policy mutation sends: preview and activation with their
// own request types, and every other command typed by its path.
type PolicyMutation =
  | { action: 'preview'; body: PolicyDraft }
  | { action: 'activate'; body: PolicyActivationRequest }
  | { action: PolicyCommandPath; body?: PolicyCommandBody<PolicyCommandPath> }

// "a, b or c": one comma list, never a chain of "or".
const plainList = (items: readonly string[], last: 'or' | 'and') =>
  items.length < 2
    ? (items[0] ?? '')
    : `${items.slice(0, -1).join(', ')} ${last} ${items.at(-1) ?? ''}`

const Check = () => (
  <svg aria-hidden="true" viewBox="0 0 24 24">
    <path d="m5 12 4 4L19 7" />
  </svg>
)

export default function PolicyPage({ data }: { data: PolicyCritical }) {
  const { http, queryClient } = useReviewContext()
  const search = policyRoute.useSearch()
  const navigate = useNavigate({ from: '/policy' })
  const activeChapter = search.section
  const [draft, setDraft] = useState<PolicyDraft>(() => ({
    name: data.state.profile.name,
    options: { ...data.state.profile.options },
    destinations: { ...data.state.profile.destinations },
  }))
  const [snapshotId, setSnapshotId] = useState<string>()
  const [previewActivePolicyId, setPreviewActivePolicyId] = useState<string>()
  const [preview, setPreview] = useState<PolicyPreviewView>()
  const [expandedPreview, setExpandedPreview] = useState<PolicyPreviewView>()
  const [stage, setStage] = useState<PolicyStage>('editing')
  const [status, setStatus] = useState<{
    tone: 'neutral' | 'success' | 'error' | 'warning'
    message: string
  }>({ tone: 'neutral', message: 'Draft matches the active policy.' })
  // The completion acknowledgement belongs to the activation this page
  // performed. Anything the owner still has to act on -- work in flight, or a
  // failure to retry -- is shown whoever is looking.
  const [activatedTransitionId, setActivatedTransitionId] = useState<string>()
  const posture = data.posture
  const safety = data.safety
  const transition = useQuery({
    queryKey: ['policy', 'transition'],
    queryFn: (queryContext) =>
      http.getPolicyTransition(queryContext.signal, queryWorkClass(queryContext)),
    staleTime: 1_000,
    retry: false,
    refetchInterval: (query) => (query.state.data?.state === 'processing' ? 1_500 : false),
  })
  // The preview names a library by its settings slug; the display name is read
  // only once a preview exists.
  const libraryName = useLibraryName(http, preview !== undefined).name
  // Activation carries each library's write permission to the new policy, so
  // the step names the libraries that hold it now (#1370).
  const consent = useQuery({
    queryKey: ['activity', 'policy-activation'] as const,
    queryFn: (queryContext) =>
      http.getActivityConsent(queryContext.signal, queryWorkClass(queryContext)),
    retry: false,
    enabled: stage === 'activation',
  })
  const keptPermission = consent.data?.capabilities
    .filter((item) => item.destinations.some((entry) => entry.consented))
    .map((item) => libraryName(item.library))
  const refresh = async () => {
    await Promise.all([
      invalidateBackgroundQueries(queryClient, { queryKey: ['policy'] }),
      invalidateBackgroundQueries(queryClient, { queryKey: ['library'] }),
      invalidateBackgroundQueries(queryClient, { queryKey: ['attention'] }),
      invalidateBackgroundQueries(queryClient, { queryKey: ['activity'] }),
    ])
  }
  const command = useMutation({
    mutationFn: async (input: PolicyMutation) => {
      // Preview and activation carry the checks the schema cannot state, so
      // they go through their typed client methods (ADR-0091 P-X1 to P-X3).
      if (input.action === 'preview') return http.previewPolicy(input.body)
      if (input.action === 'activate') return http.activatePolicy(input.body)
      return http.policyCommand(input.action, input.body)
    },
    retry: false,
    onSettled: async (_data, _error, { action }) => {
      await Promise.all([
        refresh(),
        ...(['automatic-safety/recover', 'automatic-safety/pause'].includes(action)
          ? [invalidateBackgroundQueries(queryClient, { queryKey: ['review-preparation'] })]
          : []),
      ])
    },
  })

  // The name is part of the policy document, and only this notebook activates
  // one, so an edited draft has to say it is custom or it activates under the
  // seeded built-in name. An exact revert takes the active name back.
  const editDraft = (edited: PolicyDraft) => {
    const active = { ...edited, name: data.state.profile.name }
    const changed = derivePolicyDraftDisplay(data.state, active, fieldDefaults).changed
    setDraft(changed ? { ...edited, name: 'Custom policy' } : active)
    setSnapshotId(undefined)
    setPreviewActivePolicyId(undefined)
    setPreview(undefined)
    setStage('editing')
    setStatus({
      tone: 'neutral',
      message: changed ? 'Draft has unreviewed changes.' : 'Draft matches the active policy.',
    })
  }
  const safetySummaryRef = useRef<HTMLElement>(null)
  const runAuxiliary = async <P extends PolicyCommandPath>(
    action: P,
    body?: PolicyCommandBody<P>,
  ) => {
    setStatus({ tone: 'neutral', message: 'Updating policy state…' })
    try {
      await command.mutateAsync({ action, body })
      setStatus({
        tone: 'success',
        message:
          action === 'automatic-safety/recover'
            ? 'Suspension recovered. Automatic decisions resume once they are not paused or suspended.'
            : 'Policy state updated.',
      })
      if (action === 'automatic-safety/recover') safetySummaryRef.current?.focus()
      // The dismissed update took its focused button with it, and a retry that
      // reconciles the failures will take its own.
      if (action.endsWith('/acknowledge')) finishMessageRef.current?.focus()
      if (action.endsWith('/retry')) setRetried(true)
    } catch (error) {
      setStatus(
        uncertainWriteOutcome(error)
          ? {
              tone: 'warning',
              message: 'No answer came back. Check whether the policy changed before trying again.',
            }
          : {
              tone: 'error',
              message: refusalCopy(error, 'Nothing was changed. Try again.'),
            },
      )
    }
  }
  const validateAndPreview = async () => {
    setStage('validating')
    setSnapshotId(undefined)
    setPreviewActivePolicyId(undefined)
    setPreview(undefined)
    setStatus({ tone: 'neutral', message: 'Validating the draft…' })
    let phase: 'validate' | 'drafts' | 'preview' = 'validate'
    try {
      await command.mutateAsync({ action: 'validate', body: draft })
      phase = 'drafts'
      setStatus({ tone: 'neutral', message: 'Validation passed. Saving the draft…' })
      const saved = (await command.mutateAsync({ action: 'drafts', body: draft })) as {
        policy_id: string
      }
      setSnapshotId(saved.policy_id)
      phase = 'preview'
      setStatus({ tone: 'neutral', message: 'Draft saved. Previewing undecided works…' })
      const result = (await command.mutateAsync({
        action: 'preview',
        body: draft,
      })) as PolicyPreviewView
      // P-X4 (ADR-0091): the preview is of the snapshot just saved. It
      // compares two answers, so no single mapper can check it.
      if (result.policy_id !== saved.policy_id) throw new ApiError(502, 'policy_snapshot_mismatch')
      setPreview(result)
      setPreviewActivePolicyId(result.active_policy_id)
      setStage('preview')
      setStatus({
        tone: 'success',
        message: 'Impact preview ready. The active policy is unchanged.',
      })
    } catch (error) {
      setStage('editing')
      // A snapshot is content-addressed, so saving the same draft again is
      // harmless; the refreshed state shows whether this one was kept. This
      // comes first, so a lost or unreadable answer to the save says so too.
      if (phase === 'drafts' && uncertainWriteOutcome(error))
        setStatus({
          tone: 'warning',
          message:
            'No answer came back while saving the draft. Choose Validate and preview to try again.',
        })
      else if (error instanceof CsrfRenewedError)
        setStatus({ tone: 'error', message: sessionRefreshedCopy })
      else if (error instanceof ApiError && error.status === 422)
        setStatus({
          tone: 'error',
          message: 'The draft was not accepted. Check each field, then try again.',
        })
      else if (phase === 'preview')
        setStatus({
          tone: 'error',
          message:
            "The preview didn't load. Nothing was changed. Choose Validate and preview to try again.",
        })
      else if (phase === 'drafts')
        setStatus({
          tone: 'error',
          message: 'Validation passed, but the draft was not saved. Try again.',
        })
      else
        setStatus({
          tone: 'error',
          message: "The draft couldn't be validated. Nothing was saved. Try again.",
        })
    }
  }
  const activate = async () => {
    if (!snapshotId || !preview || !previewActivePolicyId) return
    setStatus({ tone: 'neutral', message: 'Activating the reviewed policy…' })
    try {
      // The client checks that the answer activated this snapshot (P-X3).
      const result = (await command.mutateAsync({
        action: 'activate',
        body: {
          policy_id: snapshotId,
          expected_active_policy_id: previewActivePolicyId,
          expected_active_generation: preview.active_generation,
        },
      })) as PolicyActivationView
      setActivatedTransitionId(result.transition?.transition_id)
      setStage('activated')
      setStatus({
        tone: 'success',
        message: 'Policy activated.',
      })
    } catch (error) {
      if (
        error instanceof AmbiguousMutationError ||
        error instanceof BuildMismatchError ||
        (error instanceof ApiError && error.status >= 500)
      ) {
        setStage('preview')
        setStatus({
          tone: 'warning',
          message:
            'No answer came back. Check whether the policy was activated before activating it again.',
        })
      } else if (
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === 'policy_activation_is_stale'
      ) {
        setSnapshotId(undefined)
        setPreviewActivePolicyId(undefined)
        setPreview(undefined)
        setStage('editing')
        setStatus({
          tone: 'warning',
          message:
            'The active policy changed before activation. Check your changes against it, then validate again.',
        })
      } else if (
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === 'policy_transition_is_processing'
      ) {
        setStage('preview')
        setStatus({
          tone: 'warning',
          message: 'The previous policy update is still running. Try again once it finishes.',
        })
      } else {
        setStatus({
          tone: 'error',
          message: refusalCopy(error, 'The policy was not activated. Try again.'),
        })
      }
    }
  }
  const openChapter = async (section: PolicySection) => {
    await navigate({ search: { section }, replace: true })
    requestAnimationFrame(() =>
      document.getElementById(`policy-${section}`)?.focus({ preventScroll: true }),
    )
  }
  const automaticAllowed = posture.posture === 'preview' || posture.posture === 'automatic'
  const qualification = policyAutomaticQualification(data.state)
  const draftDisplay = derivePolicyDraftDisplay(data.state, draft, fieldDefaults)
  const fieldChanged = (field: FieldDefinition) => {
    const fallback = fieldDefaults[field.key]
    const activeOption = data.state.profile.options[field.key] ?? fallback?.option ?? ''
    const activeDestination =
      data.state.profile.destinations[field.key] ?? fallback?.destination ?? 'ask'
    const option = draft.options[field.key] ?? fallback?.option ?? ''
    const destination = draft.destinations[field.key] ?? fallback?.destination ?? 'ask'
    return activeOption !== option || activeDestination !== destination
  }
  const changedFields = fields.filter(fieldChanged)
  const currentChapter = chapters.find((chapter) => chapter.key === activeChapter) ?? {
    key: 'identity' as const,
    label: 'Identity',
  }
  const visibleFields = fields.filter((field) => field.chapter === activeChapter)
  const changedChapterLabels = chapters
    .filter((chapter) => changedFields.some((field) => field.chapter === chapter.key))
    .map((chapter) => chapter.label)
  // A library's changed rules are previewed only where one of its sampled
  // comparisons evaluated that field. Any other changed rule was not previewed
  // at all, so it can be reported neither as unchanged nor as a change.
  const { previewLibraries, previewRows } = useMemo(
    () => ({
      previewLibraries: (preview?.libraries ?? []).map((library) => {
        const evaluated = new Set(
          library.comparisons.flatMap((comparison) =>
            comparison.fields.map((field) => field.draft.field),
          ),
        )
        return {
          ...library,
          previewed: library.affected_categories.filter((field) => evaluated.has(field)),
          unpreviewed: library.affected_categories.filter((field) => !evaluated.has(field)),
        }
      }),
      previewRows:
        preview?.libraries
          .flatMap((library) =>
            library.cases.flatMap((item, caseIndex) => {
              const comparison = library.comparisons[caseIndex]
              if (!comparison) return []
              return comparison.fields.map((field, fieldIndex) => {
                const projected = policyImpactDisplay(
                  field.draft,
                  field.draft_destination === 'ask',
                )
                const before = policyImpactDisplay(field.active, field.active_destination === 'ask')
                return {
                  caseId: item.report_id,
                  order: fieldIndex,
                  id: `${item.report_id}:${field.draft.field}`,
                  library: library.library,
                  media: comparison.media_kind,
                  work: item.work_label,
                  field: readableCategory(field.draft.field),
                  projected,
                  impact: {
                    changed: field.changed,
                    detail: field.changed
                      ? `${policyDestinationLabels[field.active_destination]} now: ${before.detail.replace(/[^.]$/u, '$&.')} ${policyDestinationLabels[field.draft_destination]} in the draft: ${projected.detail.replace(/[^.]$/u, '$&.')}`
                      : projected.detail,
                    technicalDetail: `Active ${before.technicalDetail}; draft ${projected.technicalDetail}`,
                  },
                }
              })
            }),
          )
          .sort((a, b) => a.order - b.order) ?? [],
    }),
    [preview],
  )
  const changedRows = previewRows.filter((row) => row.impact.changed)
  const sampledWorks = previewLibraries.reduce((count, library) => count + library.cases.length, 0)
  const mediaSummary = (['ebook', 'audiobook'] as const)
    .flatMap((media) => {
      const rows = changedRows.filter((row) => row.media === media).length
      const works = previewLibraries.reduce(
        (count, library) =>
          count +
          library.comparisons.filter((comparison) => comparison.media_kind === media).length,
        0,
      )
      return works
        ? [
            `${media === 'ebook' ? 'Ebooks' : 'Audiobooks'}: ${String(works)} sampled ${works === 1 ? 'work' : 'works'}${rows ? ` · ${String(rows)} changed ${rows === 1 ? 'field' : 'fields'}` : ''}`,
          ]
        : []
    })
    .join(' · ')
  const unchangedRows = previewRows.filter((row) => !row.impact.changed)
  const mediaQualification = (
    field: FieldDefinition,
    option: string,
    media: 'ebook' | 'audiobook',
  ) => {
    const entries = (
      media === 'ebook' ? data.state.rich_qualification : data.state.audiobook_qualification
    ).filter((entry) => entry.field === field.key && entry.option === option)
    const available = entries.filter((entry) => entry.automatic_available)
    // Each value is a sentence with no label of its own, so it reads
    // plainly after "Ebooks:" and "Automatic." (#1760).
    if (!available.length) return 'No automatic effect. Needs you.'
    if (media === 'ebook' && field.key === 'cover_order')
      return option === 'replace_if_strictly_better'
        ? 'Fill empty only. Confirmed covers are kept.'
        : 'Fill empty, or replace only with a confirmed better cover.'
    if (media === 'audiobook' && field.key === 'cover_order')
      return 'Replace a small current cover only with a clearly larger confirmed one.'
    if (field.key === 'language_rendering') return `${operationScopeLabels.fill_or_respell}.`
    const grouped = new Map<TargetDestination, string[]>()
    for (const entry of available) {
      if ('operation_scope' in entry)
        return `${entry.operation_scope === 'fill' ? 'Fill empty only' : operationScopeLabels[entry.operation_scope]}.`
      const operations = grouped.get(entry.destination) ?? []
      operations.push(operationWords[entry.operation])
      grouped.set(entry.destination, operations)
    }
    // One sentence per set of operations, with the fields as its object:
    // "Fill empty, keep current or replace authors and narrators." A lone
    // fill reads "Fill tags if empty.", right for one field or many (#1760).
    const byOperations = new Map<string, { operations: string[]; fields: string[] }>()
    for (const [destination, operations] of grouped) {
      const key = operations.join('|')
      const label = destinationLabel(destination)
      const field = label === label.toUpperCase() ? label : label.toLowerCase()
      const group = byOperations.get(key) ?? { operations, fields: [] }
      group.fields.push(field)
      byOperations.set(key, group)
    }
    const sentences = [...byOperations.values()].map(({ operations, fields }) => {
      const text =
        operations.length === 1 && operations[0] === 'fill empty'
          ? `fill ${plainList(fields, 'and')} if empty`
          : `${plainList(operations, 'or')} ${plainList(fields, 'and')}`
      return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`
    })
    if (media === 'audiobook' && field.key === 'contributor_names' && option !== 'canonical')
      sentences.push('No automatic effect on narrators.')
    return sentences.join(' ')
  }

  const mediaBehavior = (field: FieldDefinition, option: string, media: 'ebook' | 'audiobook') => {
    const destination =
      draft.destinations[field.key] ?? fieldDefaults[field.key]?.destination ?? 'ask'
    const eligibility = mediaQualification(field, option, media)
    if (destination === 'automatic') return `Automatic. ${eligibility}`
    const selected =
      destination === 'ignore'
        ? 'Ignored. This field is left alone.'
        : destination === 'ask'
          ? 'Ask me. Your choice.'
          : 'Not supported. This field is left alone.'
    return `${selected} If set to Automatic: ${eligibility}`
  }

  const impactTotals = useMemo(
    () =>
      preview
        ? {
            currentWorks: preview.libraries.reduce(
              (total, library) => total + library.current_unapplied_count,
              0,
            ),
            libraries: preview.libraries.length,
            ...policyImpactTotals(previewRows),
          }
        : undefined,
    [preview, previewRows],
  )
  // A fresh container carries a seeded generation-1 row so the baseline has a
  // durable identity. It is not an activation, and reporting it as one told the
  // owner they had completed a policy update of zero items they never started.
  const baselinePolicy = transition.data?.origin === 'baseline'
  const ownerActivation =
    transition.data && !baselinePolicy && !transition.data.acknowledged
      ? transition.data
      : undefined
  // Work still running and a failure still to retry are the owner's whoever is
  // looking. A clean completion acknowledges one activation, so it is shown to
  // the session that performed it and expires with that session.
  const activationNotice =
    ownerActivation &&
    (ownerActivation.state === 'processing' ||
      ownerActivation.failed > 0 ||
      ownerActivation.transition_id === activatedTransitionId)
      ? ownerActivation
      : undefined
  const editorRef = useRef<HTMLDivElement>(null)
  const trayRef = useRef<HTMLElement>(null)
  const policyStatusRef = useRef<HTMLParagraphElement>(null)
  // A preview takes focus from the button that produced it; a refusal or
  // uncertainty that returns here is said by the status, which runs after.
  const resultRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (stage !== 'preview') return
    resultRef.current?.focus({ preventScroll: true })
    resultRef.current?.scrollIntoView({ block: 'nearest' })
  }, [stage])
  useEffect(() => {
    if (!['error', 'warning'].includes(status.tone)) return
    policyStatusRef.current?.focus({ preventScroll: true })
    policyStatusRef.current?.scrollIntoView({ block: 'nearest' })
  }, [status])
  const activationReviewRef = useRef<HTMLElement>(null)
  const activationDescriptionId = useId()
  const cutoverRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (stage !== 'activation') return
    activationReviewRef.current?.focus({ preventScroll: true })
    activationReviewRef.current?.scrollIntoView({ block: 'center' })
  }, [stage])
  // Activating unmounts the focused Activate button; the success message it
  // produced is where focus belongs next, not the document body.
  const finishMessageRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (stage !== 'activated') return
    finishMessageRef.current?.focus({ preventScroll: true })
    finishMessageRef.current?.scrollIntoView({ block: 'nearest' })
  }, [stage])
  // After a retry renders, focus goes to the notice while it is shown, or to
  // the finish message once a reconciled notice is gone, never to the empty
  // region it leaves.
  const [retried, setRetried] = useState(false)
  useEffect(() => {
    if (!retried) return
    setRetried(false)
    ;(activationNotice ? cutoverRef : finishMessageRef).current?.focus({ preventScroll: true })
  }, [activationNotice, retried])
  // A notice that ends under focus, as a retried transition finishing does,
  // takes the region's focus with it, so the finish message takes it over.
  const noticeShown = Boolean(activationNotice)
  const noticeWasShown = useRef(noticeShown)
  useEffect(() => {
    const ended = noticeWasShown.current && !noticeShown
    noticeWasShown.current = noticeShown
    const active = document.activeElement
    if (ended && (active === cutoverRef.current || active === document.body))
      finishMessageRef.current?.focus({ preventScroll: true })
  }, [noticeShown])

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const descriptionLink =
        activeChapter === 'curation' && window.location.hash === '#description-advice'
      const target = document.getElementById(
        descriptionLink ? 'description-advice-heading' : `policy-${activeChapter}`,
      )
      target?.focus({ preventScroll: true })
      if (descriptionLink) target?.scrollIntoView({ block: 'start' })
    })
    return () => cancelAnimationFrame(frame)
  }, [activeChapter])

  // The sticky tray spans both columns, so the sidebar must stop above it or
  // the tray covers the safety controls at the bottom of the left column. The
  // tray's height is content-dependent (stage copy, reflow, zoom) and the page
  // keeps a tail below the editor, so the reserve is measured rather than
  // guessed; the stylesheet subtracts it from the sidebar's bounded height.
  useLayoutEffect(() => {
    const editor = editorRef.current
    const tray = trayRef.current
    if (!editor || !tray) return undefined
    const measure = () => {
      const bottom = editor.getBoundingClientRect().bottom + window.scrollY
      const tail = Math.max(document.documentElement.scrollHeight - bottom, 0)
      editor.style.setProperty(
        '--policy-tray-reserve',
        `${String(Math.ceil(tray.offsetHeight + tail + 24))}px`,
      )
    }
    measure()
    window.addEventListener('resize', measure)
    return () => {
      window.removeEventListener('resize', measure)
    }
  })

  const completedValidate = stage === 'preview' || stage === 'activation' || stage === 'activated'
  const completedPreview = stage === 'activation' || stage === 'activated'
  const currentStep =
    stage === 'editing' || stage === 'validating' ? 0 : stage === 'preview' ? 1 : 2
  const finishTitle =
    stage === 'activated'
      ? 'Policy activated'
      : stage === 'activation'
        ? 'Ready to activate'
        : stage === 'preview'
          ? impactTotals?.changedWorks
            ? `${String(impactTotals.changedWorks)} sampled ${impactTotals.changedWorks === 1 ? 'work' : 'works'} with changes`
            : 'Impact preview ready'
          : stage === 'validating'
            ? 'Validating the draft…'
            : draftDisplay.changed
              ? `${String(changedFields.length)} ${changedFields.length === 1 ? 'change' : 'changes'} ready to validate`
              : 'Draft matches the active policy'
  // Once this activation's transition is read, its progress section (or its
  // dismissal) owns reconciliation, so the tray no longer states it.
  const finishDetail =
    stage === 'activated'
      ? activationNotice ||
        (activatedTransitionId !== undefined &&
          transition.data?.transition_id === activatedTransitionId)
        ? ''
        : impactTotals?.currentWorks
          ? 'Undecided works are being checked against the new policy.'
          : 'No undecided works needed checking.'
      : stage === 'activation'
        ? 'Check the preview before activating.'
        : stage === 'preview'
          ? `Validated${impactTotals?.materialChanges ? ` · ${String(impactTotals.materialChanges)} ${impactTotals.materialChanges === 1 ? 'changed field' : 'changed fields'} in the sample` : ''}${changedChapterLabels.length ? ` · you changed ${changedChapterLabels.join(', ')}` : ''}. Activate to make it the active policy. Reloading the page starts again from the active policy.`
          : stage === 'validating'
            ? ''
            : draftDisplay.changed
              ? `Changes in ${changedChapterLabels.join(', ')}.`
              : 'Change any field to create a new draft.'

  return (
    <div className="cm-policy-route" data-route-focus tabIndex={-1}>
      <header className="cm-page-heading">
        <div>
          <h1>Library policy</h1>
        </div>
      </header>
      <div className="cm-policy-editor" ref={editorRef}>
        <aside className="cm-policy-sidebar">
          <header>
            <strong>{data.state.profile.name}</strong>
            <small>Active policy</small>
          </header>
          <nav aria-label="Policy chapters">
            {chapters.map((chapter) => {
              const chapterFields = fields.filter((field) => field.chapter === chapter.key)
              const changes = chapterFields.filter(fieldChanged).length
              const labelId = `policy-${chapter.key}-label`
              return (
                <button
                  key={chapter.key}
                  type="button"
                  aria-current={activeChapter === chapter.key ? 'page' : undefined}
                  aria-labelledby={labelId}
                  aria-describedby={changes ? `policy-${chapter.key}-changes` : undefined}
                  onClick={() => void openChapter(chapter.key)}
                >
                  <span>
                    <strong id={labelId}>{chapter.label}</strong>
                  </span>
                  {changes ? (
                    <ChangeCount id={`policy-${chapter.key}-changes`} count={changes} />
                  ) : null}
                </button>
              )
            })}
          </nav>
          {changedFields.length ? <p className="cm-policy-chapter-legend">Changed fields</p> : null}
          <PolicySafety
            posture={posture}
            safety={safety}
            pending={command.isPending}
            summaryRef={safetySummaryRef}
            run={runAuxiliary}
          />
        </aside>
        <section className="cm-policy-content" id={`policy-${activeChapter}`} tabIndex={-1}>
          <header>
            <div>
              <h2>{currentChapter.label}</h2>
            </div>
          </header>
          <div className="cm-policy-field-list">
            {visibleFields.map((field) => {
              const option = draft.options[field.key] ?? field.options[0] ?? ''
              const destination =
                draft.destinations[field.key] ?? fieldDefaults[field.key]?.destination ?? 'ask'
              const qualified = automaticQualified(field, option, qualification)
              const automaticAvailable = qualified && automaticAllowed
              const changed = fieldChanged(field)
              return [
                <section
                  key={field.key}
                  className={`cm-policy-field ${changed ? 'is-changed' : ''}`}
                  aria-label={field.label}
                >
                  <header>
                    <span>
                      <strong>{field.label}</strong>
                      {changed ? <StatusPill tone="notice">Draft changed</StatusPill> : null}
                    </span>
                    <p>{field.meaning}</p>
                    <p className="cm-policy-media-scope">
                      <strong>Ebooks:</strong> {mediaBehavior(field, option, 'ebook')}
                      <br />
                      <strong>Audiobooks:</strong> {mediaBehavior(field, option, 'audiobook')}
                    </p>
                    {data.state.audiobook_qualification.some(
                      (entry) => entry.field === field.key && entry.option === option,
                    ) ? (
                      <Disclosure summary="Audiobook operations">
                        <ul>
                          {data.state.audiobook_qualification
                            .filter((entry) => entry.field === field.key && entry.option === option)
                            .map((entry) => (
                              <li key={entry.identity} data-qualification-identity={entry.identity}>
                                {destinationLabel(entry.destination)}:{' '}
                                {audiobookOperationLabels[entry.operation]}.{' '}
                                {entry.automatic_available
                                  ? 'Automatic offered.'
                                  : 'Automatic not offered.'}
                              </li>
                            ))}
                        </ul>
                      </Disclosure>
                    ) : null}
                    <Disclosure summary="What this changes">
                      {field.key === 'series' ? (
                        <p>
                          One source can be enough for a series. Its number comes from that source
                          and is not checked for each book.
                        </p>
                      ) : null}
                      <ul>
                        {field.options.map((value) => (
                          <li key={value}>
                            {field.optionLabels[value] ?? value}:{' '}
                            <span>
                              {representationConsequence(
                                policyRepresentationGuidance[`${field.key}:${value}`],
                              )}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </Disclosure>
                  </header>
                  {field.unsupported ? (
                    <div className="cm-policy-unavailable">
                      <strong>Not supported</strong>
                      <span>This field is shown but cannot be changed.</span>
                    </div>
                  ) : (
                    <>
                      <div className="cm-policy-mode-control">
                        <span className="cm-policy-control-heading">
                          <strong>Setting</strong>
                          {/* The mode is said once, by the Mode disclosure; the note
                              under the option says it while Automatic is chosen. */}
                          {!qualified && destination !== 'automatic' ? (
                            <small>Automatic not offered</small>
                          ) : null}
                        </span>
                        <SegmentedControl
                          label={`${field.label} setting`}
                          value={destination}
                          disabled={command.isPending}
                          options={[
                            {
                              value: 'automatic',
                              label: 'Automatic',
                              disabled: !automaticAvailable,
                            },
                            { value: 'ask', label: 'Ask me' },
                            { value: 'ignore', label: 'Ignore' },
                          ]}
                          onChange={(next) =>
                            editDraft({
                              ...draft,
                              destinations: { ...draft.destinations, [field.key]: next },
                            })
                          }
                        />
                      </div>
                      <label className="cm-policy-representation">
                        <span>Option</span>
                        <Dropdown
                          ariaLabel={`${field.label} option`}
                          value={option}
                          options={field.options.map((value) => ({
                            value,
                            label: field.optionLabels[value] ?? value,
                          }))}
                          disabled={command.isPending || field.options.length === 1}
                          onChange={(nextOption) => {
                            const movesToAsk =
                              destination === 'automatic' &&
                              !automaticQualified(field, nextOption, qualification)
                            editDraft({
                              ...draft,
                              options: { ...draft.options, [field.key]: nextOption },
                              destinations: {
                                ...draft.destinations,
                                [field.key]: movesToAsk ? 'ask' : destination,
                              },
                            })
                            if (movesToAsk)
                              setStatus({
                                tone: 'warning',
                                message: `${field.label} changed from Automatic to Ask me, because Automatic is not offered for this option.`,
                              })
                          }}
                        />
                        {destination === 'automatic' && !qualified ? (
                          <small className="cm-policy-representation-consequence">
                            Automatic is not offered for this option. Choose Ask me, Ignore or
                            another option.
                          </small>
                        ) : null}
                      </label>
                    </>
                  )}
                </section>,
                field.key === 'cover_order' ? (
                  <DescriptionAdviceSettings key="description-advice" http={http} />
                ) : null,
                field.key === 'cover_order' ? (
                  <MatchingSettings key="book-matching" http={http} />
                ) : null,
              ]
            })}
          </div>
        </section>
        {(stage === 'preview' || stage === 'activation') && preview ? (
          <section className="cm-policy-result" ref={resultRef} tabIndex={-1}>
            <div className="cm-policy-preview">
              <header>
                <div>
                  <strong>Impact preview</strong>
                </div>
                {impactTotals?.changedWorks ? (
                  <b aria-label={`${String(impactTotals.changedWorks)} sampled works with changes`}>
                    {String(impactTotals.changedWorks)}
                  </b>
                ) : null}
              </header>
              <p>
                {sampledWorks ? (
                  <>
                    {sampledWorks} of {impactTotals?.currentWorks} undecided works sampled
                    {' · '}up to 20 across all libraries
                  </>
                ) : (
                  'No undecided works to sample'
                )}
                {preview.libraries.some((library) => library.cases_truncated)
                  ? ' · Additional works were not previewed'
                  : sampledWorks
                    ? ''
                    : '.'}
              </p>
              {mediaSummary ? <p>{mediaSummary}</p> : null}
              <p>
                Title, authors, narrators, date, publisher, language and cover are not previewed
                here.
              </p>
              {/* A library override can pin every edited field, so a changed draft
                  may still change nothing in any library. */}
              {previewLibraries.length > 0 &&
              previewLibraries.every((library) => !library.affected_categories.length) ? (
                <p>No library&apos;s fields change under this draft.</p>
              ) : null}
              <div id="policy-preview-outcomes" className="cm-policy-preview-list">
                {changedRows.length
                  ? changedRows.slice(0, expandedPreview === preview ? undefined : 6).map((row) => (
                      <div key={row.id} className={row.impact.changed ? 'is-change' : ''}>
                        <span className="cm-policy-work-mark" aria-hidden="true">
                          {row.work.slice(0, 2).toUpperCase()}
                        </span>
                        <span>
                          <strong>{row.work}</strong>
                          <small>
                            {row.media === 'ebook' ? 'Ebook' : 'Audiobook'} ·{' '}
                            {libraryName(row.library)} · {row.field}
                          </small>
                        </span>
                        <span className="cm-policy-impact">
                          {/* Only changed rows are listed here (#1765). */}
                          <b>Policy change</b>
                          <small>· {row.impact.detail}</small>
                          <details>
                            <summary>Technical detail</summary>
                            <code>{row.impact.technicalDetail}</code>
                          </details>
                        </span>
                      </div>
                    ))
                  : previewLibraries
                      .filter((library) => library.previewed.length)
                      .map((library) => (
                        <div key={library.library}>
                          <span className="cm-policy-work-mark" aria-hidden="true">
                            {libraryName(library.library).slice(0, 2).toUpperCase()}
                          </span>
                          <span>
                            <strong>{libraryName(library.library)}</strong>
                            <small>{library.previewed.map(readableCategory).join(', ')}</small>
                          </span>
                          <span className="cm-policy-impact">
                            <b>No changes from the draft in this sample</b>
                            <small>
                              ·{' '}
                              {library.current_unapplied_count === 0
                                ? 'No undecided works left'
                                : `${String(library.current_unapplied_count)} undecided ${library.current_unapplied_count === 1 ? 'work' : 'works'}`}
                            </small>
                          </span>
                        </div>
                      ))}
                {previewLibraries.flatMap((library) =>
                  library.unpreviewed.map((field) => (
                    <div key={`${library.library}:${field}`} className="is-unpreviewed">
                      <span className="cm-policy-work-mark" aria-hidden="true">
                        {libraryName(library.library).slice(0, 2).toUpperCase()}
                      </span>
                      <span>
                        <strong>{readableCategory(field)}</strong>
                        <small>{libraryName(library.library)}</small>
                      </span>
                      <span className="cm-policy-impact">
                        <b>Not previewed.</b>
                        <small>This rule takes effect on the next preparation.</small>
                      </span>
                    </div>
                  )),
                )}
              </div>
              {changedRows.length > 6 ? (
                <footer>
                  <button
                    type="button"
                    aria-expanded={expandedPreview === preview}
                    aria-controls="policy-preview-outcomes"
                    onClick={() =>
                      setExpandedPreview(expandedPreview === preview ? undefined : preview)
                    }
                  >
                    {expandedPreview === preview
                      ? 'Show fewer outcomes'
                      : `Show ${String(changedRows.length - 6)} more ${changedRows.length === 7 ? 'outcome' : 'outcomes'}`}
                  </button>
                </footer>
              ) : null}
            </div>
            {unchangedRows.length ? (
              <details className="cm-policy-projected-context">
                <summary>Unchanged outcomes ({unchangedRows.length})</summary>
                <p>
                  These compare the draft with the library's current values. The draft does not
                  cause them.
                </p>
                {unchangedRows.map((row) => (
                  <p key={row.id}>
                    <strong>{row.work}</strong> · {row.media === 'ebook' ? 'Ebook' : 'Audiobook'} ·{' '}
                    {row.field}: {row.projected.detail}
                  </p>
                ))}
              </details>
            ) : null}
            {stage === 'activation' ? (
              <footer
                className="cm-policy-activation"
                ref={activationReviewRef}
                tabIndex={-1}
                role="group"
                aria-label="Review policy activation"
                aria-describedby={activationDescriptionId}
              >
                <strong>Activate this reviewed draft?</strong>
                <span id={activationDescriptionId}>
                  Changes already written stay as they are.{' '}
                  {keptPermission === undefined
                    ? consent.isError
                      ? 'Write permission is unchanged: each library keeps what it has.'
                      : 'Checking which libraries keep write permission…'
                    : keptPermission.length
                      ? `Write permission stays on for ${libraryList(keptPermission)}.`
                      : 'No library has write permission, and activating grants none.'}
                </span>
                <ul>
                  {changedFields.map((field) => (
                    <li key={field.key}>
                      <strong>{field.label}</strong>
                      <br />
                      Ebooks: {mediaBehavior(field, draft.options[field.key] ?? '', 'ebook')}
                      <br />
                      Audiobooks:{' '}
                      {mediaBehavior(field, draft.options[field.key] ?? '', 'audiobook')}
                    </li>
                  ))}
                </ul>
              </footer>
            ) : null}
          </section>
        ) : null}
        <FinishTray
          label="Policy draft and activation"
          className={`stage-${stage}`}
          ref={trayRef}
          summary={
            <>
              {['error', 'warning'].includes(status.tone) ? null : (
                <div className="cm-policy-finish-message" ref={finishMessageRef} tabIndex={-1}>
                  <strong>{finishTitle}</strong>
                  {finishDetail ? <span>{finishDetail}</span> : null}
                </div>
              )}
              <p
                className={`policy-status ${status.tone} ${['error', 'warning'].includes(status.tone) ? '' : 'sr-only'}`}
                ref={policyStatusRef}
                tabIndex={-1}
                role={status.tone === 'error' ? 'alert' : 'status'}
              >
                {status.message}
              </p>
            </>
          }
          progress={
            <ol aria-label="Policy activation steps">
              {['Validate', 'Preview', 'Activate'].map((label, index) => {
                const done =
                  index === 0
                    ? completedValidate
                    : index === 1
                      ? completedPreview
                      : stage === 'activated'
                return (
                  <li
                    key={label}
                    className={done ? 'done' : ''}
                    aria-current={
                      stage !== 'activated' && currentStep === index ? 'step' : undefined
                    }
                  >
                    <span>{done ? <Check /> : index + 1}</span>
                    <small>{label}</small>
                  </li>
                )
              })}
            </ol>
          }
          actions={
            <>
              {stage === 'preview' ? (
                <button disabled={command.isPending} onClick={() => setStage('editing')}>
                  Back to editing
                </button>
              ) : null}
              {stage === 'activation' ? (
                <button disabled={command.isPending} onClick={() => setStage('preview')}>
                  Back to preview
                </button>
              ) : null}
              {/* One button through validation, so focus stays on it while it runs. */}
              {stage === 'editing' || stage === 'validating' ? (
                <Button
                  variant="primary"
                  disabled={stage === 'editing' && !draftDisplay.changed}
                  aria-disabled={command.isPending || stage === 'validating' || undefined}
                  onClick={() => {
                    if (stage === 'editing' && !command.isPending) void validateAndPreview()
                  }}
                >
                  {stage === 'validating' ? 'Validating…' : 'Validate and preview'}
                </Button>
              ) : null}
              {stage === 'preview' ? (
                <Button
                  variant="primary"
                  disabled={command.isPending || !snapshotId || !preview || !previewActivePolicyId}
                  onClick={() => setStage('activation')}
                >
                  Review activation
                </Button>
              ) : null}
              {stage === 'activation' ? (
                <Button
                  variant="primary"
                  disabled={command.isPending}
                  onClick={() => void activate()}
                >
                  {command.isPending ? 'Activating…' : 'Activate policy'}
                </Button>
              ) : null}
              {stage === 'activated' ? (
                <Button variant="primary" onClick={() => setStage('editing')}>
                  Continue editing
                </Button>
              ) : null}
            </>
          }
        >
          {stage === 'validating' ? (
            <section className="cm-policy-validation" aria-label="Validation in progress">
              <header>
                <span className="cm-policy-validation-spinner" />
                <div>
                  <small>The draft is saved only if every check passes.</small>
                </div>
              </header>
              <ul>
                <li>
                  <span />
                  <div>
                    <strong>Fields</strong>
                    <small>A setting and an option for each of the 14</small>
                  </div>
                </li>
                <li>
                  <span />
                  <div>
                    <strong>Automatic</strong>
                    <small>Offered for each field set to Automatic</small>
                  </div>
                </li>
                <li>
                  <span />
                  <div>
                    <strong>Libraries</strong>
                    <small>Which libraries the policy covers</small>
                  </div>
                </li>
              </ul>
            </section>
          ) : null}
        </FinishTray>
      </div>
      {/* The status region stays mounted, so a transition that is already
          finished when first read is inserted into a live region that exists
          and is announced. Empty, the section has no class, name or height. */}
      <section
        className={activationNotice ? 'policy-cutover' : undefined}
        aria-label={activationNotice ? 'Policy activation progress' : undefined}
        ref={cutoverRef}
        tabIndex={activationNotice ? -1 : undefined}
      >
        <div role="status">
          {activationNotice ? (
            <>
              <h2>
                {activationNotice.state === 'processing'
                  ? 'Applying updated policy'
                  : activationNotice.failed
                    ? 'Activation completed with failures'
                    : 'Activation complete'}
              </h2>
              <p>
                {activationNotice.total
                  ? `${String(activationNotice.completed)} of ${String(activationNotice.total)} undecided works checked.`
                  : 'No undecided works needed checking.'}
              </p>
            </>
          ) : null}
        </div>
        {activationNotice ? (
          <div className="policy-workflow-actions">
            {activationNotice.failed ? (
              <button
                aria-disabled={command.isPending || undefined}
                onClick={() => {
                  if (!command.isPending)
                    void runAuxiliary(
                      `transitions/${encodeURIComponent(activationNotice.transition_id)}/retry`,
                    )
                }}
              >
                Retry failed works
              </button>
            ) : null}
            {activationNotice.state !== 'processing' ? (
              <button
                disabled={command.isPending}
                onClick={() =>
                  void runAuxiliary(
                    `transitions/${encodeURIComponent(activationNotice.transition_id)}/acknowledge`,
                  )
                }
              >
                Dismiss update
              </button>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  )
}
