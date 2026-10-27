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
  ReviewerState,
  ScanState,
  ShadowDisposition,
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
  isbn_creator_supported_title: 'ISBN and author evidence support this title',
  isbn_title_material_conflict:
    'The sources disagree about the work or title; choose the title yourself',
  bibliographic_combination_unresolved:
    'The sources do not settle one combination of these details; choose them yourself',
  competing_evidence: 'The sources disagree; choose the value yourself',
  date_refinement_requires_corroboration:
    'A more exact date needs a second source before it can replace this one',
  date_year_conflict: 'The sources disagree about the year',
  field_policy_conflict:
    'Your policy for this field and the evidence point different ways; choose the value yourself',
  insufficient_independent_corroboration: 'Not enough independent sources agree on this value',
  no_safe_bibliographic_combination:
    'No combination of these details is safe to apply automatically',
  populated_subjective_field: 'This field already has a value of yours, so a change needs you',
  provider_consensus_title_bracket_source_tag:
    'Independent providers agree after removing the bracketed source tag',
  provider_consensus_title_explicit_identifier:
    'Independent providers agree after removing the labelled ISBN',
  provider_consensus_title_parenthesized_bare_number:
    'Independent providers agree after removing the parenthesized number',
  provider_consensus_title_parenthesized_series_name:
    'Independent providers agree after preserving the structured series name',
  provider_consensus_title_parenthesized_year:
    'Independent providers agree after removing the parenthesized year',
  provider_consensus_title_subtitle_segment:
    'Independent providers agree after preserving the colon subtitle',
  provider_consensus_title_colon_unused:
    'The colon subtitle is local corroboration only; choose the title yourself',
  provider_consensus_title_series_unpreserved:
    'The parenthesized series name is not preserved as structured series evidence',
  decoration_equivalent_bracket_source_tag:
    'Providers agree once the bracketed source tag is removed; the current title is kept',
  decoration_equivalent_explicit_identifier:
    'Providers agree once the labelled ISBN is removed; the current title is kept',
  decoration_equivalent_parenthesized_bare_number:
    'Providers agree once the parenthesized number is removed; the current title is kept',
  decoration_equivalent_parenthesized_series_name:
    'Providers agree once the parenthesized series name is removed; the current title is kept',
  decoration_equivalent_parenthesized_year:
    'Providers agree once the parenthesized year is removed; the current title is kept',
  decoration_equivalent_subtitle_segment:
    'Providers agree once the colon subtitle is removed; the current title is kept',
  decoration_tidy_bracket_source_tag:
    'Providers agree once the bracketed source tag is removed; the current title is tidied to the plain form',
  decoration_tidy_explicit_identifier:
    'Providers agree once the labelled ISBN is removed; the current title is tidied to the plain form',
  decoration_tidy_parenthesized_bare_number:
    'Providers agree once the parenthesized number is removed; the current title is tidied to the plain form',
  decoration_tidy_parenthesized_series_name:
    'Providers agree once the parenthesized series name is removed after preserving the structured series name; the current title is tidied to the plain form',
  decoration_tidy_parenthesized_year:
    'Providers agree once the parenthesized year is removed; the current title is tidied to the plain form',
  decoration_tidy_subtitle_segment:
    'Providers agree once the colon subtitle is removed; the current title is tidied to the plain form',
  decoration_tidy_series_unpreserved:
    'The parenthesized series name is not preserved as structured series evidence; choose the title yourself',
  decoration_equivalence_provider_dissent:
    'Another provider states a different work title; choose the title yourself',
  decoration_equivalence_decorated_consensus:
    'More providers state the decorated title than the plain one; choose the title yourself',
  subtitle_segment_uncorroborated:
    'A colon segment in a title matches no independent subtitle evidence',
  subtitle_segment_provider_dissent:
    'Another provider states a different subtitle; choose the subtitle yourself',
  subtitle_segment_decorated_consensus:
    'The colon segment reads as title text: no provider carries it, or no more providers state the plain title than carry the decorated form',
  provider_consensus_contributor_display:
    'Independent providers agree on the contributor display form',
}
export type MaterialComparisonReason = keyof typeof materialReasonLabels
export const materialReasonLabel = (code: string) =>
  Object.hasOwn(materialReasonLabels, code)
    ? materialReasonLabels[code as MaterialComparisonReason]
    : undefined

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

// The server's closed lane status is an audit token, not something an owner can
// read: `candidates` and `candidate_limit_exceeded` differ by one word and mean
// opposite things. The consequence beside it is the server's own owner-facing
// sentence; this names the outcome the owner is looking for.
export const sourceStatusLabels = {
  candidates: 'Candidates found',
  no_match: 'No match',
  disabled: 'Not enabled',
  insufficient_input: 'Too little to search with',
  authentication: 'Authentication failed',
  authorization: 'Access denied',
  rate_limited: 'Rate limited',
  unavailable: 'Unavailable',
  incomplete: 'Incomplete evidence',
  malformed: 'Malformed evidence',
  oversized: 'Response too large',
  candidate_limit_exceeded: 'Too many candidates',
  query_limit_exceeded: 'Query allowance reached',
} satisfies Record<ProviderStatus, string>

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
  sidecar_audiobookshelf: 'Audiobookshelf sidecar file',
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
  parser_refused: 'Media too complex to read safely',
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
  failed: 'Failed safely',
  interrupted: 'Interrupted',
  planned: 'Planned',
  claimed: 'Preparing',
  applying: 'Applying',
  reconciling: 'Checking',
  retryable: 'Waiting safely',
  verified: 'Verified',
  stale: 'Stale',
  superseded: 'Superseded',
  abandoned: 'Stopped safely',
  no_change_required: 'No change required',
} satisfies Record<DescriptionState, string>

export const shadowDispositionLabels = {
  abstention: 'No automatic choice',
  candidate: 'Automatic choice',
  conflict: 'Conflicting evidence',
  ignore: 'Ignored by policy',
  keep_current: 'Keep current',
  manual: 'Owner review',
  unsupported: 'Unsupported',
} satisfies Record<ShadowDisposition, string>

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
  coverNoteLabels[code] ?? 'Another cover safeguard applies'

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

export const automaticRefusalReasons: Readonly<Record<string, string>> = {
  no_candidate: 'No eligible value was found in the retained evidence.',
  ambiguous_evidence: 'The retained evidence does not select one value for automatic handling.',
  target_work_contradicted: 'The retained evidence contradicts applicability to this work.',
  target_work_applicability_unproved:
    'The retained evidence does not establish that this value describes this work.',
  target_state_unusable:
    'The current field value cannot be used safely for this automatic operation.',
  automatic_unqualified: 'This policy option is not qualified for automatic handling.',
  manifestation_verification_required:
    'The retained evidence does not verify the ISBN for this edition.',
  description_quality_floor_not_met:
    'Retained source text does not meet the automatic synopsis rules.',
  series_membership_incomplete:
    'Series membership or position evidence is incomplete or malformed.',
  series_conflicting_membership:
    'The retained evidence does not resolve series membership for automatic handling.',
  series_provider_evidence_required: 'Provider evidence supporting this series value is required.',
  subtitle_local_corroboration_required: 'Independent evidence does not corroborate this subtitle.',
  subtitle_segment_provider_dissent:
    'Provider subtitle evidence disagrees with the proposed title split.',
  subtitle_segment_decorated_consensus:
    'The retained title evidence does not support separating this text into a subtitle.',
  atomic_bundle_unavailable: 'The required linked field selection is unavailable.',
  corroborated_split_unavailable:
    'The corroborated title and subtitle do not match the current combined title.',
  option_evidence_unavailable: 'Evidence required by the selected representation is unavailable.',
  policy_option_unavailable: 'The selected policy option is unavailable for this projection.',
  target_projection_oversized: 'The resulting field would exceed the supported size limit.',
  policy_off: 'Tag handling is off.',
  explicit_unsupported: 'Automatic explicit-content handling is unavailable for this option.',
  source_value_malformed_or_incomplete: 'ISBN evidence is malformed or incomplete.',
  source_values_conflict: 'Conflicting ISBN values remain.',
  source_value_insufficient_association: 'Association with this recording is insufficient.',
  source_unavailable: 'Source evidence is unavailable.',
  source_value_absent: 'No ISBN value was supplied.',
  series_provider_failure_isbn: 'A contributing ISBN lookup did not supply usable evidence.',
  series_provider_failure_work: 'A contributing work lookup did not supply usable evidence.',
  series_provider_failure_asin: 'A contributing ASIN lookup did not supply usable evidence.',
}

export const evidenceRoleLabel = (role: EvidenceRole) =>
  ({
    local_presence: 'Local presence',
    external_corroboration: 'External corroboration',
    external_observation: 'External observation',
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
} satisfies Record<ReviewerState, string>
