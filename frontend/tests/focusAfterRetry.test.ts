import { afterEach, describe, expect, it } from 'vitest'

import { focusAfterRetry, retryInPlace } from '../src/focusAfterRetry'

const settle = () => new Promise((resolve) => setTimeout(resolve, 50))

const mount = (html: string) => {
  document.body.innerHTML = html
  return (id: string) => {
    const found = document.getElementById(id)
    if (!found) throw new Error(`#${id} is required`)
    return found
  }
}

describe('focusAfterRetry (#1742)', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('moves focus that fell to the page', async () => {
    const element = mount('<button id="retry">Try again</button>')
    element('retry').focus()
    element('retry').remove()
    expect(document.activeElement).toBe(document.body)
    document.body.insertAdjacentHTML('beforeend', '<button id="reload">Reload</button>')
    focusAfterRetry(() => document.getElementById('reload'))
    await settle()
    expect(document.activeElement).toBe(element('reload'))
  })

  it('moves focus that is still inside the scope', async () => {
    const element = mount(
      '<section id="scope"><button id="more">Load more</button><button id="target">Try again</button></section>',
    )
    element('more').focus()
    focusAfterRetry(
      () => element('target'),
      () => element('scope'),
    )
    await settle()
    expect(document.activeElement).toBe(element('target'))
  })

  it('leaves focus the owner moved to an unrelated control', async () => {
    const element = mount(
      '<section id="scope"><button id="target">Reload</button></section><input id="search" />',
    )
    element('search').focus()
    focusAfterRetry(
      () => element('target'),
      () => element('scope'),
    )
    await settle()
    expect(document.activeElement).toBe(element('search'))
  })

  it('does nothing when there is no target', async () => {
    mount('<p>Loaded</p>')
    focusAfterRetry(() => null)
    await settle()
    expect(document.activeElement).toBe(document.body)
  })

  it('moves focus from a disabled control', async () => {
    const element = mount(
      '<button id="busy">Loading…</button><h3 id="heading" tabindex="-1">Loaded</h3>',
    )
    element('busy').focus()
    ;(element('busy') as HTMLButtonElement).disabled = true
    focusAfterRetry(() => element('heading'))
    await settle()
    expect(document.activeElement).toBe(element('heading'))
  })

  it('holds focus on the region while a retry reads, then moves it to the outcome', async () => {
    const element = mount(
      '<div id="region" tabindex="-1"><button id="retry">Try again</button></div>',
    )
    element('retry').focus()
    let release: (loaded: boolean) => void = () => undefined
    retryInPlace(
      () => element('region'),
      () =>
        new Promise<boolean>((resolve) => {
          release = resolve
        }),
      (loaded) => document.getElementById(loaded ? 'heading' : 'retry'),
    )
    // The read replaces the control: focus is on the region, not the page.
    element('retry').remove()
    expect(document.activeElement).toBe(element('region'))
    element('region').insertAdjacentHTML('beforeend', '<h3 id="heading" tabindex="-1">Loaded</h3>')
    release(true)
    await settle()
    expect(document.activeElement).toBe(element('heading'))
  })
})
