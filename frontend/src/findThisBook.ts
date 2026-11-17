// Find this book (#1477): the header's order, the words a book link reads in,
// and the checks a search request passes before it is sent. The routes
// `FindThisBook.tsx` and `BookLink.tsx` render from here; nothing here reads
// or writes.
import settingsVocabulary from '../../src/mechanical_librarian/interfaces/web/settings_vocabulary.json'
import type {
  OwnerLinkCurrent,
  OwnerLinkProvider,
  OwnerLinkScope,
  OwnerLinkState,
  OwnerSearchCard,
  OwnerSearchLane,
  OwnerSearchView,
  WorkAddress,
} from './api/http'
import { ApiError, uncertainWriteOutcome } from './api/http'
import { formatRunningTime, formatWhen } from './dates'
import { languageName } from './labels'

/**
 * The selected work's header actions, in order (L1). Swapping Evidence and
 * Find this book is this one line.
 */
export const WORK_ACTIONS = ['evidence', 'find', 'more'] as const
export type WorkAction = (typeof WORK_ACTIONS)[number]

/** The link view's query: under `library`, so a Library frame refetches it. */
export const ownerLinkKey = (address: WorkAddress) =>
  ['library', 'link', address.connection, address.library, address.item] as const

export const sourceName = (provider: OwnerLinkProvider) =>
  settingsVocabulary.provider_labels[provider]

// The Audible marketplaces as the owner reads them: the store's own domain.
const marketplaceDomains: Readonly<Record<string, string>> = {
  us: 'audible.com',
  uk: 'audible.co.uk',
  de: 'audible.de',
  fr: 'audible.fr',
  au: 'audible.com.au',
  in: 'audible.in',
  ca: 'audible.ca',
  jp: 'audible.co.jp',
  it: 'audible.it',
  es: 'audible.es',
}
export const marketplaceDomain = (code: string) => marketplaceDomains[code] ?? code

export const laneLabels = {
  title_authors: 'Title and authors',
  isbn: 'ISBN',
  asin: 'ASIN',
} as const satisfies Record<OwnerSearchLane, string>

// Header pill (L2) and the works list's status phrase.
export const pillText = (state: OwnerLinkState, provider: OwnerLinkProvider) =>
  ({
    linked: `Linked on ${sourceName(provider)}`,
    preparing: 'Linked, preparing',
    waiting: `Linked, waiting for ${sourceName(provider)}`,
    contradicted: 'Link contradicted',
    needs_replacing: 'Link needs replacing',
  })[state]
export const rowLinkPhrases = {
  linked: 'Linked',
  preparing: 'Linked, preparing',
  waiting: 'Linked, waiting',
  contradicted: 'Link contradicted',
  needs_replacing: 'Link needs replacing',
} as const satisfies Record<OwnerLinkState, string>
export const linkTone = (state: OwnerLinkState) =>
  state === 'linked' || state === 'preparing' ? 'accent' : 'notice'

const creatorsText = (creators: readonly string[]) => creators.join(', ')
const byCreators = (creators: readonly string[]) =>
  creators.length ? ` by ${creatorsText(creators)}` : ''

// The link view's two lines and its state line (§6).
export const linkLine = (link: OwnerLinkCurrent) =>
  `Linked to ${link.title}${byCreators(link.creators)} on ${sourceName(link.provider)}`
export const scopeLine = (link: Pick<OwnerLinkCurrent, 'scope' | 'identifier'>) =>
  link.scope === 'work'
    ? 'As this book'
    : link.scope === 'edition'
      ? `As this edition, ISBN ${link.identifier ?? ''}`
      : `As this recording, ASIN ${link.identifier ?? ''}`

const contradictedReason = (link: OwnerLinkCurrent) => {
  const source = sourceName(link.provider)
  switch (link.reason) {
    case 'format':
      return `${source} now lists ISBN ${link.identifier ?? ''} as a print edition.`
    case 'narrators':
      return 'The recording now credits none of this audiobook’s narrators.'
    case 'variant':
      return `${source} now lists this recording as abridged.`
    default:
      // The view carries no running times, so the line cannot name them.
      return 'The recording’s running time no longer matches this audiobook.'
  }
}
const replacingReason = (link: OwnerLinkCurrent, mediaKind: 'ebook' | 'audiobook') => {
  const source = sourceName(link.provider)
  switch (link.reason) {
    case 'missing':
      return `${source} no longer has this record.`
    case 'merged':
      return `${source} merged this record into another one.`
    case 'source_disabled':
      return `${source} is turned off for this library.`
    default:
      return mediaKind === 'audiobook'
        ? 'This work is now an audiobook.'
        : 'This work is now an ebook.'
  }
}
/** The state line under the link, or null for a link that holds. */
export const stateLine = (link: OwnerLinkCurrent, mediaKind: 'ebook' | 'audiobook', now: Date) => {
  const source = sourceName(link.provider)
  switch (link.state) {
    case 'linked':
      return null
    case 'preparing':
      return link.paused
        ? 'Preparation is paused. The link is used when it resumes.'
        : 'Preparing this work with the link.'
    case 'waiting':
      return link.retry_at === null
        ? `${source} did not answer. The link is used once it does.`
        : `${source} did not answer. Next automatic retry ${formatWhen(link.retry_at, now)}.`
    case 'contradicted':
      return `Link contradicted. ${contradictedReason(link)} Change the link or withdraw it.`
    case 'needs_replacing':
      return `Link needs replacing. ${replacingReason(link, mediaKind)} Change the link.`
  }
}

export const mediumOf = (mediaKind: string): 'ebook' | 'audiobook' =>
  mediaKind === 'epub' ? 'ebook' : 'audiobook'

// Results (§6).
export const resultCount = (count: number) =>
  count === 1 ? '1 result' : `${String(count)} results`
export const droppedLine = (dropped: number) =>
  dropped === 1 ? '1 result could not be shown.' : `${String(dropped)} results could not be shown.`
export const emptyLine = (search: Pick<OwnerSearchView, 'provider' | 'lane' | 'marketplace'>) =>
  search.lane === 'title_authors'
    ? `${sourceName(search.provider)} found nothing for this title and these authors. Change them and search again.`
    : search.lane === 'isbn'
      ? `${sourceName(search.provider)} found nothing for this ISBN. Check it, or search by title and authors.`
      : `Audible found nothing for this ASIN on ${marketplaceDomain(search.marketplace ?? '')}. Check it, or search by title and authors.`
export const disclosureLine = (
  provider: OwnerLinkProvider,
  lane: OwnerSearchLane,
  marketplace: string | null,
) =>
  lane === 'title_authors'
    ? `Search sends the title and authors to ${sourceName(provider)}.`
    : lane === 'isbn'
      ? `Search sends the ISBN to ${sourceName(provider)}.`
      : `Search sends the ASIN to ${marketplaceDomain(marketplace ?? '')}.`
export const runtimeText = (seconds: number) => formatRunningTime(String(seconds)) ?? ''
export const notChoosableLine = (provider: OwnerLinkProvider) =>
  `${sourceName(provider)} lists no ISBN or ID for this record, so it cannot be linked.`
export const cardFacts = (card: OwnerSearchCard) =>
  [
    card.year === null ? null : String(card.year),
    card.publisher,
    card.language && languageName(card.language),
  ]
    .filter((part): part is string => Boolean(part))
    .join(' · ')

// Confirmation (§6).
export const confirmQuestion = (card: OwnerSearchCard, provider: OwnerLinkProvider) =>
  `Link this work to ${card.title}${byCreators(card.creators)} from ${sourceName(provider)}?`
export const contradictionLines = (card: OwnerSearchCard, mediaKind: 'ebook' | 'audiobook') =>
  card.contradictions.map((each) => {
    switch (each.code) {
      case 'identifier':
        return each.identifier === 'asin'
          ? `The record lists ASIN ${String(each.record)}. This work has ASIN ${String(each.work)}.`
          : `The record lists ISBN ${String(each.record)}. This work has ISBN ${String(each.work)}.`
      case 'compilation':
        return 'The record is a collection of several books.'
      case 'language':
        return `The record is in ${languageName(each.language ?? '')}, which is not one of this library’s languages.`
      case 'creators_disjoint':
        return 'The record names none of this work’s authors.'
      case 'runtime':
        return `The recording runs ${runtimeText(Number(each.record))}. This ${mediaKind === 'audiobook' ? 'audiobook' : 'work'} runs ${runtimeText(Number(each.work))}.`
    }
  })
/** Why This edition or This recording is not offered, unless a line above says it already. */
export const withheldLines = (card: OwnerSearchCard, provider: OwnerLinkProvider) => {
  const listed = new Set(card.contradictions.map((each) => each.code))
  return card.withheld.flatMap(({ reason }) => {
    switch (reason) {
      case 'no_isbn':
        return ['This edition is not offered because the record lists no ISBN.']
      case 'format':
        return [
          `This edition is not offered because ${sourceName(provider)} lists it as a print edition.`,
        ]
      case 'no_runtime':
        return ['This recording is not offered because the record states no running time.']
      case 'runtime':
        return listed.has('runtime')
          ? []
          : [
              'This recording is not offered because its running time does not match this audiobook.',
            ]
      case 'narrators':
        return [
          'This recording is not offered because it credits none of this audiobook’s narrators.',
        ]
      case 'variant':
        return listed.has('language')
          ? []
          : ['This recording is not offered because it is abridged.']
    }
  })
}
export const scopeButtons = {
  work: 'This book',
  edition: 'This edition',
  recording: 'This recording',
} as const satisfies Record<OwnerLinkScope, string>

// What the owner typed, in the one spelling the server accepts: NFC, with
// runs of spaces collapsed and the ends trimmed, as an owner-entered value is
// (`FieldDecisionOwner`). The check mirrors `owner_text`; the server's own
// projection scan still answers anything this misses beside the same field.
export const ownerText = (value: string) => value.normalize('NFC').replace(/\s+/gu, ' ').trim()
export const ownerTextValid = (value: string) => {
  const text = ownerText(value)
  return (
    text.length > 0 &&
    Array.from(text).length <= 512 &&
    !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(text) &&
    !/[A-Za-z][A-Za-z0-9+.-]*:\/\//u.test(text) &&
    !/(?:^|\s)(?:~?\/|\.{1,2}\/|[A-Za-z]:\\)\S/u.test(text)
  )
}
export const isbnValid = (value: string) => {
  if (/^\d{9}[\dX]$/.test(value)) {
    const sum = Array.from(value).reduce(
      (total, digit, index) => total + (digit === 'X' ? 10 : Number(digit)) * (10 - index),
      0,
    )
    return sum % 11 === 0
  }
  if (/^\d{13}$/.test(value)) {
    const sum = Array.from(value).reduce(
      (total, digit, index) => total + Number(digit) * (index % 2 === 0 ? 1 : 3),
      0,
    )
    return sum % 10 === 0
  }
  return false
}
export const asinValid = (value: string) => /^[0-9A-Z]{10}$/.test(value)

export const fieldRefusals = {
  title: 'Enter a title of up to 512 characters, with no web address or file path.',
  author: 'Enter the author’s name, up to 512 characters, with no web address or file path.',
  isbn: 'Enter an ISBN of 10 or 13 digits.',
  asin: 'Enter an ASIN of 10 letters and digits.',
} as const

const at = (error: ApiError, now: Date) =>
  error.retryAt === null ? 'a while' : formatWhen(error.retryAt, now)
/** Where a refused search, choose or withdraw reads, and what it says. */
export interface Refusal {
  readonly at: 'title' | 'author' | 'isbn' | 'asin' | 'source' | 'search' | 'choose' | 'withdraw'
  readonly text: string
}
export const uncertainLinkCopy =
  'No answer came back. Check whether the work is linked before choosing again.'
export const refusalFor = (
  error: unknown,
  provider: OwnerLinkProvider,
  action: 'search' | 'choose' | 'withdraw',
  now: Date,
): Refusal => {
  const source = sourceName(provider)
  // A write whose answer was lost, or a 503 after it, may have landed.
  if (
    action !== 'search' &&
    (uncertainWriteOutcome(error) ||
      (error instanceof ApiError && error.status === 503 && error.code === 'unavailable'))
  )
    return { at: action, text: uncertainLinkCopy }
  if (!(error instanceof ApiError))
    return { at: action, text: `${source} did not answer. Search again.` }
  switch (error.code) {
    case 'title_invalid':
      return { at: 'title', text: fieldRefusals.title }
    case 'author_invalid':
      return { at: 'author', text: fieldRefusals.author }
    case 'isbn_invalid':
      return { at: 'isbn', text: fieldRefusals.isbn }
    case 'asin_invalid':
      return { at: 'asin', text: fieldRefusals.asin }
    case 'provider_not_offered':
      return {
        at: 'source',
        text: `${source} is no longer searched for this library. Choose another source.`,
      }
    case 'search_in_flight':
      return {
        at: 'search',
        text: 'Another search is still running. Search again when it finishes.',
      }
    case 'search_limit':
      return {
        at: 'search',
        text: `${source} has been searched 30 times in the last hour. Search again after ${at(error, now)}.`,
      }
    case 'provider_busy':
      return {
        at: 'search',
        text: `${source} is busy with other lookups. Search again in a minute.`,
      }
    case 'provider_deferred':
      return {
        at: 'search',
        text:
          error.reason === 'key'
            ? `${source} refused the API key. Check it in Settings.`
            : error.reason === 'quota_exhausted'
              ? `${source}’s request quota is used up. Search again after ${at(error, now)}.`
              : `${source} is limiting requests. Search again after ${at(error, now)}.`,
      }
    case 'search_failed':
      return {
        at: 'search',
        text:
          error.kind === 'key'
            ? `${source} refused the API key. Check it in Settings.`
            : error.kind === 'unreadable'
              ? `${source} sent an answer Mechanical Librarian could not read. Search again later.`
              : `${source} did not answer. Search again.`,
      }
    case 'search_not_found':
      return { at: action, text: 'This search has expired. Search again.' }
    case 'work_changed':
      return { at: action, text: 'This work changed in Audiobookshelf. Search again.' }
    case 'link_changed':
      return action === 'withdraw'
        ? {
            at: action,
            text: 'The link was changed in another window. Check it, then withdraw again.',
          }
        : { at: action, text: 'The link was changed in another window. Search again to change it.' }
    case 'card_not_choosable':
    case 'scope_not_offered':
      return { at: action, text: 'This record cannot be linked that way. Choose This book.' }
    default:
      return action === 'search'
        ? { at: 'search', text: `${source} did not answer. Search again.` }
        : { at: action, text: uncertainLinkCopy }
  }
}
