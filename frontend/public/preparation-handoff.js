/* global HTMLLinkElement, URL, addEventListener, clearTimeout, document, location, setTimeout, window */
;(() => {
  const motion = document.querySelector('#preparation-handoff-motion')
  if (!(motion instanceof HTMLLinkElement)) return
  const target = new URL(location.href)
  let fallback
  const disable = () => {
    motion.disabled = true
    clearTimeout(fallback)
  }
  if (
    target.pathname !== '/library' ||
    !/^preparation_run_[0-9a-f]{64}$/.test(target.searchParams.get('preparation') ?? '') ||
    !('onpagereveal' in window)
  ) {
    disable()
    return
  }
  fallback = setTimeout(disable, 2000)
  addEventListener(
    'pagereveal',
    (event) => {
      const transition = event.viewTransition
      if (transition) void transition.finished.then(disable, disable)
      else disable()
    },
    { once: true },
  )
})()
