# External contract baseline

Use this baseline whenever a change consumes or claims compatibility with a
versioned external API, protocol, schema, file format, service, or tool. Build
it before implementation or independent review. Current code, tests, fixtures,
and review comments are evidence about the implementation, not authority for
the upstream contract.

## Freeze the supported contract

Record:

- the exact supported upstream version or revision and authoritative sources;
- endpoint, operation, request, response, error, pagination, and default
  behavior used by the change;
- every upstream field or state that can affect authentication, authorization,
  scope, capability, filtering, identity, compatibility, or failure handling;
- downstream producers, mappings, consumers, and observable decisions for
  those facts; and
- deliberate omissions, with contract evidence that they cannot change the
  supported outcome.

When access or capability is involved, explicitly inspect authentication
validity, account state, tenant or library scope, per-item or content filters,
media-operation permissions, restricted or private-content controls, inherited
role defaults, query-layer enforcement, and the final projected capability.
Do not collapse distinct restrictions into one generic permission flag.

If an authoritative source is unavailable or materially ambiguous, fail the
affected compatibility claim closed. Do not let a minimal fixture define the
contract by accident.

## Build the decision matrix

For each decision-relevant upstream fact, record:

| Upstream fact | Valid partitions | Absent/null/malformed/unknown behavior | Downstream consumer and decision | Fixture and assertion |
| --- | --- | --- | --- | --- |

Include valid near-misses and interacting restrictions where one field may
permit an operation while another independently forbids it. The matrix is
complete only when it follows each fact from upstream payload through adapter,
application, and supported interface behavior.

## Prove fixture fidelity

Prefer generated or irreversibly sanitized fixtures derived from the frozen
schema or a disposable supported service. A smaller fixture is acceptable only
when the baseline names every omitted decision-relevant field and proves the
omission is neutral.

Compare fixture shape, types, defaults, and representative restricted states
with the authoritative contract. Where practical, add a drift assertion or
fixture-generation check. Green tests against a self-authored fake do not prove
compatibility until this fidelity check passes.

## Review use

Give reviewers the frozen baseline and raw source artifacts, not the worker's
interpretation or the latest review comment. Require them to derive their own
state matrix and search for one counterexample outside the existing fixtures.
A no-finding result must state how fixture fidelity and each decision-relevant
contract family were checked.
