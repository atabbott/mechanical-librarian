// One Find this book card's cover and lines (#1477). A card with no preview,
// or one that fails to load, shows a spine with the title's initials.
import { useState } from 'react'

import type { OwnerSearchCard, OwnerSearchView } from '../api/http'
import { cardFacts, runtimeText } from '../findThisBook'

function Spine({ title }: { title: string }) {
  const initials = title
    .split(/\s+/u)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => Array.from(word)[0]?.toLocaleUpperCase() ?? '')
    .join('')
  return (
    <span className="find-book-cover is-spine" aria-hidden="true">
      {initials}
    </span>
  )
}

// A failure belongs to the one preview that failed: a new search's card at the
// same position names another preview and tries it.
export function Preview({ search, card }: { search: OwnerSearchView; card: OwnerSearchCard }) {
  const src = `/api/v1/library/find/searches/${encodeURIComponent(search.search_id)}/previews/${String(card.position)}`
  const [failed, setFailed] = useState<string | null>(null)
  if (!card.preview || failed === src) return <Spine title={card.title} />
  return (
    <img
      className="find-book-cover"
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailed(src)}
    />
  )
}

// Every line is the source's text, shown as text: nothing here is a link.
export function CardLines({ card }: { card: OwnerSearchCard }) {
  const facts = cardFacts(card)
  return (
    <>
      <p className="find-book-card-title">{card.title}</p>
      {card.subtitle ? <p className="find-book-card-subtitle">{card.subtitle}</p> : null}
      {card.creators.length ? <p>{`By ${card.creators.join(', ')}`}</p> : null}
      {card.narrators.length ? <p>{`Read by ${card.narrators.join(', ')}`}</p> : null}
      {facts ? <p className="find-book-card-line">{facts}</p> : null}
      {card.isbn ? <p className="find-book-card-line">{`ISBN ${card.isbn}`}</p> : null}
      {card.asin ? <p className="find-book-card-line">{`ASIN ${card.asin}`}</p> : null}
      {card.runtime_seconds === null ? null : (
        <p className="find-book-card-line">{runtimeText(card.runtime_seconds)}</p>
      )}
    </>
  )
}
