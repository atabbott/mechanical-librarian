import type { Ref } from 'react'

import { displayValue } from '../api/http'
import type { DecisionCommand, MediaKind } from '../api/http'
import type { useAutomaticHandling } from '../automaticHandling'
import { choiceRadios, moveChoiceFocus } from '../choiceGroup'
import { DescriptionAdvicePreparation } from '../components/DescriptionAdvice'
import type { useCoverPrepareFailure } from '../coverChoices'
import {
  authoredOwnerRecorded,
  automaticallySelected,
  choiceSelected,
  commandSelected,
  decisionQuestion,
  effectSummary,
  isLongDescription,
  valueText,
} from '../fieldDecision'
import type { ReviewField } from '../fieldDecision'
import {
  choiceContext,
  combinedAutomaticTags,
  comparisonNotices,
  fieldReason,
  noticeText,
  questionReason,
  selectedAutomaticLanguageForm,
} from '../fieldReasons'
import { destinationLabel } from '../labels'
import { ownerChoiceCount } from '../reviewChoiceCounts'
import type { useReviewDecisionController } from '../reviewDecisionController'
import { AutomaticHandlingPanel } from './AutomaticHandling'
import { MetadataChoice, RecordedFieldObservations } from './FieldDecisionChoices'
import { LibraryCoverChoices } from './FieldDecisionCover'
import {
  AsinAutomaticStatement,
  DescriptionAutomaticStatement,
  NarratorsAutomaticStatement,
  ProviderConsensusAutomaticStatement,
  RecordedOutcome,
  SeriesAutomaticStatement,
  SettledFieldContext,
  VerifiedManifestationIsbnAutomaticStatement,
} from './FieldDecisionOutcome'
import { OwnerControls } from './FieldDecisionOwner'
import { ownerOutcome } from '../fieldDecisionMode'
import type { fieldDecisionMode } from '../fieldDecisionMode'

// The Library page's decision canvas: one field of the selected work, its
// question, its choices and what recording one does. The page owns selection,
// the queue, the field rail and the completion summary.

type Choose = (
  field: ReviewField,
  outcome: DecisionCommand['outcome'],
  selectedValue?: DecisionCommand['selectedValue'],
  authoredDescription?: DecisionCommand['authoredDescription'],
) => string

export function FieldDecisionCanvas({
  field: selected,
  mode,
  reviewCaseId: selectedReviewCaseId,
  mediaKind,
  controller,
  inspectingCompletion,
  backToCompletion,
  queuePosition,
  frozenDestinations,
  expandedChoices,
  toggleChoiceEvidence,
  choose,
  coverPrepareFailure,
  ownerCommand,
  setOwnerCommand,
  ownerNotice,
  automaticHandling,
  retryButton,
  reloading,
}: {
  field: ReviewField
  mode: ReturnType<typeof fieldDecisionMode>
  reviewCaseId: string
  mediaKind: MediaKind
  controller: ReturnType<typeof useReviewDecisionController>
  /** The work is complete and the owner opened this field from its summary. */
  inspectingCompletion: boolean
  backToCompletion: () => void
  queuePosition: string
  frozenDestinations: readonly string[]
  expandedChoices: { has: (key: string) => boolean }
  toggleChoiceEvidence: (key: string) => void
  choose: Choose
  coverPrepareFailure: ReturnType<typeof useCoverPrepareFailure>
  ownerCommand: string | null
  setOwnerCommand: (key: string | null) => void
  ownerNotice: { caseId: string; destination: string; text: string } | null
  automaticHandling: ReturnType<typeof useAutomaticHandling>
  retryButton: Ref<HTMLButtonElement>
  reloading: boolean
}) {
  const {
    recorded: selectedRecorded,
    actionable: selectedActionable,
    revisitable: selectedRevisitable,
    ownerEntry: selectedOwnerEntry,
    optional: selectedOptional,
    advice: selectedAdvice,
    choiceMode: selectedChoiceMode,
  } = mode
  const ownerKey = `${selectedReviewCaseId}:${selected.destination}`
  const ownerStatusShown = ownerCommand === ownerKey
  const effect = effectSummary(selected)
  const choiceStatus = controller.choiceStatusFor(
    selected.destination,
    selectedChoiceMode && !ownerStatusShown,
  )
  const selectedCoverPreparation =
    selected.destination === 'cover' &&
    selected.coverConsideration?.current !== true &&
    // A kept cover asks for nothing but stays choosable (#1310).
    (selected.needsAttention || selected.classification === 'owner_choosable')
  const selectedCoverInspection =
    selected.destination === 'cover' &&
    (selected.coverConsideration !== null || selectedCoverPreparation)
  const selectedProgress = controller.phase(selected.destination)
  // The one-tap teaching line is for someone who has not yet seen what a tap
  // does. It says so on the first field only, and gives way to what is actually
  // happening once they have chosen.
  // An owner command says what it did in its own status, once (S4).
  const choiceProgress =
    selectedProgress && ownerOutcome(selectedProgress.command.outcome) ? null : selectedProgress
  const teachingLine =
    choiceProgress?.phase === 'recording'
      ? 'Recording choice…'
      : choiceProgress?.phase === 'confirmed' ||
          (!selectedProgress && authoredOwnerRecorded(selected))
        ? 'Choice recorded.'
        : selectedActionable && frozenDestinations.indexOf(selected.destination) === 0
          ? 'One tap records and opens the next field'
          : ''
  // The direct choices in reading order, so the radio group's one Tab stop and
  // each position come from a single list (#1290).
  type DirectChoice = Omit<Parameters<typeof MetadataChoice>[0], 'radio'>
  const currentChoice: DirectChoice | null = selected.decisionActions.includes('keep_current')
    ? {
        automatic: automaticallySelected(selected, selectedProgress),
        context: choiceContext(selected, null),
        current: true,
        disabled: controller.checking,
        expanded: expandedChoices.has(`${selected.destination}:current`),
        label: 'Current value',
        longDescription: isLongDescription(selected.destination, selected.currentValue),
        onChoose: () => choose(selected, 'keep_current'),
        recording:
          commandSelected(
            selectedProgress?.command ?? null,
            selected.destination,
            'keep_current',
          ) && selectedProgress?.phase === 'recording',
        selected: choiceSelected(selected, selectedProgress, 'keep_current'),
        toggle: () => toggleChoiceEvidence(`${selected.destination}:current`),
        value: valueText(selected.destination, selected.currentValue),
      }
    : null
  const proposedChoices: (DirectChoice & { id: string })[] =
    selected.decisionActions.includes('accept') && !selected.preservationOnly
      ? selected.proposedValues.map((value, index) => {
          const context = choiceContext(
            selected,
            selected.choiceContexts.filter((item) => item.selection === 'proposed')[index]
              ?.alternativeId ?? null,
          )
          const key = `${selected.destination}:${context?.alternativeId ?? String(index)}`
          return {
            id: key,
            automatic: automaticallySelected(selected, selectedProgress),
            context,
            current: false,
            disabled: controller.checking,
            // A proposed card is named by its value, never "Alternative N" (#1519).
            evidenceName: isLongDescription(selected.destination, displayValue(value.value))
              ? `Evidence for proposed description ${String(index + 1)}`
              : `Evidence for proposed value: ${valueText(selected.destination, displayValue(value.value))}`,
            expanded: expandedChoices.has(key),
            label: '',
            longDescription: isLongDescription(selected.destination, displayValue(value.value)),
            onChoose: () => choose(selected, 'accept', value),
            recording:
              commandSelected(
                selectedProgress?.command ?? null,
                selected.destination,
                'accept',
                value,
              ) && selectedProgress?.phase === 'recording',
            selected: choiceSelected(selected, selectedProgress, 'accept', value),
            toggle: () => toggleChoiceEvidence(key),
            value: valueText(selected.destination, displayValue(value.value)),
          }
        })
      : []
  const retainedAdviceChoice: (DirectChoice & { id: string })[] =
    selectedAdvice?.selected_advice &&
    selectedAdvice.selected_advice.candidate.candidate_id !== selectedAdvice.candidate?.candidate_id
      ? [
          {
            id: 'advice:retained',
            advice: selectedAdvice,
            automatic: selectedAdvice.selection?.origin === 'automatic_policy',
            retainedAdvice: true,
            context: undefined,
            current: false,
            disabled: true,
            expanded: false,
            label: 'Description adviser · Earlier choice',
            longDescription: isLongDescription(
              'description',
              selectedAdvice.selected_advice.candidate.output.description,
            ),
            onChoose: () => undefined,
            recording: false,
            selected: Boolean(
              selectedAdvice.selected_advice.current || selectedAdvice.application_outcome,
            ),
            toggle: () => undefined,
            value: selectedAdvice.selected_advice.candidate.output.description,
          },
        ]
      : []
  const adviceCandidate = selectedAdvice?.candidate
  const adviceChoice: (DirectChoice & { id: string })[] =
    selectedAdvice && adviceCandidate
      ? [
          {
            id: 'advice:candidate',
            advice: selectedAdvice,
            automatic:
              (selectedProgress === null || selectedProgress.phase === 'failed') &&
              selectedAdvice.selection?.origin === 'automatic_policy',
            retainedAdvice: selectedAdvice.selection?.candidate_id === adviceCandidate.candidate_id,
            context: undefined,
            current: false,
            disabled:
              !selected.editable ||
              !selectedAdvice.current ||
              controller.recording ||
              controller.checking ||
              selectedAdvice.state === 'requested' ||
              selectedAdvice.state === 'running',
            expanded: false,
            label: 'Description adviser',
            longDescription: isLongDescription('description', adviceCandidate.output.description),
            onChoose: () => {
              if (!selectedAdvice.current || !selected.editable) return
              choose(
                selected,
                'accept',
                { kind: 'text', value: adviceCandidate.output.description },
                { candidateId: adviceCandidate.candidate_id, binding: selectedAdvice.binding },
              )
            },
            recording:
              selectedProgress?.command.authoredDescription?.candidateId ===
                adviceCandidate.candidate_id && selectedProgress.phase === 'recording',
            selected:
              selectedProgress && selectedProgress.phase !== 'failed'
                ? selectedProgress.command.authoredDescription?.candidateId ===
                  adviceCandidate.candidate_id
                : Boolean(
                    (selectedAdvice.selected_advice?.current === true ||
                      selectedAdvice.application_outcome) &&
                    selectedAdvice.selection?.candidate_id === adviceCandidate.candidate_id,
                  ),
            toggle: () => undefined,
            value: adviceCandidate.output.description,
          },
        ]
      : []
  const newChoices = [...proposedChoices, ...retainedAdviceChoice, ...adviceChoice]
  const directChoices = choiceRadios(
    [...(currentChoice ? [{ ...currentChoice, id: 'current' }] : []), ...newChoices],
    (choice) => ({ checked: choice.selected, disabled: choice.disabled }),
  )

  return (
    <article
      key={selected.destination}
      className={`decision-canvas${controller.recording ? ' is-recording' : ''}${selectedChoiceMode ? '' : ' is-context-only'}`}
    >
      {inspectingCompletion ? (
        <button type="button" className="decision-back" onClick={backToCompletion}>
          ← Back to completion
        </button>
      ) : null}
      <header>
        <div>
          {/* Takes focus when a retry below it leaves nothing in its place. */}
          <h3 className="decision-question" tabIndex={-1}>
            {selectedActionable
              ? decisionQuestion(selected.destination)
              : destinationLabel(selected.destination)}
          </h3>
          {selectedActionable ? (
            // One line under the question: this field's own reason (#1519).
            <p
              className={selected.automaticWithheldReason ? 'automatic-withheld-reason' : undefined}
            >
              {questionReason(selected, mediaKind)}
            </p>
          ) : selectedRevisitable ? (
            <p>
              {selected.humanDecision === null && selected.manualHold
                ? 'Your earlier decision still holds this field.'
                : 'The recorded outcome is marked below.'}{' '}
              You can choose another value.
            </p>
          ) : selectedOptional ? (
            <p>{fieldReason(selected)}</p>
          ) : null}
          {selected.automaticWithheldReason && !selectedActionable ? (
            <p className="automatic-withheld-reason">{selected.automaticWithheldReason}</p>
          ) : null}
        </div>
        {selectedActionable ? (
          <dl>
            <div>
              <dt>Queue position</dt>
              <dd>{queuePosition}</dd>
            </div>
            <div>
              <dt>Choices</dt>
              <dd>{String(ownerChoiceCount(selected))}</dd>
            </div>
          </dl>
        ) : null}
      </header>
      {(selectedChoiceMode || selectedCoverInspection) && !controller.reloadRequired ? (
        selected.destination === 'cover' ? (
          <LibraryCoverChoices
            reviewCaseId={selectedReviewCaseId}
            mediaKind={mediaKind}
            field={selected}
            disabled={(!selectedChoiceMode && !selectedCoverPreparation) || controller.checking}
            expandedEvidence={expandedChoices}
            progress={selectedProgress}
            choose={(outcome, value) => choose(selected, outcome, value)}
            revealEvidence={toggleChoiceEvidence}
            prepareFailure={coverPrepareFailure}
          />
        ) : (
          <section
            className="direct-choice protected-choice"
            // The radio group inside is named by the question.
            aria-label={
              selectedActionable
                ? undefined
                : selectedOptional
                  ? `${destinationLabel(selected.destination)} choices`
                  : `${destinationLabel(selected.destination)} recorded choices`
            }
          >
            {selectedRevisitable ? (
              <>
                {combinedAutomaticTags(selected, mediaKind) ||
                selectedAutomaticLanguageForm(selected) ? (
                  <RecordedOutcome
                    field={selected}
                    mediaKind={mediaKind}
                    expanded={expandedChoices.has(`${selected.destination}:recorded`)}
                    toggle={() => toggleChoiceEvidence(`${selected.destination}:recorded`)}
                  />
                ) : null}
                <ProviderConsensusAutomaticStatement field={selected} />
                <DescriptionAutomaticStatement field={selected} />
                <SeriesAutomaticStatement field={selected} />
                <NarratorsAutomaticStatement field={selected} />
                <AsinAutomaticStatement field={selected} />
                <VerifiedManifestationIsbnAutomaticStatement field={selected} />
              </>
            ) : null}
            <div
              className={`direct-choice-grid${selected.preservationOnly ? ' preservation-only' : ''}`}
              role="radiogroup"
              aria-label={decisionQuestion(selected.destination)}
              onKeyDown={moveChoiceFocus}
            >
              {directChoices
                .filter((choice) => choice.current)
                .map(({ id, ...choice }) => (
                  <MetadataChoice key={id} {...choice} />
                ))}
              {(selected.decisionActions.includes('accept') && !selected.preservationOnly) ||
              selectedAdvice?.candidate ||
              selectedAdvice?.selected_advice ? (
                <div
                  className={`proposed-choice-list${selected.proposedValues.length + (selectedAdvice?.candidate ? 1 : 0) === 1 ? ' has-one-choice' : ''}`}
                >
                  <header>
                    <strong>Choose a new value</strong>
                    <small aria-live="polite" className={teachingLine ? 'is-active' : ''}>
                      {teachingLine}
                    </small>
                  </header>
                  {directChoices
                    .filter((choice) => !choice.current)
                    .map(({ id, ...choice }) => (
                      <MetadataChoice key={id} {...choice} />
                    ))}
                </div>
              ) : null}
            </div>
            {selectedAdvice ? (
              <DescriptionAdvicePreparation
                advice={selectedAdvice}
                reviewCaseId={selectedReviewCaseId}
                disabled={controller.recording || controller.reloadRequired || controller.checking}
              />
            ) : null}
          </section>
        )
      ) : selectedRecorded ? (
        <RecordedOutcome
          field={selected}
          mediaKind={mediaKind}
          expanded={expandedChoices.has(`${selected.destination}:recorded`)}
          toggle={() => toggleChoiceEvidence(`${selected.destination}:recorded`)}
        />
      ) : (
        <SettledFieldContext
          field={selected}
          actionable={selectedActionable}
          ownerEntry={selectedOwnerEntry}
          expanded={expandedChoices.has(`${selected.destination}:context`)}
          toggle={() => toggleChoiceEvidence(`${selected.destination}:context`)}
        />
      )}
      {choiceStatus.offerChanged ? <p role="status">{choiceStatus.text}</p> : null}
      {selected.ownerControl && !controller.reloadRequired ? (
        <OwnerControls
          key={ownerKey}
          field={selected}
          disabled={controller.recording || controller.reloadRequired || controller.checking}
          progress={selectedProgress}
          status={
            ownerNotice?.caseId === selectedReviewCaseId &&
            ownerNotice.destination === selected.destination
              ? ownerNotice.text
              : ownerStatusShown
                ? controller.statusFor(selected.destination)
                : ''
          }
          failed={controller.decision.isError}
          submit={(outcome, value) => {
            const key = choose(selected, outcome, value)
            setOwnerCommand(ownerKey)
            return key
          }}
        />
      ) : null}
      {selected.manualHold ? (
        <AutomaticHandlingPanel handling={automaticHandling} selected={selected} />
      ) : null}
      {comparisonNotices(selected).length ? (
        <aside className="comparison-notice" aria-label="Comparison condition">
          {comparisonNotices(selected).map((notice) => (
            <p key={notice}>{`${noticeText(selected, notice, mediaKind) ?? ''}.`}</p>
          ))}
        </aside>
      ) : null}
      <RecordedFieldObservations
        key={selectedReviewCaseId}
        reviewCaseId={selectedReviewCaseId}
        field={selected}
      />
      {controller.canRetry && selectedProgress?.phase === 'failed' ? (
        <button
          ref={retryButton}
          className="decision-retry"
          type="button"
          onClick={controller.retry}
        >
          Try the same decision again
        </button>
      ) : null}
      {choiceStatus.text && !choiceStatus.offerChanged ? (
        <p role={controller.decision.isError || controller.reloadRequired ? 'alert' : 'status'}>
          {choiceStatus.text}
        </p>
      ) : null}
      {controller.reloadRequired ? (
        <button type="button" disabled={reloading} onClick={() => void controller.settle()}>
          {reloading ? 'Reloading current case…' : 'Reload current case'}
        </button>
      ) : null}
      {selectedChoiceMode && effect ? (
        <aside className="field-apply-context" aria-label="Writing to the library">
          <span className="activity-state-mark" aria-hidden="true"></span>
          <span>
            <small>Writing to the library</small>
            <strong>{effect.state}</strong>
            {effect.detail ? <em>{effect.detail}</em> : null}
          </span>
        </aside>
      ) : null}
    </article>
  )
}
