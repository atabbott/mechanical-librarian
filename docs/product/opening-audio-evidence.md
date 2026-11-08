# Opening-audio evidence

Mechanical Librarian does not transcribe audio. This page is an unscheduled plan
for how it could. The plan grants no write permission.

## Direction

Opening credits often state an audiobook's title, author, narrator, series,
publisher or performance type, such as abridged or full cast. They are often
more reliable than a path or a tag. The plan is for an optional feature that
transcribes a limited stretch of the opening and records each claim spoken
outright as evidence. It leaves out transcribing the whole book or its middle,
identifying a book from its story, recognising voices, and sharing audio or
voice data.

## Rules for any implementation

Transcription is optional, and nothing waits for it. When it is off, at capacity
or failing, the audiobook stays available and Mechanical Librarian keeps reading
other evidence. An unavailable model, an unsupported codec, a used-up quota or a
transcript with no credits is an outcome of the run. It is never evidence that a
field has no value.

Mechanical Librarian reads only short windows at the start of the first playable
track. It picks the same track every time from the same files, and records why.
Your library service's track order comes first, with filename order as a
fallback. Extras such as samples and interviews are left out. The windows stay
under a hard total limit, well inside the opening, and keep their original times
in the track. If the item's fingerprint changes, the job is cancelled before
transcription.

Each step is separate: picking the track, cutting the windows, transcribing,
pulling out claims, matching them and applying policy. Transcription goes
through one interface, `Transcriber`. It returns the text in segments, with the
language, timing, confidence, the model that ran and the kind of any failure. It
never returns a decision about metadata. The language comes from the audio or a
language you set for the library, never from a guess based on the path or a
global default. Fixed patterns read clear phrases ("written by", "narrated by",
"book two of"). An isolated reviewer can settle unclear wording only by choosing
words the transcript contains and roles from a fixed list.

By default, transcription runs where Mechanical Librarian runs. Each model and
runtime is a fixed version with a known checksum, installed when the image is
built and never at run time. Each hosted transcription service is off by
default, and you turn each one on separately. Before you do, Mechanical
Librarian shows the audio and data sent, the credentials and billing needed, how
long the service keeps the data, and what works without it.

Each claim records the field, the value, the spoken role, the words in the
transcript and their times in the audio. It also records the item's fingerprint,
the track, the language, the confidence and any warnings. It records the
versions of the transcript, the step that pulls out claims, the schema and the
model as well. An unclear value is left out. That step never makes up an
identifier, a person, a series or a position in a series. Spoken text that
sounds like an instruction is only evidence.

A transcript, the claims pulled from it and any reviewer reading of it are one
source. Their agreement is never confirmation from another source. When tags or
path values are used as spelling hints, Mechanical Librarian records each one
and compares the result with a run without hints. A hint and the transcript it
shaped never count as two sources.

A spoken credit can be wrong. It can name the performer first, describe another
work in a preview, or be misheard. A claim can confirm a value, propose one,
improve a search or show a contradiction. Evidence from catalogues, tags and the
path, and your library's current values, stay visible beside it. A spoken ISBN
can help a search or confirm evidence from an independent source. On its own, it
never establishes an edition. Several spoken ISBNs, or one that disagrees with
other evidence, stay unresolved. An ISBN spoken in a preview or about another
work never counts, even if it matches.

Opening audio and transcripts are private content under the
[data-privacy policy](../architecture/data-privacy.md). Your audio files stay
read-only. Temporary audio clips are always deleted, even after a failure or a
recovery. They never reach logs, support bundles, backups or caches. Mechanical
Librarian records hashes of the transcript and the clips, segment details, the
quoted passages, the claims and where each came from, but no audio. Keeping a
full transcript of the opening is a separate setting, and that transcript is
stored only where Mechanical Librarian runs. A reviewer is sent only the quoted
passages. Exports and support bundles carry no audio or transcript.

Transcription has lower priority than all core work. It has limits on CPU,
memory, run time, temporary space and parallel jobs. By default, one local job
runs at a time. A restart does not reset a job's retries, and an interrupted job
is picked up again. A cut-off stops new jobs after repeated failures. Mechanical
Librarian holds no database transaction or write lock while it decodes or
transcribes audio.

Each field qualifies on its own before its spoken claims can affect an automatic
decision. Until then, Mechanical Librarian measures the claims but never uses
them for one. An automatic decision from them passes the same checks as one from
any other evidence. A change of model, prompt, schema, sampling or backend
version starts a new evaluation. If transcription harms playback or core work,
or a field exceeds the error rates it qualified with, all spoken claims go back
to being measured without being used. They also go back if Mechanical Librarian
cannot prove it deleted the temporary audio.

## Open decisions

Before work starts, a decision record (ADR) or a configuration contract must
settle the transcription backend and how models are packaged. It must also set
the limits on audio clips and resources, and say whether full transcripts are
kept by default. It must settle the languages, hosted transcription, scheduling
and the threshold for each field.
