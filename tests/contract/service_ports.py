"""Reusable structural assertions for service-adapter implementations.

Future concrete adapters can subclass or call this harness with synthetic
instances.  It intentionally checks the application-owned request boundary;
vendor payload behavior remains in family-specific suites.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, get_type_hints
import unittest


@dataclass(frozen=True)
class MethodContract:
    name: str
    parameter_names: tuple[str, ...]
    parameter_kinds: tuple[inspect._ParameterKind, ...]
    parameters: tuple[object, ...]
    defaults: tuple[object, ...]
    returns: object


@dataclass(frozen=True)
class FamilyContract:
    name: str
    methods: tuple[MethodContract, ...]
    forbidden_methods: tuple[str, ...] = ()


def standard_family_contracts() -> tuple[FamilyContract, ...]:
    """Return the unchanged base contracts every future adapter must run."""

    from mechanical_librarian.application.audit import AuditRepository
    from mechanical_librarian.application.job_heartbeat import JobHeartbeatRepository
    from mechanical_librarian.application.ports import (
        AdapterDiagnostics, BackupReadiness, CoverProvider, EvidenceExtractor, LibraryReader,
        MetadataProvider, Notifier, Reviewer, SecretResolver, SnapshotRepository,
        SupportedWriter, Transaction, TransactionManager, WorkScheduler,
    )

    def method(owner: type[object], name: str) -> MethodContract:
        member = owner.__dict__[name]
        signature = inspect.signature(member)
        parameters = tuple(signature.parameters.values())[1:]
        hints = get_type_hints(member)
        return MethodContract(
            name,
            tuple(item.name for item in parameters),
            tuple(item.kind for item in parameters),
            tuple(hints.get(item.name) for item in parameters),
            tuple(item.default for item in parameters),
            hints.get("return"),
        )

    heartbeat_methods = tuple(method(JobHeartbeatRepository, name) for name in JobHeartbeatRepository.__dict__ if not name.startswith("_") and callable(JobHeartbeatRepository.__dict__[name]))
    return (
        FamilyContract("repository", tuple(method(SnapshotRepository, name) for name in ("store", "read", "list")), ("apply", "resolve", "lookup")),
        FamilyContract("transaction", (method(TransactionManager, "begin"), method(Transaction, "commit"), method(Transaction, "rollback")), ("lookup", "resolve")),
        FamilyContract("durable-scheduler", (method(WorkScheduler, "schedule"),) + heartbeat_methods, ("apply", "resolve")),
        FamilyContract("diagnostics", tuple(method(AdapterDiagnostics, name) for name in ("capabilities", "compatibility", "health")), ("apply", "resolve")),
        FamilyContract("library-reader", tuple(method(LibraryReader, name) for name in ("discover_capabilities", "list_snapshots", "read_snapshot")), ("apply", "resolve")),
        FamilyContract("supported-writer", (method(SupportedWriter, "apply"),), ("lookup", "resolve")),
        FamilyContract("extractor", (method(EvidenceExtractor, "extract"),), ("apply", "lookup", "resolve")),
        FamilyContract("metadata-provider", (method(MetadataProvider, "lookup"),), ("apply", "resolve", "extract")),
        FamilyContract("cover-provider", (method(CoverProvider, "lookup_covers"),), ("apply", "resolve", "extract")),
        FamilyContract("reviewer", (method(Reviewer, "review"),), ("apply", "resolve", "lookup")),
        FamilyContract("secret-resolver", tuple(method(SecretResolver, name) for name in ("configured", "resolve")), ("apply", "lookup")),
        FamilyContract("backup", (method(BackupReadiness, "check"),), ("apply", "resolve")),
        FamilyContract("audit", tuple(method(AuditRepository, name) for name in ("append", "read", "recent")), ("apply", "resolve")),
        FamilyContract("notifier", (method(Notifier, "notify"),), ("apply", "resolve")),
    )


class ServicePortContract:
    def __init__(self, case: unittest.TestCase) -> None:
        self.case = case

    def deny_methods(self, adapter: object, *method_names: str) -> None:
        for method_name in method_names:
            self.case.assertFalse(
                callable(getattr(adapter, method_name, None)),
                f"{type(adapter).__name__} must not expose {method_name}",
            )

    def require_method_contract(self, adapter: object, contract: MethodContract) -> None:
        owned = type(adapter).__dict__.get(contract.name)
        self.case.assertTrue(callable(owned), f"{contract.name} must be concretely implemented by {type(adapter).__name__}")
        method = getattr(adapter, contract.name)
        parameters = tuple(inspect.signature(method).parameters.values())
        hints = get_type_hints(method)
        self.case.assertEqual(tuple(item.name for item in parameters), contract.parameter_names, f"{contract.name} parameter names mismatch")
        self.case.assertEqual(tuple(item.kind for item in parameters), contract.parameter_kinds, f"{contract.name} parameter kinds mismatch")
        self.case.assertEqual(tuple(hints.get(item.name) for item in parameters), contract.parameters, f"{contract.name} annotations mismatch")
        self.case.assertEqual(tuple(item.default for item in parameters), contract.defaults, f"{contract.name} defaults mismatch")
        self.case.assertEqual(hints.get("return"), contract.returns, f"{contract.name} return annotation mismatch")

    def require_family(self, adapter: object, contract: FamilyContract) -> None:
        for method in contract.methods:
            self.require_method_contract(adapter, method)
        self.deny_methods(adapter, *contract.forbidden_methods)

    def reject_family(self, adapter: object, contract: FamilyContract) -> None:
        with self.case.assertRaises(AssertionError, msg=f"{contract.name} nonconformer was accepted"):
            self.require_family(adapter, contract)

    def require_result_value(self, result: object, value_type: type[Any]) -> Any:
        from mechanical_librarian.application.ports import PortResult

        self.case.assertIs(type(result), PortResult, "adapter must return an exact PortResult")
        self.case.assertIsNone(result.failure)
        self.case.assertIs(type(result.value), value_type, "adapter returned the wrong value type")
        return result.value

    def require_unit_result(self, result: object) -> None:
        from mechanical_librarian.application.ports import Unit

        self.require_result_value(result, Unit)

    def require_failure_result(self, result: object, kind: object) -> object:
        from mechanical_librarian.application.ports import PortFailure, PortResult

        self.case.assertIs(type(result), PortResult)
        self.case.assertIsNone(result.value)
        self.case.assertIs(type(result.failure), PortFailure)
        self.case.assertIs(result.failure.kind, kind)
        return result.failure

    def require_page_result(
        self,
        result: object,
        item_type: type[Any],
        request: object,
    ) -> object:
        from mechanical_librarian.application.ports import Page, PageRequest

        self.case.assertIs(type(request), PageRequest)
        page = self.require_result_value(result, Page)
        self.case.assertLessEqual(len(page.items), request.limit)
        self.case.assertTrue(all(type(item) is item_type for item in page.items))
        return page
