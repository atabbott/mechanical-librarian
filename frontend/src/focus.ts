import { useEffect } from 'react'

export const useRouteReadyFocus = (identity: string) => {
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      document.querySelector<HTMLElement>('#main-content [data-route-focus]')?.focus({ preventScroll: true })
    })
    return () => cancelAnimationFrame(frame)
  }, [identity])
}
