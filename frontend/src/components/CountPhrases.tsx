import { Fragment } from 'react'

// Count phrases joined by a separator. Each number and its first word are one
// inline unit (`.count-pair`): it moves to the next line whole when it fits
// there and breaks at its own space only when it is wider than a line, so no
// number or word splits; the rest of the phrase wraps as ordinary text (#1366).
export const CountPhrases = ({
  phrases,
  separator = ' · ',
}: {
  phrases: readonly string[]
  separator?: string
}) =>
  phrases.map((phrase, index) => {
    const pair = phrase.indexOf(' ', phrase.indexOf(' ') + 1)
    return (
      <Fragment key={index}>
        {index > 0 && separator}
        <span className="count-pair">{pair === -1 ? phrase : phrase.slice(0, pair)}</span>
        {pair === -1 ? '' : phrase.slice(pair)}
      </Fragment>
    )
  })
