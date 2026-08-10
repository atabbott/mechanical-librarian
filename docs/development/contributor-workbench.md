# Contributor workbench

The installed `mechanical-librarian` command is a production runtime surface and
supports only `review-serve`. The repository-only `workbench/` package retains
the evidence, scenario, inspection, and contained-reviewer workflows needed for
development. It is outside `src/`, has no installed entry point, and is excluded
from the wheel and container.

Run its catalogue from a checkout with:

```text
PYTHONPATH=src:workbench uv run python -m mechanical_librarian_workbench --format summary
```

The catalogue is closed and code-extensible. Each entry names a small typed
request and one or more existing application composition operations. Adding a
scenario requires a typed request, an explicit branch in `execute`, catalogue
registration, and focused proof. Dynamic discovery, plugins, arbitrary module or
shell execution, workflow languages, and general DAG execution are deliberately
absent.

## Executing a scenario

Call the typed API from contributor code or a focused test. For example, an
offline report inspector is constructed and executed as follows:

```python
from pathlib import Path

from mechanical_librarian_workbench import (
    ReportRequest,
    WorkbenchContext,
    execute,
)

context = WorkbenchContext(Path(".workbench/report-inspection"), input_class="test")
result = execute(context, ReportRequest("inspect", report_id="report_<digest>"))
print(result.render("summary"))  # or canonical "json"
```

All paths resolve beneath the explicit workspace. Temporary contexts are
preferred and delete themselves on exit:

```python
with WorkbenchContext.temporary() as context:
    result = execute(context, typed_request, test_operations)
```

Every successful scenario returns `WorkbenchResult`. Its summary and canonical
JSON contain the same scenario name, completion status, artifact identifier,
artifact byte count, and SHA-256 digest. Large output bytes remain in the local
artifact directory. Operation exceptions cross one redacting boundary and
become a closed `WorkbenchFailure`; do not print exception causes or raw provider
data.

## Authority and input gates

Contexts accept only synthetic fixtures, explicit test workspaces, or a private
corpus whose approval is stated at construction. `/`, `/data`, path traversal,
production target classes, and unknown input classes fail before execution.
There is no force flag or production bridge. The workbench does not relax the
application's exact-current, credential, parser, artifact, currentness, or
write-safety contracts.

Provider and contained-reviewer calls additionally require an
`IsolatedNetworkProfile`. Its identifier, sanitized-input assertion, and bounded
authority assertion are validated. A canonical attempt record is durably
created in the disposable workspace before each network operation. Use only
fake or explicitly isolated services and sanitized inputs; never point the
workbench at a production library or service.

## Qualification

Focused tests prove the full catalogue reaches the shared application
operations, action-specific inputs fail closed, private inputs require approval,
network attempts are recorded, temporary state is removed, and diagnostics are
redacted. Exact-artifact qualification additionally builds the wheel and
container, invokes installed help from outside the checkout, and proves the
workbench package cannot be imported or executed there.
