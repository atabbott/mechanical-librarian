import type {
  AudioSurface,
  ClaimBasis,
  ClaimKind,
  CoverHistoryOutcome,
  CoverSourceKind,
  DescriptionState,
  EvidenceRole,
  FieldState,
  MediaHealthKind,
  MediaKind,
  PreparationFailureStage,
  PreparationFailureStatus,
  ProviderQueryKind,
  ProviderStatus,
  ReviewerFailureCode,
  ReviewerState,
  ScanState,
  PreviewDisposition,
  SuspensionReason,
  TargetDestination,
  TransitionOutcome,
} from './api/http'

// Every code an owner surface names has an entry here, in a map that
// `satisfies` a Record over the code's closed set, so a code without owner
// words fails the type check. There is no sentence-case fallback: a code read
// as its own label is how `Isbn`, `PublishedYear` and `Asin` reached the owner
// (#1286). Two families are open on the wire rather than closed, and say so
// where they are named: cover policy notes and Activity's ledger states each
// keep one neutral sentence for a code this build does not know.

// One name per field, in Library, review, Activity and Policy alike.
const destinationLabels = {
  title: 'Title',
  subtitle: 'Subtitle',
  authors: 'Authors',
  narrators: 'Narrators',
  series: 'Series',
  genres: 'Genres',
  publishedYear: 'Publication year',
  publishedDate: 'Publication date',
  publisher: 'Publisher',
  description: 'Description',
  isbn: 'ISBN',
  asin: 'ASIN',
  language: 'Language',
  explicit: 'Explicit content',
  tags: 'Tags',
  cover: 'Cover',
} satisfies Record<TargetDestination, string>
export const destinationLabel = (destination: TargetDestination) => destinationLabels[destination]
// The same name inside a sentence: `Which ISBN should this work use?`, `3
// publication date changes`. An acronym keeps its capitals.
export const destinationPhrase = (destination: TargetDestination) =>
  /^[A-Z]{2,}$/u.test(destinationLabels[destination])
    ? destinationLabels[destination]
    : destinationLabels[destination].toLowerCase()

// The comparison conditions a review names beside a field. This map is the
// whole set: a notice outside it is not a comparison condition and is never
// shown as one.
const materialReasonLabels = {
  isbn_creator_supported_title: 'The ISBN and author support this title',
  isbn_title_material_conflict:
    'The sources disagree about the work or title. Choose the title yourself',
  bibliographic_combination_unresolved:
    'The sources do not settle one combination of these details. Choose them yourself',
  competing_evidence: 'The sources disagree. Choose the value yourself',
  date_refinement_requires_corroboration:
    'A more exact date needs a second source before it can replace this one',
  date_year_conflict: 'The sources disagree about the year',
  language_display_name:
    'The current value names the same language. Accepting only changes how it is written',
  field_policy_conflict:
    'Your policy for this field and the sources point different ways. Choose the value yourself',
  insufficient_independent_corroboration: 'Not enough independent sources agree on this value',
  no_safe_bibliographic_combination:
    'No combination of these details is safe to apply automatically',
  populated_subjective_field: 'This field already has a value of yours, so a change needs you',
  provider_consensus_title_bracket_source_tag:
    'Independent sources agree once the tag in square brackets is removed',
  provider_consensus_title_explicit_identifier:
    'Independent sources agree once the labelled ISBN is removed',
  provider_consensus_title_parenthesized_bare_number:
    'Independent sources agree once the number in brackets is removed',
  provider_consensus_title_parenthesized_series_name:
    'Independent sources agree once the series name in brackets is kept as the series',
  provider_consensus_title_parenthesized_year:
    'Independent sources agree once the year in brackets is removed',
  provider_consensus_title_subtitle_segment:
    'Independent sources agree once the part after the colon is kept as the subtitle',
  provider_consensus_title_colon_unused:
    'Only this book’s own details give the part after the colon as a subtitle. Choose the title yourself',
  provider_consensus_title_series_unpreserved:
    'No source gives the series name in brackets as the series',
  decoration_equivalent_bracket_source_tag:
    'Sources agree once the tag in square brackets is removed. The current title is kept',
  decoration_equivalent_explicit_identifier:
    'Sources agree once the labelled ISBN is removed. The current title is kept',
  decoration_equivalent_parenthesized_bare_number:
    'Sources agree once the number in brackets is removed. The current title is kept',
  decoration_equivalent_parenthesized_series_name:
    'Sources agree once the series name in brackets is removed. The current title is kept',
  decoration_equivalent_parenthesized_year:
    'Sources agree once the year in brackets is removed. The current title is kept',
  decoration_equivalent_subtitle_segment:
    'Sources agree once the part after the colon is removed. The current title is kept',
  decoration_tidy_bracket_source_tag:
    'Sources agree once the tag in square brackets is removed. The current title is tidied to match',
  decoration_tidy_explicit_identifier:
    'Sources agree once the labelled ISBN is removed. The current title is tidied to match',
  decoration_tidy_parenthesized_bare_number:
    'Sources agree once the number in brackets is removed. The current title is tidied to match',
  decoration_tidy_parenthesized_series_name:
    'Sources agree once the series name in brackets is kept as the series. The current title is tidied to match',
  decoration_tidy_parenthesized_year:
    'Sources agree once the year in brackets is removed. The current title is tidied to match',
  decoration_tidy_subtitle_segment:
    'Sources agree once the part after the colon is removed. The current title is tidied to match',
  decoration_tidy_series_unpreserved:
    'No source gives the series name in brackets as the series. Choose the title yourself',
  decoration_equivalence_provider_dissent:
    'Another source gives a different work title. Choose the title yourself',
  decoration_equivalence_decorated_consensus:
    'More sources give the title with the extra text than without it. Choose the title yourself',
  subtitle_segment_uncorroborated: 'No other source gives the part after the colon as a subtitle',
  subtitle_segment_provider_dissent:
    'Another source gives a different subtitle. Choose the subtitle yourself',
  subtitle_segment_decorated_consensus:
    'The part after the colon reads as part of the title: no source has it, or at least as many sources include it as leave it out',
  provider_consensus_contributor_display: 'Independent sources agree on how the names are written',
}
export type MaterialComparisonReason = keyof typeof materialReasonLabels
export const materialReasonLabel = (code: string) =>
  Object.hasOwn(materialReasonLabels, code)
    ? materialReasonLabels[code as MaterialComparisonReason]
    : undefined
export const materialComparisonReasons = Object.keys(
  materialReasonLabels,
) as readonly MaterialComparisonReason[]
// The conditions whose words ask the owner to choose. An optional choice
// already says no review is needed, so these are left off it; every other
// condition still explains the choice (#1450, #1557).
export const choiceRequestReasons: ReadonlySet<string> = new Set<MaterialComparisonReason>([
  'isbn_title_material_conflict',
  'bibliographic_combination_unresolved',
  'competing_evidence',
  'field_policy_conflict',
  'populated_subjective_field',
  'provider_consensus_title_colon_unused',
  'decoration_tidy_series_unpreserved',
  'decoration_equivalence_provider_dissent',
  'decoration_equivalence_decorated_consensus',
  'subtitle_segment_provider_dissent',
])

// A report's file format, as the owner calls the item.
export const mediaKindLabels = {
  epub: 'Ebook (EPUB)',
  m4b: 'Audiobook (M4B)',
  m4a: 'Audiobook (M4A)',
  mp3: 'Audiobook (MP3)',
  audio: 'Audiobook',
  unknown: 'Format not recorded',
} satisfies Record<MediaKind, string>

export const mediaHealthLabels = {
  runtime_short_of_provider: 'Shorter than the edition',
  chapter_structure_mismatch: 'Chapters differ from the edition',
  track_sequence_gap: 'A track is missing from the sequence',
  track_sequence_disorder: 'Tracks are out of order',
  track_duration_unavailable: 'A track has no readable length',
  chapter_atoms_partial: 'Chapter markers are incomplete',
  tag_disagrees_across_tracks: 'Tracks carry different tags',
  asin_disagrees_across_tracks: 'Tracks carry different ASINs',
  container_unexpected: 'Unexpected file container',
  codec_unexpected: 'Unexpected audio format',
  duration_unstated: 'Running time not stated',
  stream_encrypted: 'Audio is encrypted',
  media_unreadable: 'Audio could not be read',
} satisfies Record<MediaHealthKind, string>

export const lookupLabels = {
  isbn: 'ISBN lookup',
  work: 'Title and author lookup',
  asin: 'ASIN lookup',
  discovery: 'Search',
  none: 'No lookup',
} satisfies Record<ProviderQueryKind, string>

// The ISBN lane an audiobook's companion EPUB planned (#1723). An audiobook
// states no ISBN of its own, so its row says where the ISBN came from, for
// every status.
export const companionIsbnLookupLabel = 'ISBN lookup (EPUB in this folder)'

export const sourceLookupLabel = (source: {
  laneKind: ProviderQueryKind
  companionIsbn: boolean
}): string => (source.companionIsbn ? companionIsbnLookupLabel : lookupLabels[source.laneKind])

// The server's closed lane status is an audit token, not something an owner can
// read: `candidates` and `candidate_limit_exceeded` differ by one word and mean
// opposite things. The consequence beside it is the server's own owner-facing
// sentence; this names the outcome the owner is looking for.
export const sourceStatusLabels = {
  candidates: 'Matches found',
  no_match: 'No match',
  disabled: 'Not enabled',
  insufficient_input: 'Too little to search with',
  authentication: 'Credentials refused',
  authorization: 'Access denied',
  rate_limited: 'Rate limited',
  unavailable: 'Unavailable',
  incomplete: 'Incomplete answer',
  malformed: 'Unreadable answer',
  oversized: 'Answer too large',
  candidate_limit_exceeded: 'Too many matches',
  query_limit_exceeded: 'Lookup limit reached',
  not_needed: 'Not needed',
  adapter_fault: 'Failed in Mechanical Librarian',
} satisfies Record<ProviderStatus, string>

// A cover-only lane asked for an audiobook's cover alone, so what it found is
// a cover, never a match (#1779). Its failures keep their own labels.
export const sourceStatusLabel = (source: {
  status: ProviderStatus
  coverOnly: boolean
}): string =>
  source.coverOnly && source.status === 'candidates'
    ? 'Cover found'
    : sourceStatusLabels[source.status]

export const claimKindLabels = {
  title: 'Title',
  subtitle: 'Subtitle',
  creator: 'Author',
  contributor: 'Contributor',
  series: 'Series',
  series_sequence: 'Series position',
  isbn: 'ISBN',
  asin: 'ASIN',
  publication_date: 'Publication date',
  date_event: 'Dated event',
  contextual_credit: 'Other credit',
  publisher: 'Publisher',
  description: 'Description',
  language: 'Language',
  subject: 'Subject',
  imprint: 'Imprint',
  genre: 'Genre',
  tag: 'Tag',
  duration: 'Running time',
  performance_type: 'Performance',
  adult_product: 'Adult content',
} satisfies Record<ClaimKind, string>

export const claimBasisLabels = {
  opening_matter: 'Opening pages',
  package_metadata: 'EPUB metadata',
  target_metadata: 'Audiobookshelf record',
  embedded_tag: 'Audio file tags',
  sidecar: 'Sidecar file',
  path: 'Folder and file name',
  prior_admission: 'Earlier matched edition',
} satisfies Record<ClaimBasis, string>

export const fieldStateLabels = {
  valid: 'Valid',
  absent: 'Not present',
  malformed: 'Unreadable',
  oversized: 'Too long to use',
  unsupported: 'Not supported',
  untyped: 'Unrecognised form',
  inapplicable: 'Does not apply',
} satisfies Record<FieldState, string>

// Where embedded cover art was found, inside `Embedded cover art is present (…)`.
export const audioSurfaceLabels = {
  container_format: 'container metadata',
  container_stream: 'an embedded image',
  mp4_ilst: 'MP4 tags',
  mp4_freeform: 'MP4 custom tags',
  mp4_freeform_tone: 'MP4 custom tags',
  id3_frame: 'ID3 tags',
  id3_user_text: 'ID3 custom tags',
  sidecar_libation: 'Libation sidecar file',
  sidecar_opf: 'OPF sidecar file',
  sidecar_nfo: 'NFO sidecar file',
  sidecar_cue: 'CUE sheet',
  path_folder: 'folder name',
  path_file: 'file name',
} satisfies Record<AudioSurface, string>

export const preparationStatusLabels = {
  provider: 'A source failed',
  configuration: 'Source settings need attention',
  authentication: 'Source credentials need attention',
  stale: 'The item changed during preparation',
  transient: 'Retries ran out',
  projection: 'The review could not be prepared',
  failed: 'Preparation failed',
  currentness: 'The source changed during preparation',
  persistence: 'Progress could not be saved',
  unsupported_media: 'Unsupported media',
  unreadable_media: 'Unreadable media',
  parser_refused: 'Media too complex to read',
  multi_file_budget_exceeded: 'Too many audio files',
  missing_required_input: 'Required input missing',
  malformed_retained_data: 'Saved preparation data unreadable',
  internal_failure: 'Preparation stopped',
  unknown: 'Preparation failed',
} satisfies Record<PreparationFailureStatus, string>
export const preparationStageLabels = {
  extraction: 'while reading the file',
  provider: 'while searching sources',
  proposal: 'while comparing evidence',
  admission: 'while checking the proposal',
  projection: 'while preparing the review',
  complete: 'after preparation finished',
} satisfies Record<PreparationFailureStage, string>

// Why writes are held, as a clause: `Excluded — the connection lacks
// permission for it`, `Writes are held because …`.
export const writeHoldReasons = {
  connection_unavailable: 'the library connection needs checking',
  insufficient_permission: 'the connection lacks permission for it',
  field_unavailable: 'the library does not expose it',
  unexpected_effect_behavior: 'an earlier write behaved unexpectedly',
} satisfies Record<SuspensionReason, string>

export const scanStateLabels = {
  idle: 'Not scanned yet',
  queued: 'Waiting to start',
  discovering: 'Finding your books',
  processing: 'Preparing your books',
  completed: 'Complete',
  completed_with_failures: 'Complete · some books could not finish',
} satisfies Record<ScanState, string>

// A recorded write's lifecycle, read the same way in Activity and History.
export const writeStateLabels = {
  requested: 'Requested',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  interrupted: 'Interrupted',
  planned: 'Planned',
  claimed: 'Preparing',
  applying: 'Applying',
  reconciling: 'Checking',
  retryable: 'Waiting to retry',
  verified: 'Verified',
  // A guarded write that closed for one of several reasons; Activity names the
  // reason beside it, work history does not yet. "stale" is the store's word,
  // not the owner's (#1542).
  stale: 'Closed',
  superseded: 'Replaced',
  abandoned: 'Stopped',
  no_change_required: 'No change needed',
} satisfies Record<DescriptionState, string>

export const previewDispositionLabels = {
  abstention: 'No automatic choice',
  candidate: 'Automatic choice',
  conflict: 'Sources disagree',
  ignore: 'Ignored by policy',
  keep_current: 'Keep current',
  manual: 'Needs you',
  unsupported: 'Not supported',
} satisfies Record<PreviewDisposition, string>

export const transitionLabels = {
  admitted: 'Entered review',
  superseded: 'Replaced by a newer review',
  no_case: 'No review needed',
  retired: 'Review closed',
  tombstoned: 'Left the library',
} satisfies Record<TransitionOutcome, string>

export const coverHistoryLabels = {
  automatic_prepared: 'Cover choices prepared',
  owner_retry_prepared: 'Cover choices prepared again',
  superseded: 'Cover choices replaced',
  selected: 'Cover chosen',
  kept_incumbent: 'Current cover kept',
  no_action: 'No cover change',
  stale: 'Cover choices out of date',
} satisfies Record<CoverHistoryOutcome, string>

export const coverSourceLabels = {
  abs: 'Audiobookshelf',
  epub: 'EPUB file',
  hardcover: 'Hardcover',
  open_library: 'Open Library',
  inventaire: 'Inventaire',
  audible_catalog: 'Audible',
  itunes_search: 'Apple Books',
  retained: 'Replaced cover',
} satisfies Record<CoverSourceKind, string>

// A cover policy's notes and a cover's omission reasons reach the browser
// inside documents the transport hands back unnarrowed, so they are open
// here: each known code has owner words and any other reads as one neutral
// sentence, never as its code.
const coverNoteLabels: Readonly<Record<string, string>> = {
  provider_fetching: 'Sources are still being fetched',
  actual_target_unverified: 'The cover in Audiobookshelf could not be confirmed',
  confirmed_incumbent_not_improved: 'No confirmed cover improves on the current one',
  deterministic_confirmed_order: 'Confirmed covers are ranked in a fixed order',
  no_confirmed_candidates: 'No cover is confirmed for this book',
  duplicate_preview: 'Same image as another cover',
  preview_byte_limit: 'Image too large to preview',
  aggregate_byte_limit: 'Left out to keep the set small',
  shortlist_limit: 'Beyond the number of covers compared',
  source_absent: 'No cover at this source',
  source_rejected: 'The source refused the image',
  source_invalid_declaration: 'The image was not what the source declared',
  source_unsupported_format: 'Unsupported image format',
  source_oversized: 'Image too large',
  source_decode_failed: 'The image could not be read',
  source_animated_or_multiframe: 'Animated or multi-frame image',
  source_unsafe_remote: 'The image address was not safe to fetch',
  source_unavailable: 'The source was unavailable',
  source_source_changed: 'The image changed while it was read',
  source_timeout: 'The source did not answer in time',
  source_truncated: 'The image arrived incomplete',
}
export const coverNoteLabel = (code: string) =>
  coverNoteLabels[code] ?? 'Another cover rule applies'

// Server identifiers with no meaning for a reader; never shown as a label.
export const opaqueSource = /^(?:local|evidence|provider)_[0-9a-f]+$/iu
// A provider's record reference, named by its provider: `audible-product-B08…`
// is `Audible product B08…`. Anything else stays the bounded text it is.
const candidateReferencePrefixes = [
  ['audible-product-', 'Audible product'],
  ['audnexus-book-', 'Audnexus book'],
  ['google-books-volume-', 'Google Books volume'],
  ['inventaire-edition-', 'Inventaire edition'],
  ['inventaire-work-', 'Inventaire work'],
  ['itunes-collection-', 'Apple Books collection'],
  ['open-library-edition-', 'Open Library edition'],
  ['open-library-work-', 'Open Library work'],
  ['audiosilo-recording-', 'AudioSilo recording'],
  ['hardcover-edition-', 'Hardcover edition'],
  ['hardcover-work-', 'Hardcover work'],
] as const
export const candidateReference = (value: string) => {
  const prefix = candidateReferencePrefixes.find(([code]) => value.startsWith(code))
  const text = prefix ? `${prefix[1]} ${value.slice(prefix[0].length)}` : value
  return text.length > 160 ? `${text.slice(0, 157)}…` : text
}

// A language code reads as its name with the code beside it, `English (en)`;
// the code stays because a name and a code are different values a policy
// tells apart. A value that is not a language tag, or that the runtime cannot
// name, is shown as it is.
export const languageName = (value: string) => {
  if (!/^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/u.test(value)) return value
  try {
    const name = new Intl.DisplayNames(undefined, { type: 'language', fallback: 'none' }).of(value)
    return name && name.toLowerCase() !== value.toLowerCase() ? `${name} (${value})` : value
  } catch {
    return value
  }
}

// A retained language observation names its language as the field value
// beside it does: `Language: pt-BR` reads `Language: Brazilian Portuguese
// (pt-BR)`, region and script included (#1286). The server sends the value the
// row states, and the first whole word equal to it is named, so the sentence
// around it can be reworded (#1802). An undetermined language keeps its tag,
// since the observation already says it is undetermined, and a value that is
// not a tag is shown as it is.
export const observationText = (row: { observation: string; language: string | null }) => {
  const { observation, language } = row
  if (language === null || language.toLowerCase() === 'und') return observation
  const name = languageName(language)
  if (name === language) return observation
  // A value languageName names is tag-shaped, so it needs no escaping.
  const match = new RegExp(`(?<![A-Za-z0-9-])${language}(?![A-Za-z0-9-])`, 'u').exec(observation)
  return match === null
    ? observation
    : observation.slice(0, match.index) + name + observation.slice(match.index + language.length)
}

// One vocabulary for every preparation count, in the masthead and the run line
// alike (#1366); a count not yet known reads as an ellipsis, never 0. Only the
// ready count stands at zero, so a quiet run is not a wall of zeros (#641).
// Each phrase keeps ordinary spaces and is rendered by CountPhrases, which keeps
// the number with its first word whenever the two fit on a line (#1013, #1366).
export const preparationOutcomes = (
  counts: {
    readonly ready: number
    readonly recorded: number
    readonly not_matched: number
    readonly failed: number
  } | null,
) =>
  [
    [counts?.ready, 'ready to browse'] as const,
    [counts?.recorded, 'recorded, nothing to decide'] as const,
    [counts?.not_matched, 'not matched'] as const,
    [counts?.failed, 'could not finish'] as const,
  ]
    .filter(([count], index) => index === 0 || count !== 0)
    .map(([count, words]) => `${count === undefined ? '…' : String(count)} ${words}`)

// Why Automatic chose nothing, in the same sentence the review case reads
// (`_AUTOMATIC_REFUSAL_TEXT` in `application/review_web.py`), so a reason is
// worded once wherever it appears.
export const automaticRefusalReasons: Readonly<Record<string, string>> = {
  isbn_title_material_conflict:
    'The sources disagree about the work or title. Choose the title yourself.',
  no_candidate: 'No source offered a value that can be used here.',
  ambiguous_evidence: 'The sources do not agree on one value.',
  target_work_contradicted: 'The sources suggest this value belongs to a different work.',
  target_work_applicability_unproved:
    'The sources do not show that this value describes this work.',
  target_state_unusable: 'The current value cannot be changed for you.',
  automatic_unqualified: 'Automatic is not offered for this kind of change, so it is left to you.',
  manifestation_verification_required: 'The sources do not confirm this ISBN for this edition.',
  description_quality_floor_not_met:
    'No source description meets the rules for choosing one for you.',
  candidate_language_differs:
    'The value comes from a source in a different language from this book.',
  candidate_credit_prefixed:
    'The proposed value starts with a credit label such as "Narrated by", so it is never written for you.',
  candidate_strips_marks:
    'The proposed value drops accents from a name you already have, so it is never written for you.',
  candidate_promotional_title:
    'The proposed title carries an announcement such as "Now a Major Motion Picture", so it is never written for you.',
  insufficient_support: 'Not enough sources agree to choose a value for you.',
  series_membership_incomplete: 'The sources give an incomplete or unreadable series or position.',
  series_conflicting_membership: 'The sources disagree about the series.',
  series_provider_evidence_required: 'No source confirms this series.',
  subtitle_local_corroboration_required: 'No other source confirms this subtitle.',
  subtitle_restates_series:
    'The proposed subtitle only repeats the series, so it is never written for you.',
  subtitle_segment_provider_dissent: 'The sources disagree with splitting the title this way.',
  subtitle_segment_decorated_consensus:
    'The sources do not support splitting this text into a subtitle.',
  atomic_bundle_unavailable: 'A linked field this choice depends on is unavailable.',
  corroborated_split_unavailable:
    'The confirmed title and subtitle do not match the current combined title.',
  option_evidence_unavailable: 'The source details your chosen option needs are unavailable.',
  policy_option_unavailable: 'The option you chose does not apply to this field.',
  target_projection_oversized: 'The result would be longer than the library accepts.',
  policy_off: 'Tag handling is off.',
  explicit_unsupported: 'Explicit content is never marked for you with this setting.',
  source_value_malformed_or_incomplete: 'The sources give an unreadable or incomplete ISBN.',
  source_values_conflict: 'The sources give different ISBNs.',
  source_value_insufficient_association:
    'The sources do not tie this ISBN closely enough to this recording.',
  source_unavailable: 'The sources could not be read.',
  source_value_absent: 'No source supplied an ISBN.',
  series_provider_failure_isbn: 'An ISBN lookup did not return a usable answer.',
  series_provider_failure_work: 'A title and author lookup did not return a usable answer.',
  series_provider_failure_asin: 'An ASIN lookup did not return a usable answer.',
}

export const evidenceRoleLabel = (role: EvidenceRole) =>
  ({
    local_presence: 'Already in the library',
    external_corroboration: 'Confirms this value',
    external_observation: 'Reports this value',
  })[role]

const coverReviewerNames: Record<string, string> = {
  'openai/gpt-6-luna': 'GPT-6 Luna',
  'xiaomi/mimo-v2.6-flash': 'MiMo 2.6 Flash',
  'qwen/qwen3.8-flash': 'Qwen 3.8 Flash',
  'mistralai/ministral-3b-2512': 'Ministral 3B',
  'openai/gpt-5.6-luna': 'GPT-5.6 Luna',
  'mistralai/mistral-small-2603': 'Mistral Small 4',
}

export const coverReviewerLabel = (model: string) =>
  coverReviewerNames[model] ? `${coverReviewerNames[model]} (${model})` : model

// A reviewer's state, inside `Advisory availability: …` (#1286).
export const reviewerStateLabels = {
  ready: 'ready to sign in',
  configured: 'configured',
  starting: 'starting',
  issued: 'sign-in code issued',
  waiting: 'waiting for you to sign in',
  validating: 'checking',
  usable: 'ready to use',
  expired: 'sign-in expired',
  cancelled: 'sign-in cancelled',
  failed: 'failed',
  interrupted: 'interrupted',
  host_unsupported: 'not supported on this host',
  not_provisioned: 'not set up on this host',
  unconfigured: 'not configured',
  invalid_key: 'API key refused',
  quota_exhausted: 'quota used up',
  rate_limited: 'rate limited',
  model_unavailable: 'model unavailable',
  model_text_only: 'model reads text only',
  malformed_response: 'unreadable response',
  unreachable: 'unreachable',
  response_too_large: 'answer too large',
  timed_out: 'timed out',
  data_policy_refused: 'refused by data policy',
  image_limit_exceeded: 'too many images',
} satisfies Record<ReviewerState, string>
// What a configured reviewer's failure asks the owner to change, wherever the
// failure is shown: reviewer access in Settings and a failed cover review.
export const reviewerFailureCopy = {
  invalid_key: 'The reviewer’s API key was refused. Replace it in Settings → Reviewer, then retry.',
  quota_exhausted: 'The reviewer has no quota left. Add quota, then retry.',
  rate_limited: 'The reviewer is rate limited. Wait, then retry.',
  model_unavailable:
    'The reviewer model is unavailable. Choose another model in Settings → Reviewer.',
  model_text_only:
    'The reviewer model cannot read images. Choose a vision model in Settings → Reviewer.',
  malformed_response: 'The reviewer’s answer could not be read. Retry later.',
  unreachable: 'The reviewer could not be reached. Check network access, then retry.',
  response_too_large:
    'The reviewer’s answer was too large to read. Retry later, or choose another model in Settings → Reviewer.',
  timed_out:
    'The reviewer did not answer in time. Retry, or choose a faster model in Settings → Reviewer.',
  data_policy_refused:
    'No provider for this model accepts your OpenRouter data policy. Change your privacy settings on OpenRouter, or choose another model in Settings → Reviewer.',
  image_limit_exceeded:
    'The reviewer model accepts fewer images than this review sends. Choose another vision model in Settings → Reviewer.',
} satisfies Record<ReviewerFailureCode, string>
