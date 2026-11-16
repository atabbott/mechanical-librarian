import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(cleanup)
vi.stubGlobal('scrollTo', vi.fn())
vi.stubGlobal('scrollBy', vi.fn())
Element.prototype.scrollIntoView = vi.fn()

// jsdom has no modal dialog. This stands in for the platform's open state and
// its cancel and close events only; the browser tests prove focus and inertness.
HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
  this.open = true
}
HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
  if (!this.open) return
  this.open = false
  this.dispatchEvent(new Event('close'))
}
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape' || event.defaultPrevented) return
  const dialog = document.querySelector<HTMLDialogElement>('dialog[open]')
  if (dialog?.dispatchEvent(new Event('cancel', { cancelable: true }))) dialog.close()
})
