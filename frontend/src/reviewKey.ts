// Which review key a keydown asks for (#1337). The filter is kept apart from
// the component so it can be tested directly.
export type ReviewKey = 'j' | 'k' | '1' | '2' | '3' | 'e' | 'u' | '?'

const keys: Partial<Record<string, ReviewKey>> = {
  j: 'j',
  k: 'k',
  e: 'e',
  u: 'u',
  '1': '1',
  '2': '2',
  '3': '3',
  '?': '?',
}

const typing =
  'input, textarea, select, [contenteditable], form, .attention-rail, .review-keys-dialog'

/** The review key an event asks for, or null when the event is not for the keys. */
export const reviewKey = (event: KeyboardEvent): ReviewKey | null => {
  if (
    event.defaultPrevented ||
    event.repeat ||
    event.isComposing ||
    // An input method still composing sends 229 where isComposing is false.
    // eslint-disable-next-line @typescript-eslint/no-deprecated -- no standard field says this.
    event.keyCode === 229 ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey
  )
    return null
  const key = keys[event.key.length === 1 ? event.key.toLowerCase() : '']
  // Shift is how some layouts type ? and the digits, never a letter here.
  if (key === undefined || (event.shiftKey && /[a-z]/u.test(key))) return null
  const target = event.target
  if (target === document.body) return key
  if (!(target instanceof Element) || !target.closest('.library-page') || target.closest(typing))
    return null
  return key
}
