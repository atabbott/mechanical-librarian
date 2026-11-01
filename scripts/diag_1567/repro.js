// Minimal stand-in for the Library route's Attention open/close (#1567).
// No React: the open and close each change the same DOM the app changes and
// make the same scroll and focus calls in the same order.
addEventListener('DOMContentLoaded', () => {
  const $ = (s) => document.querySelector(s)
  const workspace = $('.library-workspace')
  const toggle = $('.attention-toggle')
  const header = $('.selected-work-header')
  const back = $('.compact-workspace-back')
  let rail = null
  let frames = []
  const masthead = () => Math.max(0, $('.masthead')?.getBoundingClientRect().bottom ?? 0)
  const open = () => {
    workspace.classList.add('attention-open')
    back.remove()
    header.removeAttribute('data-route-focus')
    workspace.append($('#rail').content.firstElementChild.cloneNode(true))
    rail = workspace.querySelector('.attention-rail')
    toggle.setAttribute('aria-expanded', 'true')
    rail.querySelector('button.attention-close, button[aria-label="Close"], button')
    for (const b of rail.querySelectorAll('button')) if (b.textContent.trim() === 'Close') b.addEventListener('click', () => setTimeout(close, 0))
    window.scrollTo({ top: 0 })  // the router's reset on navigation
    const h = rail.querySelector('h2')
    frames.push(requestAnimationFrame(() => frames.push(requestAnimationFrame(() => {
      h.focus({ preventScroll: true })
      h.scrollIntoView({ block: 'nearest' })
    }))))
  }
  const close = () => {
    for (const f of frames) cancelAnimationFrame(f)
    frames = []
    rail?.remove(); rail = null
    workspace.classList.remove('attention-open')
    workspace.prepend(back)
    header.setAttribute('data-route-focus', 'true')
    toggle.setAttribute('aria-expanded', 'false')
    const top = scrollY + workspace.getBoundingClientRect().top - masthead()
    window.scrollTo({ top })  // the compact reveal (a layout effect)
    requestAnimationFrame(() => requestAnimationFrame(() => header.focus({ preventScroll: true })))
  }
  toggle.addEventListener('click', () => setTimeout(() => (rail ? close() : open()), 0))
  window.__repro = { open, close }
})
