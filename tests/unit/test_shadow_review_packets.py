from __future__ import annotations

import hashlib
import json
import unittest

from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.metadata_report import (
    MAX_CONTRIBUTIONS,
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    PublicationDateRelation,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.reviewer_decision_summary import (
    MAX_CREATOR_ROLE_FACTS,
    MAX_DECISION_CORRELATIONS,
)
from mechanical_librarian.domain.shadow_review import (
    MAX_LINKAGE_FORMAT_FACTS,
    MAX_LINKAGE_IDENTIFIER_FACTS,
    PACKET_SCHEMA,
    ShadowReviewPacket,
    ShadowReviewValueError,
    canonical_json,
)
from tests.unit.test_proposal_set import (
    _claim,
    _contradictory_publication_provider,
    _explicit_local_ebook_isbn,
    _identity_bound_alias_report,
    _partially_bound_direct_author_report,
    _provider,
    _publication_local_isbn,
    _publication_provider,
)
from tests.unit.test_proposal_set import _fields as _proposal_fields
from tests.unit.test_proposal_set import _report as _proposal_report


def _local_isbn() -> LocalClaim:
    return LocalClaim.create(kind=ClaimKind.ISBN, observed='9780061120084', canonical='9780061120084', excerpt='9780061120084', applicability='unknown', checksum='valid_isbn_13', applicability_basis='unlabelled', scope=ClaimScope.EDITION)

class ShadowReviewPacketTests(unittest.TestCase):

    def test_acceptable_language_choice_replays_with_all_raw_candidates(self) -> None:
        report = _proposal_report(fields=_proposal_fields(language=TargetField(TargetDestination.LANGUAGE, TargetFieldState.MISSING)), providers=(_provider('alpha', 'alpha-edition', (_claim('alpha', 'alpha-edition', ClaimKind.LANGUAGE, 'en', scope=ClaimScope.EDITION),)), _provider('beta', 'beta-edition', (_claim('beta', 'beta-edition', ClaimKind.LANGUAGE, 'eng', scope=ClaimScope.EDITION),))))
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        language = next((value for value in packet.proposals if value['destination'] == 'language'))
        self.assertEqual('acceptable_choice', language['outcome'])
        self.assertEqual({'en', 'eng'}, {candidate['value']['value'] for candidate in language['candidates']})
        self.assertTrue(all((candidate['decision_summary']['support'] == {'independent_provider_correlations': 1, 'local_evidence': 0, 'target_only_preservation': 0} and candidate['decision_summary']['selected_manifestation_binding'] == 'unresolved' for candidate in language['candidates'])))
        self.assertEqual({'evidence_sufficiency': 'candidate', 'target_only_preservation': 0}, language['decision_summary'])
        self.assertEqual("shadow-review-packet-v6", PACKET_SCHEMA)
        self.assertEqual(packet, ShadowReviewPacket.from_json(packet.to_json(), report))
        prior = json.loads(packet.to_json())
        prior["schema_version"] = "shadow-review-packet-v5"
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(prior), report)

    def test_populated_competing_field_marks_target_only_preservation(self) -> None:

        def provider(name: str, authority: str, author: str) -> ProviderObservation:
            return _provider(name, f'{name}-edition', (_claim(name, f'{name}-edition', ClaimKind.CREATOR, author, scope=ClaimScope.WORK, role='author'),), authority=authority)
        cases = (('manual', ('Ada Author', 'Bea Author'), (provider('catalogue', 'hardcover', 'Ada Author'),), 'unresolved_choice', 'manual'), ('candidate', ('Old Author',), (provider('alpha', 'hardcover', 'New Author'), provider('beta', 'open_library', 'New Author')), 'propose_change', 'candidate'))
        for name, current, providers, outcome, sufficiency in cases:
            with self.subTest(name=name):
                report = _proposal_report(fields=_proposal_fields(authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, current)), providers=providers)
                proposal = evaluate_report(report)
                packet = ShadowReviewPacket.create(ReviewCase.create(proposal, report), proposal, report)
                authors = next((value for value in packet.proposals if value['destination'] == TargetDestination.AUTHORS.value))
                self.assertEqual(outcome, authors['outcome'])
                self.assertTrue(authors['candidates'])
                self.assertFalse(any((candidate['assessment'] == 'preserves_target' for candidate in authors['candidates'])))
                self.assertEqual({'evidence_sufficiency': sufficiency, 'target_only_preservation': 1}, authors['decision_summary'])
                self.assertEqual(packet, ShadowReviewPacket.from_json(packet.to_json(), report))

    def test_provider_cover_correlation_counts_as_independent_support(self) -> None:
        artifact = CoverArtifact.create(b'cover', b'preview', 'image/png', 10, 20)
        source = CoverSource(cover_source_id(CoverSourceKind.HARDCOVER, 'catalogue:cover'), CoverSourceKind.HARDCOVER, CoverSourceState.RETRIEVED, artifact, provenance=CoverProvenance('catalogue', CoverDesignation.WORK, 'catalogue-work'))
        report = _proposal_report(covers=(source,))
        proposal = evaluate_report(report)
        packet = ShadowReviewPacket.create(ReviewCase.create(proposal, report), proposal, report)
        cover = next((value for value in packet.proposals if value['destination'] == TargetDestination.COVER.value))
        self.assertEqual('propose_change', cover['outcome'])
        self.assertEqual(1, len(cover['candidates']))
        self.assertEqual({'independent_provider_correlations': 1, 'local_evidence': 0, 'target_only_preservation': 0}, cover['candidates'][0]['decision_summary']['support'])
        self.assertEqual(packet, ShadowReviewPacket.from_json(packet.to_json(), report))

    def test_shared_month_candidate_replays_in_shadow_packet(self) -> None:
        report = _proposal_report(fields=_proposal_fields(published_year=TargetField(TargetDestination.PUBLISHED_YEAR, TargetFieldState.MISSING), published_date=TargetField(TargetDestination.PUBLISHED_DATE, TargetFieldState.MISSING)), local_claims=(_publication_local_isbn(),), providers=(_publication_provider('alpha', 'alpha-edition', ('2001-02-03',), authority='hardcover'), _publication_provider('beta', 'beta-edition', ('2001-02-09',), authority='open_library')))
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        published_date = next((value for value in packet.proposals if value['destination'] == 'publishedDate'))
        self.assertEqual(1, len(published_date['candidates']))
        candidate = published_date['candidates'][0]
        self.assertEqual('2001-02', candidate['value']['value'])
        self.assertEqual('manifestation_publication', candidate['date_event'])
        self.assertEqual('month', candidate['date_precision'])
        self.assertEqual(2, len(candidate['correlations']))
        self.assertEqual({'independent_provider_correlations': 2, 'local_evidence': 0, 'target_only_preservation': 0}, candidate['decision_summary']['support'])
        self.assertEqual({'destination': 'publishedDate', 'event': 'manifestation_publication', 'logical_field': 'publication', 'paired_destination_consistency': 'consistent', 'precision': 'month', 'report_only': True, 'write_eligibility': 'unsupported'}, candidate['decision_summary']['publication'])
        self.assertEqual(packet, ShadowReviewPacket.from_json(packet.to_json(), report))
        mismatched = json.loads(packet.to_json())
        next((value for value in mismatched['proposals'] if value['destination'] == 'publishedDate'))['candidates'][0]['value']['value'] = '2001-02-03'
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket(mismatched['review_case_id'], tuple(mismatched['proposals']), tuple(mismatched['citations']), tuple(mismatched['correlations']), mismatched['schema_version'])
        forged = json.loads(packet.to_json())
        forged_published = next((value for value in forged['proposals'] if value['destination'] == 'publishedDate'))
        forged_published['candidates'][0]['decision_summary']['publication']['paired_destination_consistency'] = 'conflicting'
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(forged), report)

    def test_complete_authors_summarize_same_representation_role_exclusions(self) -> None:
        claims = (_claim('catalogue', 'edition-1', ClaimKind.CREATOR, 'Ada Author', scope=ClaimScope.WORK, role='author'), _claim('catalogue', 'edition-1', ClaimKind.CREATOR, 'Bea Author', scope=ClaimScope.WORK, role='author', order=1), _claim('catalogue', 'edition-1', ClaimKind.CONTRIBUTOR, 'Nia Narrator', scope=ClaimScope.EDITION, role='narrator', order=2), _claim('catalogue', 'edition-1', ClaimKind.CONTRIBUTOR, 'Ed Editor', scope=ClaimScope.WORK, role='editor', order=3))
        report = _proposal_report(title=None, fields=_proposal_fields(None, authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)), providers=(_provider('catalogue', 'edition-1', claims),))
        proposal = evaluate_report(report)
        packet = ShadowReviewPacket.create(ReviewCase.create(proposal, report), proposal, report)
        authors = next((value for value in packet.proposals if value['destination'] == 'authors'))
        summary = authors['candidates'][0]['decision_summary']
        self.assertEqual({claim.reference for claim in claims if claim.role == 'author'}, {fact['reference'] for fact in summary['creator_roles']['included']})
        self.assertEqual({'editor', 'narrator'}, {fact['role'] for fact in summary['creator_roles']['excluded']})
        self.assertEqual({'independent_provider_correlations': 1, 'local_evidence': 0, 'target_only_preservation': 0}, summary['support'])
        citation_ids = {citation['citation_id'] for citation in packet.citations}
        self.assertTrue({fact['reference'] for fact in summary['creator_roles']['excluded']}.issubset(citation_ids))
        forged = json.loads(packet.to_json())
        forged_authors = next((value for value in forged['proposals'] if value['destination'] == 'authors'))
        forged_authors['candidates'][0]['decision_summary']['support']['independent_provider_correlations'] = True
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(forged), report)
        forged = json.loads(packet.to_json())
        forged_authors = next((value for value in forged['proposals'] if value['destination'] == 'authors'))
        role_fact = forged_authors['candidates'][0]['decision_summary']['creator_roles']['included'][0]
        forged_authors['candidates'][0]['decision_summary']['creator_roles']['included'] = [role_fact] * (MAX_CREATOR_ROLE_FACTS + 1)
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(forged), report)
        forged = json.loads(packet.to_json())
        forged_authors = next((value for value in forged['proposals'] if value['destination'] == 'authors'))
        forged_authors['candidates'][0]['decision_summary']['selected_manifestation_binding'] = 'established'
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(forged), report)

    def test_three_complete_author_representations_retain_all_role_facts(self) -> None:
        author_values = tuple((f'Author {index:02d}' for index in range(MAX_CONTRIBUTIONS)))
        providers = tuple((_provider(provider, f'{provider}-edition', tuple((_claim(provider, f'{provider}-edition', ClaimKind.CREATOR, author, scope=ClaimScope.WORK, role='author', order=index) for index, author in enumerate(author_values))), authority=authority) for provider, authority in (('alpha', 'hardcover'), ('beta', 'open_library'), ('gamma', 'google_books'))))
        report = _proposal_report(title=None, fields=_proposal_fields(None, authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)), providers=providers)
        proposal = evaluate_report(report)
        packet = ShadowReviewPacket.create(ReviewCase.create(proposal, report), proposal, report)
        authors = next((value for value in packet.proposals if value['destination'] == 'authors'))
        self.assertEqual(1, len(authors['candidates']))
        summary = authors['candidates'][0]['decision_summary']
        self.assertEqual(MAX_DECISION_CORRELATIONS * MAX_CONTRIBUTIONS, MAX_CREATOR_ROLE_FACTS)
        self.assertEqual(3 * MAX_CONTRIBUTIONS, len(summary['creator_roles']['included']))
        self.assertEqual([], summary['creator_roles']['excluded'])
        self.assertEqual({'independent_provider_correlations': 3, 'local_evidence': 0, 'target_only_preservation': 0}, summary['support'])
        self.assertEqual(packet, ShadowReviewPacket.from_json(packet.to_json(), report))

    def test_selected_epub_edition_evidence_establishes_manifestation(self) -> None:
        report = _proposal_report(fields=_proposal_fields(isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)), local_claims=(_explicit_local_ebook_isbn(),), providers=())
        proposal = evaluate_report(report)
        packet = ShadowReviewPacket.create(ReviewCase.create(proposal, report), proposal, report)
        isbn = next((value for value in packet.proposals if value['destination'] == 'isbn'))
        summary = isbn['candidates'][0]['decision_summary']
        self.assertEqual('edition', summary['evidence_scope'])
        self.assertEqual('established', summary['selected_manifestation_binding'])
        self.assertEqual({'independent_provider_correlations': 0, 'local_evidence': 1, 'target_only_preservation': 0}, summary['support'])

    def test_identity_bound_creator_aliases_do_not_reach_shadow_packet(self) -> None:
        report = _identity_bound_alias_report(include_title=True)
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        authors = next((value for value in packet.proposals if value['destination'] == 'authors'))
        self.assertEqual([['Ada Example', 'Bob Example']], [candidate['value']['value'] for candidate in authors['candidates']])
        provider_creator_references = {claim.reference for claim in report.providers[0].candidates[0].claims if claim.kind is ClaimKind.CREATOR}
        self.assertTrue(provider_creator_references.isdisjoint((reference for candidate in authors['candidates'] for reference in candidate['evidence_references'])))

    def test_incomplete_identity_bindings_replay_direct_author_evidence(self) -> None:
        report = _partially_bound_direct_author_report(include_title=True)
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        authors = next((value for value in packet.proposals if value['destination'] == 'authors'))
        provider_creator_references = {claim.reference for claim in report.providers[0].candidates[0].claims if claim.kind is ClaimKind.CREATOR}
        self.assertTrue(provider_creator_references.issubset({reference for candidate in authors['candidates'] for reference in candidate['evidence_references']}))

    def test_publication_candidate_projects_typed_source_and_access_context(self) -> None:
        lexical = '2024-03-05T10:30:00-04:00'
        claim = ProviderClaim.create(provider='catalogue', candidate='edition-1', kind=ClaimKind.PUBLICATION_DATE, value=lexical, state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='not_applicable', raw_order=2)
        identifier = ProviderClaim.create(provider='catalogue', candidate='edition-1', kind=ClaimKind.ISBN, value='9780061120084', state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='ebook')
        asin = ProviderClaim.create(provider='catalogue', candidate='edition-1', kind=ClaimKind.ASIN, value='b000000001', state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='ebook', raw_order=1)
        local_date = LocalClaim.create(kind=ClaimKind.PUBLICATION_DATE, observed='2024-03-05', canonical='2024-03-05', excerpt='2024-03-05', applicability='not_applicable', checksum='not_applicable', applicability_basis='not_applicable', scope=ClaimScope.EDITION, date_event=DateEvent.MANIFESTATION_PUBLICATION, date_precision=DatePrecision.DAY)
        report = MetadataReport.create(target=TargetFacts('primary', 'ebooks', 'item-1', default_target_fields(None)), file=FileFacts(0, 'a' * 64, 'b' * 64), local_claims=(_local_isbn(), local_date), local_reasons=('package_metadata_isbn',), providers=(ProviderObservation('catalogue', ProviderStatus.CANDIDATES, (), (ProviderCandidate('edition-1', 'work-1', (identifier, asin, claim), (ProviderEvidence(ProviderEvidenceKind.PHYSICAL_FORMAT, 'ebook', ClaimScope.EDITION),)),), ProviderQueryKind.WORK, 'hardcover', accessed_at='2023-11-14T22:13:20.125000Z'),))
        report = MetadataReport.from_json(report.to_json())
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        with self.assertRaisesRegex(ShadowReviewValueError, 'authoritative source is unavailable'):
            ShadowReviewPacket.from_json(packet.to_json())
        projected = next((value for value in packet.proposals if value['destination'] == 'publishedDate'))['candidates'][0]
        self.assertEqual('manifestation_publication', projected['date_event'])
        self.assertEqual('day', projected['date_precision'])
        provider_reference = next((reference for reference in projected['evidence_references'] if reference.startswith('provider_')))
        local_reference = local_date.reference
        for name, removed, remove_candidate_reference in (('provider', {provider_reference}, False), ('linked_local', {local_reference}, False), ('provider_and_linked_local', {provider_reference, local_reference}, False), ('provider_local_and_candidate_reference', {provider_reference, local_reference}, True)):
            document = json.loads(packet.to_json())
            document['citations'] = [value for value in document['citations'] if value['citation_id'] not in removed]
            if remove_candidate_reference:
                next((value for value in document['proposals'] if value['destination'] == 'publishedDate'))['candidates'][0]['evidence_references'] = []
            serialized = canonical_json(document)
            for source in (None, report):
                with self.subTest(tamper=name, source='absent' if source is None else 'present'), self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(serialized, source)
        document = json.loads(packet.to_json())
        injected_reference = 'local_' + '0' * 64
        document['citations'].append({'citation_id': injected_reference, 'fact': {'canonical': 'invented', 'reference': injected_reference}})
        document['citations'].sort(key=lambda value: value['citation_id'])
        for source in (None, report):
            with self.subTest(tamper='injected_local', source='absent' if source is None else 'present'), self.assertRaises(ShadowReviewValueError):
                ShadowReviewPacket.from_json(canonical_json(document), source)
        original_document = json.loads(packet.to_json())
        original_ids = (original_document['review_case_id'], [(proposal['proposal_id'], [candidate['candidate_id'] for candidate in proposal['candidates']]) for proposal in original_document['proposals']])
        for tamper in ('destination', 'date_typing', 'evidence_references', 'citations', 'complete_projection_rewrite'):
            document = json.loads(packet.to_json())
            publication = next((value for value in document['proposals'] if value['destination'] == 'publishedDate'))
            if tamper in {'destination', 'complete_projection_rewrite'}:
                publication['destination'] = 'title'
            if tamper in {'destination', 'date_typing', 'complete_projection_rewrite'}:
                for candidate in publication['candidates']:
                    candidate['date_event'] = None
                    candidate['date_precision'] = None
            if tamper in {'evidence_references', 'complete_projection_rewrite'}:
                for candidate in publication['candidates']:
                    candidate['evidence_references'] = []
            if tamper in {'citations', 'complete_projection_rewrite'}:
                document['citations'] = []
            self.assertEqual(original_ids, (document['review_case_id'], [(proposal['proposal_id'], [candidate['candidate_id'] for candidate in proposal['candidates']]) for proposal in document['proposals']]))
            for source in (None, report):
                with self.subTest(tamper=f'preserved_ids_{tamper}', source='absent' if source is None else 'present'), self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), source)
        citation = next((value for value in packet.citations if value['citation_id'] == claim.reference))['fact']
        self.assertEqual('hardcover', citation['access']['authority'])
        self.assertEqual('work', citation['access']['query_kind'])
        self.assertEqual('2023-11-14T22:13:20.125000Z', citation['access']['accessed_at'])
        self.assertEqual('edition-1', citation['candidate'])
        self.assertEqual('work-1', citation['work_identity'])
        self.assertEqual('B000000001', asin.value)
        expected_identifier_facts = sorted([{'applicability': 'ebook', 'kind': 'isbn', 'raw_order': 0, 'reference': identifier.reference, 'scope': 'edition', 'value': '9780061120084'}, {'applicability': 'ebook', 'kind': 'asin', 'raw_order': 1, 'reference': asin.reference, 'scope': 'edition', 'value': 'B000000001'}], key=lambda fact: fact['reference'])
        self.assertEqual([fact['reference'] for fact in expected_identifier_facts], citation['linkage']['identifier_references'])
        self.assertEqual(expected_identifier_facts, citation['linkage']['identifier_facts'])
        self.assertEqual('ebook', citation['linkage']['format_facts'][0]['value'])
        self.assertEqual(lexical, citation['fact']['date_lexical'])
        self.assertEqual('timestamp', citation['fact']['date_precision'])
        document = json.loads(packet.to_json())
        published = next((value for value in document['proposals'] if value['destination'] == 'publishedDate'))
        published['candidates'][0]['date_event'] = 'work_first_publication'
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        def provider_date(document: dict[str, object]) -> dict[str, object]:
            return next((value for value in document['citations'] if value['citation_id'] == claim.reference))['fact']
        for field, invalid in (('applicability', 'print'), ('kind', 'title'), ('raw_order', 99), ('reference', 'provider_' + '0' * 64), ('scope', 'work'), ('value', '9780061120085'), ('value', 'sk-proj-credential-shaped')):
            document = json.loads(packet.to_json())
            linked = provider_date(document)['linkage']
            fact = next((value for value in linked['identifier_facts'] if value['kind'] == 'isbn'))
            fact[field] = invalid
            with self.subTest(field=field, invalid=invalid):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), report)
        document = json.loads(packet.to_json())
        linked = provider_date(document)['linkage']
        next((value for value in linked['identifier_facts'] if value['kind'] == 'asin'))['value'] = 'A000000001'
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)
        for extra in ('role', 'state'):
            document = json.loads(packet.to_json())
            provider_date(document)['linkage']['identifier_facts'][0][extra] = None if extra == 'role' else 'malformed'
            with self.subTest(extra=extra):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), report)
        for field, invalid in (('candidate', 'edition-2'), ('provider', 'other'), ('candidate', '/private/library/edition'), ('work_identity', 'api_key=private')):
            document = json.loads(packet.to_json())
            provider_date(document)[field] = invalid
            with self.subTest(context=field, invalid=invalid):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), report)
        document = json.loads(packet.to_json())
        provider_date(document)['linkage']['identifier_facts'].pop()
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)
        document = json.loads(packet.to_json())
        provider_date(document)['linkage']['identifier_references'].pop()
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)
        document = json.loads(packet.to_json())
        linked = provider_date(document)['linkage']
        linked['identifier_facts'].append(linked['identifier_facts'][0].copy())
        linked['identifier_references'].append(linked['identifier_references'][0])
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)
        document = json.loads(packet.to_json())
        linked = provider_date(document)['linkage']
        linked['identifier_facts'].extend((linked['identifier_facts'][0].copy() for _ in range(MAX_LINKAGE_IDENTIFIER_FACTS - 1)))
        linked['identifier_references'].extend((linked['identifier_references'][0] for _ in range(MAX_LINKAGE_IDENTIFIER_FACTS - 1)))
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)
        document = json.loads(packet.to_json())
        linked = provider_date(document)['linkage']
        linked['identifier_facts'].reverse()
        linked['identifier_references'].reverse()
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)
        document = json.loads(packet.to_json())
        linked = provider_date(document)['linkage']
        linked['format_facts'][0]['role'] = 'narrator'
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)
        for invalid in ('/private/library/format', 'api_key=private'):
            document = json.loads(packet.to_json())
            provider_date(document)['linkage']['format_facts'][0]['value'] = invalid
            with self.subTest(format_value=invalid):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), report)
        document = json.loads(packet.to_json())
        del provider_date(document)['linkage']
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)
        invented_identifier = ProviderClaim.create(provider='catalogue', candidate='edition-1', kind=ClaimKind.ISBN, value='9780306406157', state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='ebook', raw_order=7)
        invented_identifier_data = invented_identifier.to_data()
        invented_identifier_fact = {key: invented_identifier_data[key] for key in ('applicability', 'kind', 'raw_order', 'reference', 'scope', 'value')}
        invented_format = ProviderEvidence(ProviderEvidenceKind.PHYSICAL_FORMAT, 'hardcover', ClaimScope.EDITION).to_data()

        def add_identifier(linkage: dict[str, object]) -> None:
            linkage['identifier_facts'].append(invented_identifier_fact)
            linkage['identifier_facts'].sort(key=lambda fact: fact['reference'])
            linkage['identifier_references'][:] = [fact['reference'] for fact in linkage['identifier_facts']]

        def add_format(linkage: dict[str, object]) -> None:
            linkage['format_facts'].append(invented_format)
            linkage['format_facts'].sort(key=canonical_json)
        for name, mutate in (('invented_valid_isbn', add_identifier), ('invented_physical_format', add_format)):
            document = json.loads(packet.to_json())
            mutate(provider_date(document)['linkage'])
            forged_json = canonical_json(document)
            with self.subTest(binding=name, document='packet'):
                with self.assertRaisesRegex(ShadowReviewValueError, 'source binding is invalid'):
                    ShadowReviewPacket.from_json(forged_json, report)
        self.assertEqual(16, MAX_LINKAGE_IDENTIFIER_FACTS)
        self.assertEqual(16, MAX_LINKAGE_FORMAT_FACTS)

    def test_provider_rich_date_evidence_requires_content_bound_linkage(self) -> None:
        identifier = ProviderClaim.create(provider='catalogue', candidate='edition-1', kind=ClaimKind.ISBN, value='9780061120084', state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='ebook')
        first_publication = ProviderEvidence(ProviderEvidenceKind.FIRST_PUBLICATION_DATE, '2001-02-03', ClaimScope.WORK)
        report = MetadataReport.create(target=TargetFacts('primary', 'ebooks', 'item-1', default_target_fields(None)), file=FileFacts(0, 'a' * 64, 'b' * 64), local_claims=(_local_isbn(), LocalClaim.create(kind=ClaimKind.PUBLICATION_DATE, observed='2002', canonical='2002', excerpt='2002', applicability='not_applicable', checksum='not_applicable', applicability_basis='not_applicable', scope=ClaimScope.EDITION, date_event=DateEvent.MANIFESTATION_PUBLICATION, date_precision=DatePrecision.YEAR)), local_reasons=('package_metadata_isbn',), providers=(ProviderObservation('catalogue', ProviderStatus.CANDIDATES, (), (ProviderCandidate('edition-1', 'work-1', (identifier,), (first_publication,)),)),))
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        replayed = ShadowReviewPacket.from_json(packet.to_json(), report)
        rich = next((citation['fact'] for citation in replayed.citations if 'evidence' in citation['fact'] and citation['fact']['evidence']['kind'] == 'first_publication_date'))
        self.assertEqual([identifier.reference], rich['linkage']['identifier_references'])
        self.assertEqual('event_incompatible', rich['evidence']['links'][0]['publication_date_relation'])
        self.assertEqual(0, rich['linkage']['identifier_facts'][0]['raw_order'])

        def rich_date(document: dict[str, object]) -> dict[str, object]:
            return next((citation['fact'] for citation in document['citations'] if 'evidence' in citation['fact'] and citation['fact']['evidence']['kind'] == 'first_publication_date'))
        document = json.loads(packet.to_json())
        del rich_date(document)['linkage']
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)
        document = json.loads(packet.to_json())
        rich_date(document)['linkage'] = {'identifier_references': []}
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)
        for field, invalid in (('candidate', 'edition-2'), ('provider', 'other')):
            document = json.loads(packet.to_json())
            rich_date(document)[field] = invalid
            with self.subTest(field=field):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), report)
        document = json.loads(packet.to_json())
        rich_date(document)['evidence']['kind'] = 'copyright_date'
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

    def test_unbound_manifestation_incompatibility_replays_from_target_lookup(self) -> None:
        provider_title = ProviderClaim.create(provider='catalogue', candidate='edition-1', kind=ClaimKind.TITLE, value='Provider title', state=FieldState.VALID, scope=ClaimScope.WORK, applicability='not_applicable')
        publication_text = ProviderEvidence(ProviderEvidenceKind.PUBLICATION_DATE_TEXT, '2001-02-03', ClaimScope.EDITION)
        target_fields = tuple((TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, ('Ada Author',)) if field.destination is TargetDestination.AUTHORS else field for field in default_target_fields('Target title')))
        report = MetadataReport.create(target=TargetFacts('primary', 'ebooks', 'item-1', target_fields), file=FileFacts(0, 'a' * 64, 'b' * 64), local_claims=(), local_reasons=('no_opening_claims',), providers=(ProviderObservation('catalogue', ProviderStatus.CANDIDATES, candidates=(ProviderCandidate('edition-1', 'work-1', (provider_title,), (publication_text,)),), query_kind=ProviderQueryKind.WORK),))
        proposal = evaluate_report(report)
        self.assertEqual('ineligible', next((result.outcome.value for result in proposal.results if result.destination.value == 'publishedDate')))
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)

        def retained_date(document: dict[str, object]) -> dict[str, object]:
            return next((citation['fact']['evidence'] for citation in document['citations'] if 'evidence' in citation['fact'] and citation['fact']['evidence']['kind'] == 'publication_date_text'))
        replayed = ShadowReviewPacket.from_json(packet.to_json(), report)
        evidence = retained_date(replayed.to_data())
        self.assertEqual(PublicationDateRelation.MANIFESTATION_INCOMPATIBLE.value, evidence['links'][0]['publication_date_relation'])
        self.assertIsNone(evidence['links'][0]['local_reference'])
        self.assertFalse(any((value['citation_id'].startswith('local_') for value in packet.citations)))
        tampered = json.loads(packet.to_json())
        retained_date(tampered)['links'] = []
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(tampered), report)

    def test_contradictory_work_relation_date_is_visible_but_not_a_candidate(self) -> None:
        local_date = LocalClaim.create(kind=ClaimKind.PUBLICATION_DATE, observed='2002', canonical='2002', excerpt='2002', applicability='not_applicable', checksum='not_applicable', applicability_basis='not_applicable', scope=ClaimScope.EDITION, date_event=DateEvent.MANIFESTATION_PUBLICATION, date_precision=DatePrecision.YEAR)
        report = MetadataReport.create(target=TargetFacts('primary', 'ebooks', 'item-1', default_target_fields(None)), file=FileFacts(0, 'a' * 64, 'b' * 64), local_claims=(_local_isbn(), local_date), local_reasons=('package_metadata_isbn',), providers=(_contradictory_publication_provider(),))
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        self.assertIn(local_date.reference, {value['citation_id'] for value in packet.citations})
        self.assertEqual(packet, ShadowReviewPacket.from_json(packet.to_json(), report))
        publication = next((evidence for evidence in report.providers[0].candidates[0].evidence if evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT))
        citation = next((value for value in packet.citations if value['citation_id'] == 'evidence_' + hashlib.sha256(canonical_json({'candidate': 'edition-1', 'evidence': publication.to_data(), 'provider': 'catalogue'}).encode('utf-8')).hexdigest()))
        self.assertEqual('manifestation_incompatible', citation['fact']['evidence']['links'][0]['publication_date_relation'])
        self.assertFalse(any((citation['citation_id'] in candidate['evidence_references'] for value in packet.proposals for candidate in value['candidates'])))
        tampered = json.loads(packet.to_json())
        tampered['citations'] = [value for value in tampered['citations'] if value['citation_id'] != local_date.reference]
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(tampered), report)
        tampered = json.loads(packet.to_json())
        local_citation = next((value for value in tampered['citations'] if value['citation_id'] == local_date.reference))
        local_citation['fact']['canonical'] = '2003'
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(tampered), report)

    def test_publication_linkage_construction_rejects_context_over_limits(self) -> None:

        def packet_for(claims: tuple[ProviderClaim, ...], evidence: tuple[ProviderEvidence, ...]) -> None:
            report = MetadataReport.create(target=TargetFacts('primary', 'ebooks', 'item-1', default_target_fields(None)), file=FileFacts(0, 'a' * 64, 'b' * 64), local_claims=(_local_isbn(),), local_reasons=('package_metadata_isbn',), providers=(ProviderObservation('catalogue', ProviderStatus.CANDIDATES, (), (ProviderCandidate('edition-1', 'work-1', claims, evidence),)),))
            proposal = evaluate_report(report)
            case = ReviewCase.create(proposal, report)
            ShadowReviewPacket.create(case, proposal, report)
        isbn = ProviderClaim.create(provider='catalogue', candidate='edition-1', kind=ClaimKind.ISBN, value='9780061120084', state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='ebook')
        identifiers = (isbn, *tuple((ProviderClaim.create(provider='catalogue', candidate='edition-1', kind=ClaimKind.ASIN, value='B000000001', state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='ebook', raw_order=order + 1) for order in range(MAX_LINKAGE_IDENTIFIER_FACTS))))
        publication = ProviderClaim.create(provider='catalogue', candidate='edition-1', kind=ClaimKind.PUBLICATION_DATE, value='2024-03-05', state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='not_applicable', raw_order=len(identifiers))
        packet_for((*identifiers[:-1], publication), ())
        with self.assertRaises(ShadowReviewValueError):
            packet_for((*identifiers, publication), ())
        formats = tuple((ProviderEvidence(ProviderEvidenceKind.PHYSICAL_FORMAT, f'format-{index:02d}', ClaimScope.EDITION) for index in range(MAX_LINKAGE_FORMAT_FACTS + 1)))
        publication = ProviderClaim.create(provider='catalogue', candidate='edition-1', kind=ClaimKind.PUBLICATION_DATE, value='2024-03-05', state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='not_applicable')
        packet_for((isbn, publication), formats[:-1])
        with self.assertRaises(ShadowReviewValueError):
            packet_for((isbn, publication), formats)
        private_candidate = '/private/library/edition-1'
        private_publication = ProviderClaim.create(provider='catalogue', candidate=private_candidate, kind=ClaimKind.PUBLICATION_DATE, value='2024-03-05', state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='not_applicable')
        report = MetadataReport.create(target=TargetFacts('primary', 'ebooks', 'item-1', default_target_fields(None)), file=FileFacts(0, 'a' * 64, 'b' * 64), local_claims=(_local_isbn(),), local_reasons=('package_metadata_isbn',), providers=(ProviderObservation('catalogue', ProviderStatus.CANDIDATES, (), (ProviderCandidate(private_candidate, 'work-1', (ProviderClaim.create(provider='catalogue', candidate=private_candidate, kind=ClaimKind.ISBN, value='9780061120084', state=FieldState.VALID, scope=ClaimScope.EDITION, applicability='ebook'), private_publication)),)),))
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.create(case, proposal, report)
if __name__ == '__main__':
    unittest.main()
