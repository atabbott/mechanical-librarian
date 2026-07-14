# EPUB Package Evidence Report

## Purpose and current boundary

The F06 operator surface emits one deterministic, bounded, cited package
metadata envelope without persistence or retained raw artifacts:

```console
mechanical-librarian epub-package-report \
  --config config-v1.json \
  --mount books \
  --file generated/book.epub \
  --format json
```

`--format` is optional and defaults to `json`; `text` is a deterministic
rendering of the same canonical projection. The real command composes the typed
contract with no-follow descriptor access, a bounded transient child, hostile
ZIP/XML validation, and source-stability verification.

The command uses one configured read-only logical mount and a relative logical
file reference. It has no database, persistence, network, target-service,
provider, reviewer, credential, temporary extraction, or write authority. That
least-authority product boundary does not claim a kernel sandbox.

## Output and exits

Success writes only to stdout. Every failure writes only its canonical envelope
to stderr and leaves stdout empty. Every envelope contains a stable typed
`outcome`; JSON is canonical and bounded to 524,288 UTF-8 bytes.

| Exit | Outcomes |
| ---: | --- |
| 0 | `success`, `no_applicable_evidence` |
| 2 | `invalid_invocation`; argument parsing emits the same typed, path-free envelope |
| 3 | `invalid_configuration`, `invalid_reference`, `unavailable_source` |
| 4 | `unsupported_format`, `unsupported_version`, `unsupported_compression`, `unsupported_multiple_renditions`, `encrypted` |
| 5 | `malformed`, `integrity_failure`, `resource_limit`, `timeout`, `cancellation`, `unstable_source` |
| 70 | `child_failure`, `internal_failure` |

Reports contain path-free citations, repeated raw observations in source order,
normalized observations in controlled field/display/source order, warnings in
code/citation order, declarations, the fixed limit profile, explicit artifact
absence, and deterministic logical usage counters. Runtime memory, CPU,
descriptor and elapsed-time measurements belong to qualification evidence and
are not canonical output.

Untrusted metadata is a one-way projection. A value that resembles a
credential, private path, filesystem URI, control-text spoof, or other unsafe
public text is never copied into raw or normalized report fields. The report
retains only a deterministic `unsafe-source-text-sha256` placeholder plus the
cited `unsafe_metadata_redacted` warning. Neither JSON nor text rendering can
recover the rejected source value.

## Supported package subset

The frozen revisions and deliberate omissions are maintained in the
[compatibility policy](../reference/compatibility.md#epub-package-compatibility-baseline-for-f06).
F06 reads package declarations only. It does not parse navigation XHTML, NCX
content, landmarks, opening/body XHTML, cover binaries or linked metadata, and
does not persist evidence. Those behaviors remain in F07 and later slices.

The parent starts exactly one direct child in a new process group. It passes the
already-open read-only source descriptor and one parent-loss control descriptor;
it does not delegate a directory, reusable filesystem locator, network client,
credential, shell, or product write port. Timeout and cancellation signal the
child's process group, apply the bounded grace period, then kill and reap the
group. This is direct-child lifecycle management, not an unattended process
tree or a kernel sandbox.

The child starts with the running Python executable plus `-I` and `-B`, an inert
`/` working directory, closed standard input after its bounded context, and a
fixed allowlisted environment. `HOME`, `PYTHONPATH`, ambient credentials and
proxy variables are absent; `PYTHONDONTWRITEBYTECODE=1` and
`sys.dont_write_bytecode` prevent bytecode output.

The complete repository suite passes on macOS development with CPython 3.13.14
and 3.14.6. The F06 parser/process cohort also passes as a non-root process on
Linux CPython 3.13.11 with the repository and installed environment read-only,
networking disabled, and exact assertions for the 256 MiB `RLIMIT_AS`,
one-process `RLIMIT_NPROC`, CPU, descriptor and core gates. Linux installs
`PR_SET_PDEATHSIG` before lowering `RLIMIT_NPROC`; Darwin uses the control-pipe
watcher because it does not make the Linux production resource claim. The
cohort additionally exercises output bounds, cancellation, timeout, parent
loss, process-group cleanup, sibling usability and no-bytecode operation. See
[ADR-0007](../architecture/decisions/0007-transient-epub-package-child.md).

## Reproducible qualification and recovery demonstrations

The synthetic cohort manifest records deterministic generation inputs and the
exact observation-field, citation-section and warning projections for every
parser fixture, plus a per-fixture SHA-256 over the complete canonical
observation, normalized-observation, declaration/citation and warning
projection. Its executable boundary matrix covers every configurable
default and every constructible immutable hard cap at below, exact and above
values. The manifest records why exact compressed-member and manifest/spine
collection hard-cap inputs cannot coexist with the equal source-archive and
public-declaration caps. Its hostile matrix executes ZIP, path, XML, OPF,
encryption, normalization and report-bound evidence. Every generated archive
and canonical report is produced twice and must be byte-identical. Run it from
an installed wheel with:

```console
python -m unittest \
  tests.unit.test_epub_package_fixture_manifest \
  tests.unit.test_epub_package_extractor \
  tests.contract.test_local_evidence_extractor \
  tests.integration.test_epub_package_report
```

Expected output ends in `OK`. The process cases inject cancellation, timeout,
an explicit nonzero child crash, leader exit with a TERM-resistant descendant,
parent loss, malformed output, and excessive output. Each case asserts the
exact typed failure, reaps the child or group, and—where recovery is
meaningful—runs a sibling extraction that must return `success`. No manual
cleanup step is expected; a surviving child, descriptor delta, temporary
artifact or unusable sibling fails qualification.
The supported command itself is also exercised with representative generated
success, malformed, required-encryption and resource-limit packages. Success is
exit 0 on stdout; those hostile cases are respectively exit 5 `malformed`, exit
4 `encrypted`, and exit 5 `resource_limit` on stderr, with the opposite stream
empty and private temporary paths absent.

Actual resource measurements are produced by the public-safe synthetic runner:

```console
PYTHONPATH=src:. python scripts/qualify_epub_package.py
```

The committed Linux result is
[`epub-package-f06-qualification.json`](../reference/epub-package-f06-qualification.json).
It was measured as UID/GID `65534:65534` in the digest-pinned CPython 3.13 image
already listed in the compatibility policy, with networking disabled, a
read-only container root and repository mount, 512 MiB container memory and a
64-process container ceiling. The run observed 26,452 KiB child peak RSS,
0.190492 seconds combined child CPU, 0.309761 seconds elapsed, six child file
descriptors, one process-group member, 24,935 output bytes, zero parent
descriptor delta and zero surviving children. The artifact is schema-checked
and its measurements are asserted against the child gates by the cohort tests.

To reproduce that isolation, first prepare a disposable environment while
dependency access is allowed, then execute the measurement with networking
disabled:

```console
docker volume create f06-qualification-env
docker run --rm --entrypoint /bin/sh \
  -v f06-qualification-env:/env -v "$PWD:/repo:ro" \
  ghcr.io/astral-sh/uv@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca \
  -c 'uv venv --clear /env && uv pip install --python /env/bin/python /repo/dist/mechanical_librarian-0.0.0-py3-none-any.whl'
docker run --rm --network none --read-only --user 65534:65534 \
  --pids-limit 64 --memory 512m --cpus 1 --entrypoint /bin/sh \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
  -v f06-qualification-env:/env:ro -v "$PWD:/repo:ro" \
  ghcr.io/astral-sh/uv@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca \
  -c 'cd /repo && PYTHONPATH=/repo /env/bin/python scripts/qualify_epub_package.py'
docker volume rm f06-qualification-env
```

The measurement command emits one canonical JSON line with `outcome=success`.
It uses only repository-authored synthetic bytes and never points at a live
library.
