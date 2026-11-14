// A work whose fields wait for their check against a changed Book matching
// setting: its choices are disabled, and the case reads again by itself when
// the check finishes (#1355).
export const caseCheckingCopy = 'Checking this work against the new Book matching setting.'

export function CaseCheckingLine({ checking }: { checking: boolean }) {
  return checking ? (
    <p className="case-checking-line" role="status">
      {caseCheckingCopy}
    </p>
  ) : null
}
