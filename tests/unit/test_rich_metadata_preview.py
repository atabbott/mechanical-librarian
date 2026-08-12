"""Direct contract proof for the no-effect rich metadata preview compiler."""

from __future__ import annotations

import unittest

from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile, resolve_policy
from mechanical_librarian.domain.metadata_report import (
    ClaimKind, ClaimScope, LocalClaim, MetadataReport, ProviderEvidence,
    ProviderEvidenceKind, TargetFacts, TargetField, TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    BibliographicCombination, BibliographicSelection,
    ProposalAlternative, ProposalAssessment, ProposalOutcome, ProposalResult,
    ProposalScope, ProposalSet, ProposalValue, ProposalValueError,
    ProposalValueKind, evaluate_report,
)
from mechanical_librarian.domain.metadata_report import TargetDestination, TargetSeries
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError, compile_rich_metadata_preview,
)
from tests.unit.test_proposal_set import _claim, _fields, _provider, _report
from tests.unit.test_bibliographic_bundles import _bibliographic_report, _source


class RichMetadataPreviewTests(unittest.TestCase):
    @staticmethod
    def _local_isbn(value, checksum):
        return LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed=value,
            canonical=value,
            excerpt=value,
            applicability="ebook",
            checksum=checksum,
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )

    def _preview_field(self, report, field, option):
        options = dict(MetadataPolicyProfile.builtin().options)
        options[field] = option
        profile = MetadataPolicyProfile.create(
            "Exact field preview",
            options,
            dict(MetadataPolicyProfile.builtin().destinations),
        )
        policy = resolve_policy(profile, report.target.library)
        proposal = evaluate_report(
            report, policy.resolved_id, dict(policy.destinations)
        )
        return next(
            value
            for value in compile_rich_metadata_preview(
                policy, report, proposal
            ).fields
            if value.field == field
        )

    def _with_state(self, report, destination, state):
        return MetadataReport.create(
            target=TargetFacts(report.target.connection, report.target.library, report.target.item, tuple(TargetField(field.destination, state) if field.destination is destination else field for field in report.target.fields)),
            file=report.file, local_claims=report.local_claims,
            local_reasons=report.local_reasons, providers=report.providers,
            cover_sources=report.cover_sources,
        )

    def _list_preview(self, field, option, current, alternatives=1):
        from mechanical_librarian.domain.metadata_report import TargetDestination
        destination = TargetDestination[field.upper()]
        source = _report()
        state = TargetFieldState.MISSING if current is None else TargetFieldState.VALUE
        report = MetadataReport.create(
            target=TargetFacts(source.target.connection, source.target.library, source.target.item, tuple(TargetField(value.destination, state, current) if value.destination is destination else value for value in source.target.fields)), file=source.file, local_claims=source.local_claims, local_reasons=source.local_reasons, providers=source.providers, cover_sources=source.cover_sources,
        )
        profile_options = dict(MetadataPolicyProfile.builtin().options); profile_options[field] = option
        profile = MetadataPolicyProfile.create("Lists", profile_options, dict(MetadataPolicyProfile.builtin().destinations))
        policy = resolve_policy(profile, report.target.library)
        base = evaluate_report(report)
        values = (("Fantasy", "Epic"), ("Mystery",))[:alternatives]
        alts = tuple(ProposalAlternative.create(ProposalValue(ProposalValueKind.LIST, value), ProposalAssessment.FILLS_EMPTY if current is None else ProposalAssessment.REPLACES_TARGET, ("provider_" + chr(97 + index) * 64,), ("correlation_" + chr(97 + index) * 64,), ProposalScope.WORK) for index, value in enumerate(values))
        ordered = tuple(sorted(alts, key=lambda value: value.alternative_id))
        result = ProposalResult(destination, ProposalOutcome.PROPOSE_CHANGE if alternatives == 1 else ProposalOutcome.UNRESOLVED_CHOICE, ordered, ordered[0].alternative_id if alternatives == 1 else None, ("unique_typed_candidate",) if alternatives == 1 else ("competing_evidence",), next(value.dependencies for value in base.results if value.destination is destination))
        proposal = ProposalSet.create(report_id=base.report_id, target_fingerprint=base.target_fingerprint, results=tuple(result if value.destination is destination else value for value in base.results), resolved_policy_id=policy.resolved_id, bibliographic_combinations=base.bibliographic_combinations)
        return next(value for value in compile_rich_metadata_preview(policy, report, proposal).fields if value.field == field)

    def test_exact_pair_compiles_all_eight_fields_in_closed_order(self) -> None:
        report = _report()
        proposal = evaluate_report(report)
        policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
        preview = compile_rich_metadata_preview(policy, report, proposal)
        self.assertEqual(
            ("subtitle", "series", "genres", "tags", "description_fill", "isbn_form", "asin", "explicit"),
            tuple(field.field for field in preview.fields),
        )
        self.assertEqual("policy_off", preview.fields[3].reason)
        self.assertEqual("explicit_unsupported", preview.fields[-1].reason)

    def test_binding_mismatch_fails_closed(self) -> None:
        report = _report()
        proposal = evaluate_report(report)
        policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
        with self.assertRaises(RichMetadataPreviewError):
            compile_rich_metadata_preview(
                resolve_policy(MetadataPolicyProfile.builtin(), "other"), report, proposal
            )

    def test_rich_automatic_and_ignore_are_typed_abstentions(self) -> None:
        report = _report()
        proposal = evaluate_report(report)
        options = dict(MetadataPolicyProfile.builtin().options)
        destinations = dict(MetadataPolicyProfile.builtin().destinations)
        destinations["genres"] = "automatic"
        destinations["tags"] = "ignore"
        profile = MetadataPolicyProfile.create("Manual rich preview", options, destinations)
        policy = resolve_policy(profile, report.target.library)
        # Rebind only in this direct pure-module proof; evidence/results remain intact.
        rebound = proposal.create(report_id=proposal.report_id, target_fingerprint=proposal.target_fingerprint, results=proposal.results, resolved_policy_id=policy.resolved_id, bibliographic_combinations=proposal.bibliographic_combinations)
        fields = {field.field: field for field in compile_rich_metadata_preview(policy, report, rebound).fields}
        self.assertEqual("automatic_unqualified", fields["genres"].reason)
        self.assertEqual("policy_ignored", fields["tags"].reason)

    def test_every_closed_rich_option_is_stably_exposed(self) -> None:
        report = _report()
        base = MetadataPolicyProfile.builtin()
        for field, options in {
            "subtitle": ("fill_empty", "corroborated_split"),
            "series": ("primary_narrative", "source_order", "all_qualified"),
            "genres": ("fill_empty", "add", "replace"),
            "tags": ("off", "fill_empty", "add", "replace"),
            "description_fill": ("safe_plain_text", "google_visible_text"),
            "isbn_form": ("preserve_exact", "prefer_isbn13"),
            "asin": ("preserve_exact", "ebook_manifestation"),
            "explicit": ("unsupported",),
        }.items():
            for option in options:
                choices = dict(base.options); choices[field] = option
                profile = MetadataPolicyProfile.create("Matrix", choices, dict(base.destinations))
                policy = resolve_policy(profile, report.target.library)
                proposal = evaluate_report(report)
                rebound = proposal.create(report_id=proposal.report_id, target_fingerprint=proposal.target_fingerprint, results=proposal.results, resolved_policy_id=policy.resolved_id, bibliographic_combinations=proposal.bibliographic_combinations)
                preview = compile_rich_metadata_preview(policy, report, rebound)
                rendered = preview.to_data()
                self.assertEqual(rendered, preview.to_data())
                self.assertEqual(option, next(value.option for value in preview.fields if value.field == field))

    def test_unusable_target_states_abstain_before_candidate_interpretation(self) -> None:
        base = _report()
        destinations = {
            "subtitle": "SUBTITLE", "series": "SERIES", "genres": "GENRES",
            "tags": "TAGS", "description_fill": "DESCRIPTION", "isbn_form": "ISBN", "asin": "ASIN",
        }
        from mechanical_librarian.domain.metadata_report import TargetDestination
        for state in (TargetFieldState.UNLOADED, TargetFieldState.MALFORMED, TargetFieldState.OVERSIZED):
            for field, destination_name in destinations.items():
                report = self._with_state(base, TargetDestination[destination_name], state)
                policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
                proposal = evaluate_report(report)
                rebound = proposal.create(report_id=proposal.report_id, target_fingerprint=proposal.target_fingerprint, results=proposal.results, resolved_policy_id=policy.resolved_id, bibliographic_combinations=proposal.bibliographic_combinations)
                preview = compile_rich_metadata_preview(policy, report, rebound)
                selected = next(value for value in preview.fields if value.field == field)
                self.assertEqual("target_state_unusable", selected.reason)
                self.assertEqual("abstain", selected.outcome)

    def test_destination_precedence_is_typed_and_never_projects(self) -> None:
        report = _report()
        base = MetadataPolicyProfile.builtin()
        for field, destination, reason in (("genres", "ignore", "policy_ignored"), ("genres", "automatic", "automatic_unqualified"), ("tags", "ask", "policy_off")):
            choices = dict(base.destinations); choices[field] = destination
            profile = MetadataPolicyProfile.create("Destination", dict(base.options), choices)
            policy = resolve_policy(profile, report.target.library)
            proposal = evaluate_report(report)
            rebound = proposal.create(report_id=proposal.report_id, target_fingerprint=proposal.target_fingerprint, results=proposal.results, resolved_policy_id=policy.resolved_id, bibliographic_combinations=proposal.bibliographic_combinations)
            selected = next(value for value in compile_rich_metadata_preview(policy, report, rebound).fields if value.field == field)
            self.assertEqual(reason, selected.reason)
            self.assertEqual("abstain", selected.outcome)

    def test_tags_off_precedes_absent_or_ambiguous_candidates(self) -> None:
        report = _report()
        policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
        proposal = evaluate_report(report)
        rebound = proposal.create(report_id=proposal.report_id, target_fingerprint=proposal.target_fingerprint, results=proposal.results, resolved_policy_id=policy.resolved_id, bibliographic_combinations=proposal.bibliographic_combinations)
        tag = next(value for value in compile_rich_metadata_preview(policy, report, rebound).fields if value.field == "tags")
        self.assertEqual(("policy_off", "abstain", None), (tag.reason, tag.outcome, tag.desired_value))

    def test_list_fill_add_replace_and_no_ops_preserve_exact_order(self) -> None:
        self.assertEqual(("projected", ("Fantasy", "Epic")), (self._list_preview("genres", "fill_empty", None).outcome, self._list_preview("genres", "fill_empty", None).desired_value))
        filled = self._list_preview("genres", "fill_empty", ("Existing",))
        self.assertEqual(("no_op", ("Existing",), "target_preserved"), (filled.outcome, filled.desired_value, filled.reason))
        added = self._list_preview("genres", "add", ("Epic", "Existing"))
        self.assertEqual(("Epic", "Existing", "Fantasy"), added.desired_value)
        replaced = self._list_preview("tags", "replace", ("Existing",))
        self.assertEqual(("Fantasy", "Epic"), replaced.desired_value)
        unchanged = self._list_preview("tags", "add", ("Fantasy", "Epic"))
        self.assertEqual(("no_op", "unchanged"), (unchanged.outcome, unchanged.reason))

    def test_populated_list_modes_use_real_unresolved_evaluator_candidate(self) -> None:
        genres = TargetField(
            TargetDestination.GENRES,
            TargetFieldState.VALUE,
            ("Fantasy",),
        )
        claim = _claim(
            "catalogue", "edition-1", ClaimKind.GENRE, "Mystery",
            scope=ClaimScope.WORK,
        )
        report = _report(
            fields=_fields(genres=genres),
            providers=(_provider("catalogue", "edition-1", (claim,)),),
        )
        for option, expected in (
            ("add", ("Fantasy", "Mystery")),
            ("replace", ("Mystery",)),
        ):
            options = dict(MetadataPolicyProfile.builtin().options)
            options["genres"] = option
            policy = resolve_policy(
                MetadataPolicyProfile.create(
                    "Populated list", options,
                    dict(MetadataPolicyProfile.builtin().destinations),
                ),
                report.target.library,
            )
            proposal = evaluate_report(
                report, policy.resolved_id, dict(policy.destinations)
            )
            result = next(
                value for value in proposal.results
                if value.destination is TargetDestination.GENRES
            )
            self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
            field = next(
                value for value in compile_rich_metadata_preview(
                    policy, report, proposal
                ).fields
                if value.field == "genres"
            )
            self.assertEqual(("projected", expected), (field.outcome, field.desired_value))

    def test_list_ambiguity_never_synthesizes_separate_alternatives(self) -> None:
        value = self._list_preview("genres", "replace", None, alternatives=2)
        self.assertEqual(("abstain", "ambiguous_evidence", None), (value.outcome, value.reason, value.desired_value))

    def test_corroborated_split_uses_real_admitted_candidate_members(self) -> None:
        report = _bibliographic_report(current_title="Secret History: A Mistborn Novella", providers=(_source("catalogue", "edition-1", titles=("Secret History",), subtitle="A Mistborn Novella"),))
        base = evaluate_report(report)
        self.assertTrue(base.bibliographic_combinations)
        options = dict(MetadataPolicyProfile.builtin().options); options["subtitle"] = "corroborated_split"
        policy = resolve_policy(MetadataPolicyProfile.create("Split", options, dict(MetadataPolicyProfile.builtin().destinations)), report.target.library)
        proposal = base.create(report_id=base.report_id, target_fingerprint=base.target_fingerprint, results=base.results, resolved_policy_id=policy.resolved_id, bibliographic_combinations=base.bibliographic_combinations)
        selected = next(value for value in compile_rich_metadata_preview(policy, report, proposal).fields if value.field == "subtitle")
        self.assertEqual({"title": "Secret History", "subtitle": "A Mistborn Novella"}, selected.desired_value)

    def test_corroborated_split_rejects_unbounded_or_absent_separator(self) -> None:
        report = _bibliographic_report(
            current_title="Secret History / A Mistborn Novella",
            providers=(_source(
                "catalogue", "edition-1", titles=("Secret History",),
                subtitle="A Mistborn Novella",
            ),),
        )
        options = dict(MetadataPolicyProfile.builtin().options)
        options["subtitle"] = "corroborated_split"
        policy = resolve_policy(
            MetadataPolicyProfile.create(
                "Bounded split", options,
                dict(MetadataPolicyProfile.builtin().destinations),
            ),
            report.target.library,
        )
        proposal = evaluate_report(
            report, policy.resolved_id, dict(policy.destinations)
        )
        selected = next(
            value for value in compile_rich_metadata_preview(
                policy, report, proposal
            ).fields
            if value.field == "subtitle"
        )
        self.assertEqual(
            ("abstain", "corroborated_split_unavailable", None),
            (selected.outcome, selected.reason, selected.desired_value),
        )

    def test_series_options_are_closed_and_primary_without_combination_abstains(self) -> None:
        report = _report()
        base = evaluate_report(report)
        for option in ("primary_narrative", "source_order", "all_qualified"):
            options = dict(MetadataPolicyProfile.builtin().options); options["series"] = option
            policy = resolve_policy(MetadataPolicyProfile.create("Series", options, dict(MetadataPolicyProfile.builtin().destinations)), report.target.library)
            proposal = base.create(report_id=base.report_id, target_fingerprint=base.target_fingerprint, results=base.results, resolved_policy_id=policy.resolved_id, bibliographic_combinations=base.bibliographic_combinations)
            field = next(value for value in compile_rich_metadata_preview(policy, report, proposal).fields if value.field == "series")
            if option == "primary_narrative" and not base.bibliographic_combinations:
                self.assertEqual("no_candidate", field.reason)
            self.assertEqual(option, field.option)

    def test_series_modes_use_one_admitted_tuple_and_never_synthesize(self) -> None:
        report = _report()
        base = evaluate_report(report)
        targets = {value.destination: value for value in report.target.fields}
        alt = ProposalAlternative.create(ProposalValue(ProposalValueKind.SERIES, (TargetSeries("Reading Order", "2"), TargetSeries("Publication Order", None))), ProposalAssessment.FILLS_EMPTY, ("provider_" + "a" * 64,), ("correlation_" + "a" * 64,), ProposalScope.WORK)
        result = ProposalResult(TargetDestination.SERIES, ProposalOutcome.PROPOSE_CHANGE, (alt,), alt.alternative_id, ("unique_typed_candidate",), next(value.dependencies for value in base.results if value.destination is TargetDestination.SERIES))
        combo = BibliographicCombination.create((BibliographicSelection.preserve(targets[TargetDestination.TITLE]), BibliographicSelection.preserve(targets[TargetDestination.SUBTITLE]), BibliographicSelection.candidate(TargetDestination.SERIES, alt.alternative_id)), ("representation_" + "a" * 64,))
        for option in ("primary_narrative", "source_order", "all_qualified"):
            options = dict(MetadataPolicyProfile.builtin().options); options["series"] = option
            policy = resolve_policy(MetadataPolicyProfile.create("Series fixture", options, dict(MetadataPolicyProfile.builtin().destinations)), report.target.library)
            proposal = ProposalSet.create(report_id=base.report_id, target_fingerprint=base.target_fingerprint, results=tuple(result if value.destination is TargetDestination.SERIES else value for value in base.results), resolved_policy_id=policy.resolved_id, bibliographic_combinations=(combo,))
            field = next(value for value in compile_rich_metadata_preview(policy, report, proposal).fields if value.field == "series")
            self.assertEqual(({"name": "Reading Order", "sequence": "2"},), field.desired_value)
            if option == "all_qualified":
                self.assertTrue(field.narrowed_for_target)
                self.assertEqual(({"name": "Reading Order", "sequence": "2"}, {"name": "Publication Order", "sequence": None}), field.qualified_value)

    def test_preserving_single_series_is_a_no_op_for_source_order_and_all_qualified(self) -> None:
        base = _report()
        current = (TargetSeries("Exact Provider Spelling", "2"),)
        report = MetadataReport.create(
            target=TargetFacts(base.target.connection, base.target.library, base.target.item, tuple(
                TargetField(value.destination, TargetFieldState.VALUE, current)
                if value.destination is TargetDestination.SERIES else value
                for value in base.target.fields
            )), file=base.file, local_claims=base.local_claims, local_reasons=base.local_reasons,
            providers=base.providers, cover_sources=base.cover_sources,
        )
        base_proposal = evaluate_report(report)
        alternative = ProposalAlternative.create(ProposalValue(ProposalValueKind.SERIES, current), ProposalAssessment.PRESERVES_TARGET, ("provider_" + "a" * 64,), ("correlation_" + "a" * 64,), ProposalScope.WORK)
        result = ProposalResult(TargetDestination.SERIES, ProposalOutcome.KEEP, (alternative,), None, ("positive_preservation_evidence",), next(value.dependencies for value in base_proposal.results if value.destination is TargetDestination.SERIES))
        for option in ("source_order", "all_qualified"):
            options = dict(MetadataPolicyProfile.builtin().options); options["series"] = option
            policy = resolve_policy(MetadataPolicyProfile.create("Keep", options, dict(MetadataPolicyProfile.builtin().destinations)), report.target.library)
            proposal = ProposalSet.create(report_id=base_proposal.report_id, target_fingerprint=base_proposal.target_fingerprint, results=tuple(result if value.destination is TargetDestination.SERIES else value for value in base_proposal.results), resolved_policy_id=policy.resolved_id, bibliographic_combinations=base_proposal.bibliographic_combinations)
            field = next(value for value in compile_rich_metadata_preview(policy, report, proposal).fields if value.field == "series")
            self.assertEqual(("no_op", "unchanged", False), (field.outcome, field.reason, field.narrowed_for_target))

    def test_keep_series_requires_one_preserving_membership(self) -> None:
        alternative = ProposalAlternative.create(
            ProposalValue(
                ProposalValueKind.SERIES, (TargetSeries("Not preserving", None),)
            ),
            ProposalAssessment.FILLS_EMPTY,
            ("provider_" + "a" * 64,),
            ("correlation_" + "a" * 64,),
            ProposalScope.WORK,
        )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.SERIES,
                ProposalOutcome.KEEP,
                (alternative,),
                None,
                ("positive_preservation_evidence",),
            )

    def test_description_options_use_exact_admitted_text_and_google_evidence(self) -> None:
        transformed = _claim(
            "google", "edition-1", ClaimKind.DESCRIPTION, "2 < 3",
            scope=ClaimScope.WORK,
        )
        google = _report(providers=(_provider(
            "google", "edition-1", (transformed,),
            (ProviderEvidence(
                ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                "google-books-html-visible-text-v1", ClaimScope.WORK,
            ),),
            authority="google_books",
        ),))
        visible = self._preview_field(
            google, "description_fill", "google_visible_text"
        )
        self.assertEqual(("projected", "2 < 3", None), (
            visible.outcome, visible.desired_value, visible.reason,
        ))

        safe = _claim(
            "catalogue", "edition-1", ClaimKind.DESCRIPTION,
            "Bounded plain description", scope=ClaimScope.WORK,
        )
        ordinary = _report(providers=(_provider(
            "catalogue", "edition-1", (safe,)
        ),))
        plain = self._preview_field(
            ordinary, "description_fill", "safe_plain_text"
        )
        unavailable = self._preview_field(
            ordinary, "description_fill", "google_visible_text"
        )
        self.assertEqual("Bounded plain description", plain.desired_value)
        self.assertEqual(
            ("abstain", "option_evidence_unavailable"),
            (unavailable.outcome, unavailable.reason),
        )

        corroborated = _report(providers=(
            _provider(
                "google", "edition-1", (transformed,),
                (ProviderEvidence(
                    ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                    "google-books-html-visible-text-v1", ClaimScope.WORK,
                ),),
                authority="google_books",
            ),
            _provider(
                "open_library", "edition-2",
                (_claim(
                    "open_library", "edition-2", ClaimKind.DESCRIPTION,
                    "2 < 3", scope=ClaimScope.WORK,
                ),),
            ),
        ))
        mixed = self._preview_field(
            corroborated, "description_fill", "google_visible_text"
        )
        self.assertEqual(
            ("projected", "2 < 3", None),
            (mixed.outcome, mixed.desired_value, mixed.reason),
        )

    def test_google_description_requires_transform_and_authority(self) -> None:
        claim = _claim(
            "google", "edition-1", ClaimKind.DESCRIPTION, "Plain text",
            scope=ClaimScope.WORK,
        )
        for provider in (
            _provider(
                "google", "edition-1", (claim,), authority="google_books"
            ),
            _provider(
                "google", "edition-1", (claim,),
                (ProviderEvidence(
                    ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                    "google-books-html-visible-text-v1", ClaimScope.WORK,
                ),),
            ),
        ):
            field = self._preview_field(
                _report(providers=(provider,)),
                "description_fill", "google_visible_text",
            )
            self.assertEqual("option_evidence_unavailable", field.reason)

    def test_description_preserves_populated_and_abstains_on_ambiguity_or_absence(self) -> None:
        populated = _report(fields=_fields(description=TargetField(
            TargetDestination.DESCRIPTION,
            TargetFieldState.VALUE,
            "Owner description",
        )))
        preserved = self._preview_field(
            populated, "description_fill", "google_visible_text"
        )
        self.assertEqual(
            ("no_op", "Owner description", "target_preserved"),
            (preserved.outcome, preserved.desired_value, preserved.reason),
        )

        left = _claim(
            "alpha", "edition-1", ClaimKind.DESCRIPTION, "First",
            scope=ClaimScope.WORK,
        )
        right = _claim(
            "beta", "edition-2", ClaimKind.DESCRIPTION, "Second",
            scope=ClaimScope.WORK,
        )
        ambiguous = self._preview_field(
            _report(providers=(
                _provider("alpha", "edition-1", (left,)),
                _provider("beta", "edition-2", (right,)),
            )),
            "description_fill", "safe_plain_text",
        )
        absent = self._preview_field(
            _report(), "description_fill", "safe_plain_text"
        )
        self.assertEqual("ambiguous_evidence", ambiguous.reason)
        self.assertEqual("no_candidate", absent.reason)

    def test_isbn_options_preserve_or_select_only_exact_admitted_forms(self) -> None:
        populated = _report(fields=_fields(isbn=TargetField(
            TargetDestination.ISBN, TargetFieldState.VALUE, "0306406152"
        )))
        preserved = self._preview_field(
            populated, "isbn_form", "prefer_isbn13"
        )
        self.assertEqual(
            ("no_op", "0306406152", "target_preserved"),
            (preserved.outcome, preserved.desired_value, preserved.reason),
        )

        isbn10 = self._local_isbn("0306406152", "valid_isbn_10")
        ten_report = _report(
            fields=_fields(isbn=TargetField(
                TargetDestination.ISBN, TargetFieldState.MISSING
            )),
            local_claims=(isbn10,),
        )
        exact = self._preview_field(
            ten_report, "isbn_form", "preserve_exact"
        )
        unavailable = self._preview_field(
            ten_report, "isbn_form", "prefer_isbn13"
        )
        self.assertEqual("0306406152", exact.desired_value)
        self.assertEqual(
            ("abstain", "option_evidence_unavailable", None),
            (unavailable.outcome, unavailable.reason, unavailable.desired_value),
        )

        isbn13 = self._local_isbn("9780306406157", "valid_isbn_13")
        thirteen_report = _report(
            fields=_fields(isbn=TargetField(
                TargetDestination.ISBN, TargetFieldState.MISSING
            )),
            local_claims=(isbn13,),
        )
        preferred = self._preview_field(
            thirteen_report, "isbn_form", "prefer_isbn13"
        )
        self.assertEqual("9780306406157", preferred.desired_value)

    def test_isbn13_preference_abstains_on_two_exact_admitted_forms(self) -> None:
        report = _report(
            fields=_fields(isbn=TargetField(
                TargetDestination.ISBN, TargetFieldState.MISSING
            )),
            local_claims=(
                self._local_isbn("9780140328721", "valid_isbn_13"),
                self._local_isbn("9780306406157", "valid_isbn_13"),
            ),
        )
        field = self._preview_field(
            report, "isbn_form", "prefer_isbn13"
        )
        self.assertEqual(
            ("abstain", "ambiguous_evidence", None),
            (field.outcome, field.reason, field.desired_value),
        )

    def test_asin_options_preserve_exact_text_and_require_ebook_manifestation(self) -> None:
        populated = _report(fields=_fields(asin=TargetField(
            TargetDestination.ASIN, TargetFieldState.VALUE, "B012345678"
        )))
        preserved = self._preview_field(
            populated, "asin", "ebook_manifestation"
        )
        self.assertEqual(
            ("no_op", "B012345678", "target_preserved"),
            (preserved.outcome, preserved.desired_value, preserved.reason),
        )

        ebook = _claim(
            "catalogue", "edition-1", ClaimKind.ASIN, "B012345678",
            scope=ClaimScope.EDITION, applicability="ebook",
        )
        report = _report(
            fields=_fields(asin=TargetField(
                TargetDestination.ASIN, TargetFieldState.MISSING
            )),
            providers=(_provider("catalogue", "edition-1", (ebook,)),),
        )
        exact = self._preview_field(report, "asin", "preserve_exact")
        manifestation = self._preview_field(
            report, "asin", "ebook_manifestation"
        )
        self.assertEqual("B012345678", exact.desired_value)
        self.assertEqual("B012345678", manifestation.desired_value)

        unknown = _claim(
            "catalogue", "edition-2", ClaimKind.ASIN, "B087654321",
            scope=ClaimScope.EDITION, applicability="unknown",
        )
        hostile_report = _report(
            fields=_fields(asin=TargetField(
                TargetDestination.ASIN, TargetFieldState.MISSING
            )),
            providers=(_provider("catalogue", "edition-2", (unknown,)),),
        )
        options = dict(MetadataPolicyProfile.builtin().options)
        options["asin"] = "ebook_manifestation"
        policy = resolve_policy(
            MetadataPolicyProfile.create(
                "ASIN evidence gate", options,
                dict(MetadataPolicyProfile.builtin().destinations),
            ),
            hostile_report.target.library,
        )
        base = evaluate_report(
            hostile_report, policy.resolved_id, dict(policy.destinations)
        )
        alternative = ProposalAlternative.create(
            ProposalValue(ProposalValueKind.TEXT, "B087654321"),
            ProposalAssessment.FILLS_EMPTY,
            (unknown.reference,),
            ("correlation_" + "a" * 64,),
            ProposalScope.EDITION,
        )
        result = ProposalResult(
            TargetDestination.ASIN,
            ProposalOutcome.PROPOSE_CHANGE,
            (alternative,),
            alternative.alternative_id,
            ("unique_typed_candidate",),
            next(
                value.dependencies for value in base.results
                if value.destination is TargetDestination.ASIN
            ),
        )
        proposal = ProposalSet.create(
            report_id=base.report_id,
            target_fingerprint=base.target_fingerprint,
            results=tuple(
                result if value.destination is TargetDestination.ASIN else value
                for value in base.results
            ),
            resolved_policy_id=policy.resolved_id,
            bibliographic_combinations=base.bibliographic_combinations,
        )
        rejected = next(
            value for value in compile_rich_metadata_preview(
                policy, hostile_report, proposal
            ).fields
            if value.field == "asin"
        )
        self.assertEqual("option_evidence_unavailable", rejected.reason)
