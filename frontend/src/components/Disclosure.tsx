import type { ReactNode } from 'react'

// A native disclosure whose summary carries the trailing chevron. Its body
// opens beneath the summary, so it never displaces a neighbouring control.
export const Disclosure = ({ summary, children }: { summary: ReactNode; children: ReactNode }) => (
  <details className="cm-disclosure">
    <summary>{summary}</summary>
    {children}
  </details>
)
