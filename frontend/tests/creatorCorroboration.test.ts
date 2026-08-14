import { describe, expect, it } from 'vitest'

import { creatorCorroborationLabels } from '../src/routes/creatorCorroboration'

describe('creatorCorroborationLabels', () => {
  it('names both privacy-safe providers only beside creator corroboration', () => {
    const evidence = [
      { reference: 'provider_a', provider: 'hardcover' },
      { reference: 'provider_b', provider: 'open_library' },
      { reference: 'provider_c', provider: 'private-connection' },
    ] as never

    expect(
      creatorCorroborationLabels(
        ['explicit_author_corroborates_untyped_creator'],
        ['provider_a', 'provider_b', 'provider_c'], evidence,
      ),
    ).toEqual(['Hardcover', 'Open Library'])
    expect(creatorCorroborationLabels([], ['provider_a'], evidence)).toEqual([])
  })
})
