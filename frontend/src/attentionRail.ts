import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'

// The width range where the Attention rail is a drawer over the workspace.
export const drawerQuery = '(min-width: 761px) and (max-width: 1199px)'

// The Attention rail's focus lifecycle on Library: opening reveals and focuses
// its heading, closing hands focus back, and as a drawer it holds focus.
export const useAttentionRail = (
  attention: string | undefined,
  {
    rail,
    heading,
    toggle,
  }: {
    rail: RefObject<HTMLElement | null>
    heading: RefObject<HTMLHeadingElement | null>
    toggle: RefObject<HTMLButtonElement | null>
  },
) => {
  // Close and Escape hand focus back to the toggle that opened the rail.
  const returnToToggle = useRef(false)
  const returnToWork = useRef(false)
  // A close reaches the route one navigation later, so the opening reveal
  // must not focus or scroll a rail that is already going away (#1563).
  const cancelOpeningReveal = useRef<(() => void) | null>(null)

  useEffect(() => {
    const opened = attention === 'open'
    if (!opened && !returnToToggle.current) return
    returnToToggle.current = false
    let focusFrame = 0
    const routeFrame = requestAnimationFrame(() => {
      focusFrame = requestAnimationFrame(() => {
        // Closing returns focus from the rail it left, or from where the
        // route's own focus put it, never from a field the owner moved to.
        const active = document.activeElement
        const routeTarget = document.querySelector('#main-content [data-route-focus]')
        // At 760px and below closing over a selected work reveals that work,
        // so focus stays on its header there rather than the toggle above it.
        if (opened) {
          heading.current?.focus({ preventScroll: true })
          heading.current?.scrollIntoView({ block: 'nearest' })
        } else if (
          !active ||
          active === document.body ||
          active === routeTarget ||
          rail.current?.contains(active)
        ) {
          ;(returnToWork.current && routeTarget instanceof HTMLElement
            ? routeTarget
            : toggle.current
          )?.focus({ preventScroll: true })
        }
      })
    })
    const cancel = () => {
      cancelAnimationFrame(routeFrame)
      cancelAnimationFrame(focusFrame)
    }
    if (opened) cancelOpeningReveal.current = cancel
    return () => {
      cancel()
      if (cancelOpeningReveal.current === cancel) cancelOpeningReveal.current = null
    }
  }, [attention, heading, rail, toggle])

  // From 761 to 1199px the rail is a drawer over the workspace: everything
  // outside it but the toolbar that toggles it is inert until it closes, and
  // its own Tab order wraps.
  useEffect(() => {
    const drawerRail = rail.current
    if (attention !== 'open' || !drawerRail || typeof window.matchMedia !== 'function') return
    const drawer = window.matchMedia(drawerQuery)
    let muted: Element[] = []
    const restore = () => {
      for (const node of muted) node.removeAttribute('inert')
      muted = []
    }
    const apply = () => {
      restore()
      if (!drawer.matches) return
      for (let node: Element = drawerRail; node.parentElement && node !== document.body;) {
        const parent: Element = node.parentElement
        for (const sibling of parent.children)
          if (
            sibling !== node &&
            !sibling.hasAttribute('inert') &&
            !(toggle.current && sibling.contains(toggle.current))
          ) {
            sibling.setAttribute('inert', '')
            muted.push(sibling)
          }
        node = parent
      }
    }
    apply()
    drawer.addEventListener('change', apply)
    return () => {
      drawer.removeEventListener('change', apply)
      restore()
    }
  }, [attention, rail, toggle])

  return {
    // Close and Escape: focus goes back to the toggle, or at 760px and below
    // to the selected work the closing rail uncovers.
    returnFocus: (toWork: boolean) => {
      returnToToggle.current = true
      returnToWork.current = toWork
    },
    cancelOpeningReveal: () => cancelOpeningReveal.current?.(),
  }
}
