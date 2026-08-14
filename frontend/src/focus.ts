import { useEffect } from 'react'

export const useRouteReadyFocus = (identity: string) => {
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const path = window.location.pathname
      const main = document.querySelector<HTMLElement>('#main-content')
      const focusIdentity = `${path}:${identity}`
      if (main?.dataset.routeFocusIdentity === focusIdentity) return
      const target = document.querySelector<HTMLElement>('#main-content [data-route-focus]')
      if (!target) return
      target.focus({ preventScroll: true })
      if (main) main.dataset.routeFocusIdentity = focusIdentity
    })
    return () => cancelAnimationFrame(frame)
  }, [identity])
}
