import { useState } from 'react'

import type { LibraryCoverView } from '../api/http'

// The first letter of each of the first two words that have one, so a
// bracket or an ampersand never stands for a word (#1290).
const initials = (title: string) =>
  title
    .split(/\s+/u)
    .map((word) => /\p{L}/u.exec(word)?.[0])
    .filter((letter): letter is string => letter !== undefined)
    .slice(0, 2)
    .map((letter) => letter.toUpperCase())
    .join('') || 'ML'
// A work's mark: the cover the library holds when its report retained one,
// contained whole and never setting the row's height, else its spine of
// initials. A cover that fails to load falls back to the spine (#1290).
export function BookMark({ title, cover }: { title: string; cover: LibraryCoverView | undefined }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const shown = cover !== undefined && failedUrl !== cover.url
  return (
    <span
      className={shown ? 'library-book-mark has-cover' : 'library-book-mark'}
      aria-hidden="true"
    >
      {shown ? (
        <img
          src={cover.url}
          alt=""
          width={cover.width}
          height={cover.height}
          loading="lazy"
          decoding="async"
          onError={() => {
            setFailedUrl(cover.url)
          }}
        />
      ) : (
        initials(title)
      )}
    </span>
  )
}
