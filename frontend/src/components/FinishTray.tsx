import { useLayoutEffect, useRef } from 'react'
import type { ReactNode, RefObject } from 'react'

// The inset tray that finishes a page-owned draft: summary first, an optional
// progress strip, then aligned actions; `children` spans the full width below.
// While it floats it publishes its height so focused controls scroll clear. A
// tray taller than a third of the window, as enlarged text on a phone makes
// it, would cover what it finishes, so it scrolls with the page instead.
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
  // Re-measured whenever the tray or the window resizes, since a breakpoint or
  // zoom can switch it between sticky and static without a render.
  useLayoutEffect(() => {
    const element = tray.current
    const root = document.documentElement
    const measure = () => {
      const settled = element?.hasAttribute('data-static')
      // It floats again only once clearly shorter, so a phone's toolbar
      // showing and hiding never flips it back and forth.
      const limit = window.innerHeight / (settled ? 3.5 : 3)
      element?.toggleAttribute('data-static', element.offsetHeight > limit)
      // Settling into the page carries focus with it, so the tray, or the
      // focused control when the tray is taller than the window, is brought
      // back into view.
      const focused = document.activeElement
      if (!settled && element?.hasAttribute('data-static') && focused instanceof HTMLElement)
        if (element.contains(focused))
          (element.offsetHeight <= window.innerHeight ? element : focused).scrollIntoView({
            block: 'nearest',
          })
      if (element && getComputedStyle(element).position === 'sticky')
        root.style.setProperty('--finish-tray-height', `${String(element.offsetHeight)}px`)
      else root.style.removeProperty('--finish-tray-height')
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    if (element) observer?.observe(element)
    window.addEventListener('resize', measure)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', measure)
      root.style.removeProperty('--finish-tray-height')
    }
  }, [tray])
  return (
    <section className={`cm-finish-tray ${className}`} aria-label={label} ref={tray}>
      <div className="cm-finish-summary">{summary}</div>
      {progress}
      <div className="cm-finish-actions">{actions}</div>
      {children}
    </section>
  )
}
