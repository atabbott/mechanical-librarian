import { useLayoutEffect, useRef } from 'react'
import type { ReactNode, RefObject } from 'react'

// The inset tray that finishes a page-owned draft: summary first, an optional
// progress strip, then aligned actions; `children` spans the full width below.
// While it floats it publishes its height so focused controls scroll clear.
export const FinishTray = ({
  label,
  className = '',
  ref,
  summary,
  progress,
  actions,
  children,
}: {
  label: string
  className?: string
  ref?: RefObject<HTMLElement | null>
  summary: ReactNode
  progress?: ReactNode
  actions: ReactNode
  children?: ReactNode
}) => {
  const own = useRef<HTMLElement>(null)
  const tray = ref ?? own
  useLayoutEffect(() => {
    const element = tray.current
    const root = document.documentElement
    if (element && getComputedStyle(element).position === 'sticky')
      root.style.setProperty('--finish-tray-height', `${String(element.offsetHeight)}px`)
    else root.style.removeProperty('--finish-tray-height')
  })
  useLayoutEffect(
    () => () => {
      document.documentElement.style.removeProperty('--finish-tray-height')
    },
    [],
  )
  return (
    <section className={`cm-finish-tray ${className}`} aria-label={label} ref={tray}>
      <div className="cm-finish-summary">{summary}</div>
      {progress}
      <div className="cm-finish-actions">{actions}</div>
      {children}
    </section>
  )
}
