"""Fixed-byte synthetic author statements shared by extraction and lifecycle proof."""

import html

from tests.support.epub_packages import _opening_epub, _opening_package, _xhtml

AUTHOR_STATEMENTS = (
    ("<ul><li><p>Author: Ada Example</p></li></ul>", "Ada Example", "author"),
    (
        "<div><p>Cover design by Pat Example</p><p>Author: Ada Example</p></div>",
        "Ada Example",
        "author",
    ),
    (
        '<section epub:type="titlepage"><p>By Ada Example</p></section>',
        "Ada Example",
        "author",
    ),
    ('<p epub:type="creator">Written by Ada Example</p>', "Ada Example", "author"),
    (
        '<h1 epub:type="fulltitle">Clockwork</h1><p>by Ada Example</p>',
        "Ada Example",
        "author",
    ),
    (
        '<section epub:type="copyright-page"><p>Author: Ada Example</p></section>',
        "Ada Example",
        "author",
    ),
    (
        '<section epub:type="titlepage"><p>By <em>Ada Example</em></p></section>',
        "Ada Example",
        "author",
    ),
    ('<p epub:type="creator">Ada Example</p>', "Ada Example", None),
    ("<p>Author photograph by Pat Example</p>", None, None),
    (
        '<p>Copyright text <span epub:type="creator">by Ada Example</span></p>',
        None,
        None,
    ),
    (
        '<section epub:type="titlepage"><section epub:type="copyright-page"><p>By Example House</p></section></section>',
        None,
        None,
    ),
    ("<p>By the river the boat waited</p>", None, None),
    (
        '<section epub:type="bodymatter"><p>Author: Ada Example</p></section>',
        None,
        None,
    ),
    (
        '<figure><figcaption><span epub:type="creator">By Ada Example</span></figcaption></figure>',
        None,
        None,
    ),
    ('<p>Cover design <span epub:type="creator">by Ada Example</span></p>', None, None),
    ("<p>Author Ada Example</p>", None, None),
)


MIXED_AUTHOR_STATEMENTS = (
    "<p>Author: Ada Example. All rights reserved.</p>",
    "<p>Author: Ada Example. <span>ALL <em>rights</em>   reserved.</span></p>",
    "<p>Author: Ada Example; Publisher: Example House</p>",
    "<p>Authors: Ada Example; Subtitle: A Clockwork Story</p>",
    "<p>Author: Ada Example;Imprint: Example House</p>",
    "<p>Author: Ada Example; Copyright by Example House</p>",
    "<p>Author: Ada Example; Copyright: Example House</p>",
    "<p>Author: Ada Example; © Example House</p>",
    '<p epub:type="creator">Written by Ada Example; Publisher: Example House</p>',
    '<section epub:type="titlepage"><p>By Ada Example; Subtitle: Clockwork</p></section>',
    "<p>Author: <em>Ada Example</em>; <span>Publisher: Example House</span></p>",
    "<p>Authors: Ada Example; <span>Copyright <em>by Example House</em></span></p>",
)

TYPED_CREDIT_STATEMENTS = (
    (
        '<span epub:type="contributor">Translated by Bea Artist</span>',
        "Translated by Bea Artist",
        "translator",
        "CONTRIBUTOR",
    ),
    (
        "<div>Edited by Bea Artist</div>",
        "Edited by Bea Artist",
        "editor",
        "CONTRIBUTOR",
    ),
    (
        "<section>Illustrated by Bea Artist</section>",
        "Illustrated by Bea Artist",
        "illustrator",
        "CONTRIBUTOR",
    ),
    (
        "<div>Converted by Bea Artist</div>",
        "Converted by Bea Artist",
        "conversion",
        "CONTEXTUAL_CREDIT",
    ),
    (
        "<p>Translated by <em>Bea Artist</em></p>",
        "Translated by Bea Artist",
        "translator",
        "CONTRIBUTOR",
    ),
)

LEGITIMATE_AUTHOR_STATEMENTS = (
    ("<p>Authors: The Imprint Collective</p>", "The Imprint Collective"),
    ("<p>Author: The Copyright Collective</p>", "The Copyright Collective"),
    ("<p>Author: Ada Publisher</p>", "Ada Publisher"),
    ("<p>Author: Élodie O’Neill-Smith</p>", "Élodie O’Neill-Smith"),
)


def author_epub(
    body: str,
    *,
    rear: bool = False,
    recovered: bool = False,
    package_creators: tuple[tuple[str, str | None], ...] = (),
) -> bytes:
    """Use nine slots to force rear inspection without changing the opening limit."""
    media = "text/html" if recovered else "application/xhtml+xml"
    items = tuple((f"slot{i}", f"slot{i}.xhtml", media, "") for i in range(9))
    package = _opening_package(
        version=2 if recovered else 3,
        items=items,
        spine=tuple(item[0] for item in items),
        guide=tuple(("preface", f"slot{i}.xhtml") for i in range(9))
        if recovered
        else (),
    )
    metadata = "".join(
        f'<dc:creator id="creator{index}">{html.escape(name)}</dc:creator>'
        + (
            f'<meta refines="#creator{index}" property="role" scheme="marc:relators">{html.escape(role)}</meta>'
            if role is not None
            else ""
        )
        for index, (name, role) in enumerate(package_creators)
    )
    package = package.replace(b"</metadata>", metadata.encode() + b"</metadata>")
    content = ("<p>All rights reserved</p>" if rear else "") + body

    def markup(value: str) -> bytes:
        if recovered:
            return (
                '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                + value
                + "</body></html>"
            ).encode()
        return _xhtml(value)

    return _opening_epub(
        version=2 if recovered else 3,
        package=package,
        entries=tuple(
            (
                f"EPUB/slot{i}.xhtml",
                markup(
                    content if i == (8 if rear else 0) else "<p>Ordinary prose.</p>"
                ),
                8,
            )
            for i in range(9)
        ),
    )


def fragment_author_epub(
    role: str = "copyright-page",
    fragment: str = "#blocked",
    *,
    rear: bool = True,
    duplicate: bool = False,
) -> bytes:
    items = (
        ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
    ) + tuple((f"s{i}", f"s{i}.xhtml", "application/xhtml+xml", "") for i in range(9))
    package = _opening_package(items=items, spine=tuple(f"s{i}" for i in range(9)))
    links = "".join(
        f'<a epub:type="titlepage" href="s{i}.xhtml">Title</a>'
        for i in range(5 if rear else 0)
    )
    target = 8 if rear else 0
    links += f'<a epub:type="{role}" href="s{target}.xhtml{fragment}">Credit</a>'
    body = '<p>All rights reserved</p><div id="blocked"><p epub:type="creator">By Blocked Example</p></div><div><p epub:type="creator">By Allowed Example</p></div>'
    if duplicate:
        body += '<div id="blocked"><p>Other block</p></div>'
    return _opening_epub(
        package=package,
        entries=(
            (
                "EPUB/nav.xhtml",
                _xhtml('<nav epub:type="landmarks">' + links + "</nav>"),
                8,
            ),
        )
        + tuple(
            (
                f"EPUB/s{i}.xhtml",
                _xhtml(
                    body if i == target else '<h1 epub:type="fulltitle">Example</h1>'
                ),
                8,
            )
            for i in range(9)
        ),
    )


def author_report(
    root, scenario: str = "supported", *, rear: bool = True, recovered: bool = False
):
    """Real isolated extraction and canonical report for durable/UI consumers."""
    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )
    from mechanical_librarian.application.configuration import MediaMountConfiguration
    from mechanical_librarian.bootstrap.composition import _IsolatedEpubInspector
    from mechanical_librarian.domain.metadata_report import (
        ClaimKind,
        ClaimScope,
        FileFacts,
        MetadataReport,
        TargetDestination,
        TargetFacts,
        TargetField,
        TargetFieldState,
    )
    from tests.support.proposal_evidence import _claim, _fields, _provider

    names = (
        (
            "Alexandra Everlasting Example of the Northern Collaborative Writing Society",
            "Beatrice Wonderfully Long Example and the Clockwork Storytelling Collective",
        )
        if scenario == "long"
        else ("Ada Example",)
    )
    long_names = scenario == "long"
    if long_names:
        scenario = "supported"
    body = {
        "supported": "<p>Author photograph by Pat Example</p>",
        "incidental": "<p>Author photograph by Pat Example</p>",
        "conflict": "<p>Authors: Different Writer</p>",
        "local_fill": "<p>Authors: Ada Example</p>",
        "local_replace": "<p>Authors: Ada Example</p>",
        "none": "<p>Ordinary prose.</p>",
        "mixed": MIXED_AUTHOR_STATEMENTS[2],
        "copyright_notice": MIXED_AUTHOR_STATEMENTS[0],
        "single_provider": "<p>Ordinary prose.</p>",
        "two_provider_fill": "<p>Ordinary prose.</p>",
        "same_authority": "<p>Ordinary prose.</p>",
    }[scenario]
    if long_names:
        body = "<p>Author photograph by Patricia Example of the Northern Portrait and Archival Photography Collective, with image preparation for the Clockwork Illustrated Edition</p>"
    payload = author_epub(body, rear=rear, recovered=recovered)
    root.mkdir(parents=True, exist_ok=True)
    (root / "book.epub").write_bytes(payload)
    inspection = _IsolatedEpubInspector(CoverArtifactStore(root / "artifacts")).inspect(
        MediaMountConfiguration("books", str(root), "/library", True),
        "book.epub",
        len(payload) + 1,
    )
    providers = (
        tuple(
            _provider(
                name,
                name + "-edition",
                tuple(
                    _claim(
                        name,
                        name + "-edition",
                        ClaimKind.CREATOR,
                        author,
                        scope=ClaimScope.WORK,
                        role="author",
                        order=index,
                    )
                    for index, author in enumerate(names)
                ),
                authority="hardcover" if scenario == "same_authority" else name,
            )
            for name in (
                ("hardcover",)
                if scenario == "single_provider"
                else ("hardcover", "open_library")
            )
        )
        if scenario
        in {
            "supported",
            "conflict",
            "single_provider",
            "two_provider_fill",
            "same_authority",
        }
        else ()
    )
    current = (
        None
        if scenario
        in {
            "local_fill",
            "mixed",
            "copyright_notice",
            "single_provider",
            "two_provider_fill",
            "same_authority",
        }
        else ("Current Writer",)
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            _fields(
                authors=TargetField(
                    TargetDestination.AUTHORS,
                    TargetFieldState.MISSING
                    if current is None
                    else TargetFieldState.VALUE,
                    current,
                )
            ),
        ),
        file=FileFacts(0, "a" * 64, inspection.content_digest),
        local_claims=inspection.claims,
        local_reasons=("opening_matter",),
        providers=providers,
    )
    return MetadataReport.from_json(report.to_json())


def publish_author_case(repository, report, destination: str = "automatic"):
    """Owner policy activation and actual report-to-outcome publication."""
    import json

    from mechanical_librarian.domain.metadata_policy import (
        DEFAULT_OPTIONS,
        OPTION_CATALOGUE,
        MetadataPolicyProfile,
        human_review_destinations,
        resolve_policy,
    )
    from mechanical_librarian.domain.proposal_set import evaluate_report

    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    destinations["contributor_names"] = destination
    profile = MetadataPolicyProfile.create(
        "Author evidence",
        dict(DEFAULT_OPTIONS) | {"contributor_names": "canonical"},
        destinations,
    )
    resolved = resolve_policy(profile, report.target.library)
    repository.enter_shadow_preview(1)
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        2,
    )
    if destination == "automatic":
        repository.activate_qualified_automatic(3)
    proposal = evaluate_report(
        report, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    repository.persist_shadow_outcome(case_id, 4)
    return case_id, proposal
