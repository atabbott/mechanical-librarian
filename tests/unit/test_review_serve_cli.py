"""In-process argv qualification for the installed command surface.

`review-serve` is the one installed command with no in-process CLI cohort of
its own. `tests/unit/test_review_tls.py` does invoke it with `--port`, but
through `subprocess.Popen([sys.executable, "-m", "mechanical_librarian", ...])`,
which qualifies the HTTPS boundary of a real server rather than the parser: a
regression in argument handling surfaces there only as a server that fails to
start, and #737's mutation run recorded `_port` as reached by no test at all.
The tables below run the same surface in process, where each argument's
refusal and parsed type is observable.

`_port` is imported directly, which this suite otherwise avoids. It is an
`argparse` type converter, so its refusals reach `_Parser.error`, which raises
one fixed `ValueError("invalid invocation")`; `main` catches that and writes
its own fixed diagnostic. The converter's own message is therefore unobservable
through any public seam, and the bound it enforces is the contract under test.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import pytest

from mechanical_librarian.interfaces.cli import _port, main

# Every command with the complete set of arguments its parser declares
# required. Order matches the order each runner receives them in, so a case can
# name a prefix of the tuple `main` passes on.
_COMMANDS: dict[str, tuple[tuple[str, str], ...]] = {
    "review-serve": (
        ("--config", "/config.json"),
        ("--database", "/state.sqlite3"),
        ("--catalogue", "/catalogue.sqlite3"),
        ("--artifacts", "/artifacts"),
    ),
    "policy-preview": (
        ("--config", "/config.json"),
        ("--database", "/state.sqlite3"),
        ("--profile", "/profile.json"),
    ),
    "cover-review": (
        ("--config", "/config.json"),
        ("--database", "/state.sqlite3"),
        ("--artifacts", "/artifacts"),
        ("--case", "review_case_1"),
    ),
    "review-activity": (
        ("--config", "/config.json"),
        ("--database", "/state.sqlite3"),
    ),
    "provider-recovery": (
        ("--config", "/config.json"),
        ("--database", "/state.sqlite3"),
        ("--catalogue", "/catalogue.sqlite3"),
        ("--artifacts", "/artifacts"),
    ),
    "qualification-state-bridge": (
        ("--database", "/state.sqlite3"),
        ("--backup", "/backup.sqlite3"),
        ("--output", "/current.sqlite3"),
    ),
}

_POSITIONALS: dict[str, tuple[str, ...]] = {
    "cover-review": ("show",),
    "qualification-state-bridge": ("retained-library-v26-to-v31",),
}

# What each runner must receive for a complete invocation, including the two
# `review-serve` defaults. `policy-preview` is absent: `main` reads the profile
# from disk before delegating, so its parsed types are proved in
# `test_policy_preview_cli.py`, where a real profile file already exists.
_DELEGATED: dict[str, tuple[object, ...]] = {
    "review-serve": (
        Path("/config.json"),
        Path("/state.sqlite3"),
        Path("/catalogue.sqlite3"),
        Path("/artifacts"),
        "127.0.0.1",
        8080,
    ),
    "cover-review": (
        Path("/config.json"),
        Path("/state.sqlite3"),
        Path("/artifacts"),
        "review_case_1",
        "show",
        None,
        None,
    ),
    "review-activity": (Path("/config.json"), Path("/state.sqlite3")),
    "provider-recovery": (
        Path("/config.json"),
        Path("/state.sqlite3"),
        Path("/catalogue.sqlite3"),
        Path("/artifacts"),
    ),
    "qualification-state-bridge": (
        Path("/state.sqlite3"),
        Path("/backup.sqlite3"),
        Path("/current.sqlite3"),
        "retained-library-v26-to-v31",
    ),
}

# One case per `required=True` site the parser declares, plus the bare
# invocation that exercises `add_subparsers(..., required=True)`. Each case
# omits exactly one argument, because a mutant clears exactly one site: an
# invocation omitting several is refused by whichever sites remain required,
# and proves nothing about the one under test.
_OMISSIONS: tuple[tuple[str, str], ...] = (
    *(
        (command, flag)
        for command, flags in _COMMANDS.items()
        for flag, _value in flags
    ),
    ("", ""),
)
_OMISSION_IDS: tuple[str, ...] = tuple(
    f"{command or 'no'}-{flag.lstrip('-') or 'command'}" for command, flag in _OMISSIONS
)


def _argv(command: str, omit: str = "") -> list[str]:
    if not command:
        return []
    argv = [command, *_POSITIONALS.get(command, ())]
    for flag, value in _COMMANDS[command]:
        if flag != omit:
            argv += [flag, value]
    return argv


def _invoke(argv: list[str], calls: list[tuple[str, tuple[object, ...]]], errors):
    """Run `main` with every runner recorded rather than composed."""

    def record(name):
        def runner(*arguments):
            calls.append((name, arguments))
            return {}

        return runner

    return main(
        argv,
        review_serve_runner=record("review-serve"),
        policy_preview_runner=record("policy-preview"),
        cover_review_runner=record("cover-review"),
        review_activity_runner=record("review-activity"),
        provider_recovery_runner=record("provider-recovery"),
        qualification_state_bridge_runner=record("qualification-state-bridge"),
        stdout=io.StringIO(),
        stderr=errors,
    )


@pytest.mark.parametrize(("command", "omitted"), _OMISSIONS, ids=_OMISSION_IDS)
def test_every_required_argument_is_refused_when_omitted(
    command: str, omitted: str
) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []
    errors = io.StringIO()

    assert 2 == _invoke(_argv(command, omit=omitted), calls, errors)
    assert "Review server failed: invalid invocation.\n" == errors.getvalue()
    assert [] == calls


@pytest.mark.parametrize("command", tuple(_DELEGATED), ids=tuple(_DELEGATED))
def test_supplied_arguments_reach_the_runner_with_their_parsed_types(
    command: str,
) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    assert 0 == _invoke(_argv(command), calls, io.StringIO())
    # Equality carries the type: `Path("/artifacts") != "/artifacts"`, so a
    # declaration that stopped converting delivers a string and fails here.
    assert [(command, _DELEGATED[command])] == calls


def test_explicit_port_reaches_the_runner_as_an_integer() -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    assert 0 == _invoke(
        [*_argv("review-serve"), "--port", "65535"], calls, io.StringIO()
    )
    assert (65535,) == calls[0][1][5:]


def test_host_outside_the_closed_choice_set_is_refused() -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []
    errors = io.StringIO()

    argv = [*_argv("review-serve"), "--host", "10.0.0.1"]
    assert 2 == _invoke(argv, calls, errors)
    assert "Review server failed: invalid invocation.\n" == errors.getvalue()
    assert [] == calls


# Named rather than left to pytest's generated ids. The tier runner reduces a
# parameter set to `[A-Za-z0-9_]` to keep a case identity path-free, and under
# that reduction `"+8080"` and `" 8080"` both become `_8080` -- one identity for
# two cases, which the cohort's collision guard refuses outright.
@pytest.mark.parametrize(
    "value",
    (
        "0",
        "-1",
        "65536",
        "99999999999999999999",
        "+8080",
        " 8080",
        "08080",
        "0x1f90",
        "",
        "8080\n",
    ),
    ids=(
        "below-range",
        "negative",
        "above-range",
        "far-above-range",
        "signed",
        "leading-space",
        "leading-zero",
        "hexadecimal",
        "empty",
        "trailing-newline",
    ),
)
def test_port_refuses_every_noncanonical_value(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="invalid port"):
        _port(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    (("1", 1), ("8080", 8080), ("65535", 65535)),
    ids=("lower-bound", "typical", "upper-bound"),
)
def test_port_accepts_the_closed_range_it_declares(value: str, expected: int) -> None:
    assert expected == _port(value)
