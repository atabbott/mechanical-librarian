// Import identifiers (#1478): the entry at the foot of each saved library's
// record, and the page that takes the libraries list's place while it is
// open. It previews the file, then links every row the preview says will be
// linked, in one import.
import { Link } from '@tanstack/react-router'
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode, Ref } from 'react'

import { ApiError, BuildMismatchError, refusalCopy, sessionRefreshedCopy } from '../api/http'
import type { IdentifierPreviewView } from '../api/http'
import {
  checkingImportCopy,
  closedCopy,
  fileLimitBytes,
  fileRefusalCopy,
  formatExample,
  formatText,
  importedCopy,
  linkLabel,
  linkedWorks,
  notRecordedClosedCopy,
  notRecordedCopy,
  staleCopy,
  uncheckedCopy,
} from '../identifierImport'
import { useReviewContext } from '../router'
import { Button } from './Button'
import { IdentifierImportPreview } from './IdentifierImportPreview'
import { StaleBuildRead } from './StaleBuildRead'

/** Copy that quotes the file's own words in backticks shows them as code. */
const Quoted = ({ text }: { text: string }) =>
  text.split('`').map((part, index) => (index % 2 ? <code key={index}>{part}</code> : part))

type Step =
  | { kind: 'file'; refusal?: string }
  | { kind: 'checking' }
  | { kind: 'preview'; preview: IdentifierPreviewView; key: string; notice?: string }
  | { kind: 'linking'; preview: IdentifierPreviewView; key: string }
  | { kind: 'refused'; text: string; again: boolean }
  | { kind: 'done'; linked: number }

const newKey = () => crypto.randomUUID()

/** A line that takes focus when it appears, so the change is heard. */
const Line = ({
  className,
  role,
  target,
  children,
}: {
  className: string
  role: 'alert' | 'status'
  target: Ref<HTMLParagraphElement>
  children: ReactNode
}) => (
  <p className={className} role={role} tabIndex={-1} ref={target}>
    {children}
  </p>
)

function IdentifierImportFlow({
  connection,
  library,
  onBusy,
}: {
  connection: string
  library: string
  onBusy: (busy: boolean) => void
}) {
  const { http } = useReviewContext()
  const [step, setStep] = useState<Step>({ kind: 'file' })
  const [file, setFile] = useState<File | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const focusLine = useRef<HTMLParagraphElement | null>(null)
  // Where focus goes once the next step has rendered.
  const focusNext = useRef<'line' | 'input' | null>(null)
  const formatId = useId()
  const inputId = useId()
  useLayoutEffect(() => {
    if (focusNext.current === 'line') focusLine.current?.focus()
    if (focusNext.current === 'input') input.current?.focus()
    focusNext.current = null
  })
  const busy = step.kind === 'checking' || step.kind === 'linking'
  useEffect(() => onBusy(busy), [busy, onBusy])
  const show = (next: Step) => {
    focusNext.current = 'line'
    setStep(next)
  }
  const preview = async (chosen: File) => {
    if (chosen.size > fileLimitBytes) return show({ kind: 'file', refusal: 'body_too_large' })
    setStep({ kind: 'checking' })
    try {
      const result = await http.previewIdentifiers(connection, library, chosen)
      show({ kind: 'preview', preview: result, key: newKey() })
    } catch (error) {
      show({
        kind: 'file',
        refusal:
          error instanceof BuildMismatchError
            ? 'build'
            : error instanceof ApiError
              ? refusalCopy(error, error.code)
              : 'unavailable',
      })
    }
  }
  // After a lost answer the import is read back, never sent again unasked.
  const check = async (current: IdentifierPreviewView, key: string) => {
    show({ kind: 'refused', text: checkingImportCopy, again: false })
    try {
      const status = await http.getIdentifierPreviewStatus(current.preview_id)
      if (status.state === 'imported') return show({ kind: 'done', linked: status.linked ?? 0 })
      if (status.state === 'open')
        return show({ kind: 'preview', preview: current, key, notice: notRecordedCopy })
      show({ kind: 'refused', text: notRecordedClosedCopy, again: true })
    } catch {
      show({ kind: 'refused', text: uncheckedCopy, again: false })
    }
  }
  const confirm = async (current: IdentifierPreviewView, key: string) => {
    setStep({ kind: 'linking', preview: current, key })
    try {
      const result = await http.importIdentifiers(current.preview_id, key)
      show({ kind: 'done', linked: result.linked })
    } catch (error) {
      if (error instanceof BuildMismatchError)
        return show({ kind: 'refused', text: 'build', again: false })
      const code = error instanceof ApiError ? error.code : ''
      if (code === 'csrf_renewed')
        return show({ kind: 'preview', preview: current, key, notice: sessionRefreshedCopy })
      if (code === 'preview_stale') return show({ kind: 'refused', text: staleCopy, again: true })
      if (code === 'preview_not_found')
        return show({ kind: 'refused', text: closedCopy, again: true })
      if (code === 'preview_imported')
        return show({ kind: 'refused', text: importedCopy, again: false })
      // A lost answer, a fault after the write, or any other refusal: whether
      // the import was recorded is read back.
      if (!(error instanceof ApiError)) throw error
      await check(current, key)
    }
  }
  const another = () => {
    setFile(null)
    focusNext.current = 'input'
    setStep({ kind: 'file' })
  }
  const chooser = (
    <div className="identifier-import-file">
      <span className="identifier-import-label">Identifiers file</span>
      <input
        id={inputId}
        ref={input}
        className="identifier-import-input"
        type="file"
        accept=".tsv,.txt,text/plain,text/tab-separated-values"
        aria-describedby={formatId}
        onChange={(event) => setFile(event.currentTarget.files?.[0] ?? null)}
      />
      <label htmlFor={inputId} className="identifier-import-choose">
        <span className="button-secondary">Choose file</span>
        {file ? <span className="identifier-import-name">{file.name}</span> : null}
      </label>
      <p id={formatId}>
        <Quoted text={formatText} />
      </p>
      <pre className="identifier-import-example">{formatExample.join('\n')}</pre>
    </div>
  )
  if (step.kind === 'file' || step.kind === 'checking') {
    const checking = step.kind === 'checking'
    const refusal = step.kind === 'file' ? step.refusal : undefined
    return (
      <div className="identifier-import-flow">
        {refusal === 'build' ? (
          <StaleBuildRead lead="The file was not checked." />
        ) : refusal ? (
          <Line className="identifier-import-refusal" role="alert" target={focusLine}>
            <Quoted text={refusal === sessionRefreshedCopy ? refusal : fileRefusalCopy(refusal)} />
          </Line>
        ) : null}
        {chooser}
        {checking ? (
          <p className="identifier-import-status" role="status">
            Checking the file…
          </p>
        ) : null}
        <div className="identifier-import-actions">
          <Button
            type="button"
            variant="primary"
            disabled={file === null}
            aria-disabled={checking || undefined}
            onClick={() => file && !checking && void preview(file)}
          >
            Preview
          </Button>
        </div>
      </div>
    )
  }
  if (step.kind === 'preview' || step.kind === 'linking') {
    const linking = step.kind === 'linking'
    const count = step.preview.counts.will_link
    return (
      <div className="identifier-import-flow">
        {step.kind === 'preview' && step.notice ? (
          <Line className="identifier-import-refusal" role="alert" target={focusLine}>
            {step.notice}
          </Line>
        ) : null}
        <IdentifierImportPreview
          preview={step.preview}
          ref={step.kind === 'preview' && step.notice ? undefined : focusLine}
        />
        {count === 0 ? <p className="identifier-import-status">No rows can be linked.</p> : null}
        {linking ? (
          <p className="sr-only" role="status">
            Linking…
          </p>
        ) : null}
        <div className="identifier-import-actions">
          {count > 0 ? (
            <Button
              type="button"
              variant="primary"
              aria-disabled={linking || undefined}
              onClick={() => !linking && void confirm(step.preview, step.key)}
            >
              {linking ? 'Linking…' : linkLabel(count)}
            </Button>
          ) : null}
          <Button type="button" disabled={linking} onClick={another}>
            Choose another file
          </Button>
        </div>
      </div>
    )
  }
  if (step.kind === 'refused')
    return (
      <div className="identifier-import-flow">
        {step.text === 'build' ? (
          <StaleBuildRead lead="The file was not imported." />
        ) : (
          <Line
            className={
              step.text === checkingImportCopy
                ? 'identifier-import-status'
                : 'identifier-import-refusal'
            }
            role={step.text === checkingImportCopy ? 'status' : 'alert'}
            target={focusLine}
          >
            {step.text}
            {step.text === importedCopy || step.text === uncheckedCopy ? (
              <>
                {' '}
                <ActivityLink />
              </>
            ) : null}
          </Line>
        )}
        {step.text === checkingImportCopy ? null : (
          <div className="identifier-import-actions">
            {step.again && file ? (
              <Button type="button" variant="primary" onClick={() => void preview(file)}>
                Preview again
              </Button>
            ) : null}
            <Button type="button" onClick={another}>
              Choose another file
            </Button>
          </div>
        )}
      </div>
    )
  return (
    <div className="identifier-import-flow">
      <Line className="identifier-import-success" role="status" target={focusLine}>
        {linkedWorks(step.linked)}. They are prepared again in the library&apos;s next scan. To undo
        the import, withdraw it in <ActivityLink />.
      </Line>
    </div>
  )
}

const ActivityLink = () => (
  <Link to="/activity" search={{ eventType: 'all', rangeDays: 30, page: 1, pageSize: 25 }}>
    Activity
  </Link>
)

/** The page that takes the libraries list's place while an import is open. */
export function IdentifierImportSheet({
  connection,
  library,
  label,
  onBack,
}: {
  connection: string
  library: string
  label: string
  onBack: () => void
}) {
  const heading = useRef<HTMLHeadingElement>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => heading.current?.focus(), [])
  return (
    <section className="identifier-import-sheet" aria-labelledby="identifier-import-heading">
      <div className="identifier-import-back">
        <Button type="button" disabled={busy} onClick={onBack}>
          Back to libraries
        </Button>
      </div>
      <h3 id="identifier-import-heading" ref={heading} tabIndex={-1}>
        Import identifiers for {label}
      </h3>
      <IdentifierImportFlow connection={connection} library={library} onBusy={setBusy} />
    </section>
  )
}

/** The entry each saved library's record ends with. */
export const IdentifierImportEntry = ({ id, onOpen }: { id: string; onOpen: () => void }) => (
  <div className="identifier-import">
    <button type="button" id={id} onClick={onOpen}>
      Import identifiers
    </button>
  </div>
)
