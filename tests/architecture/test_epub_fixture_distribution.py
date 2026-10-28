from __future__ import annotations

import subprocess
import tarfile
import tempfile
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def distribution() -> tuple[list[str], list[str]]:
    """The member names of one built wheel and sdist, in that order."""

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        completed = subprocess.run(
            (
                "uv",
                "build",
                "--sdist",
                "--wheel",
                "--out-dir",
                str(output),
            ),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        with ZipFile(next(output.glob("*.whl"))) as wheel:
            wheel_names = wheel.namelist()
        with tarfile.open(next(output.glob("*.tar.gz")), "r:gz") as sdist:
            sdist_names = sdist.getnames()
    return wheel_names, sdist_names


@pytest.mark.parametrize("index", (0, 1), ids=("wheel", "sdist"))
def test_distribution_excludes_fixture_registry_generators_and_materialized_files(
    distribution: tuple[list[str], list[str]], index: int
) -> None:
    names = distribution[index]
    serialized = "\n".join(names)
    assert "tests/fixtures/epub_package" not in serialized
    assert "tests/support/epub_packages.py" not in serialized
    assert "materialize_epub_fixtures.py" not in serialized
    assert not any(name.casefold().endswith(".epub") for name in names)


def test_sdist_carries_the_frontend_sources_and_no_built_or_vendored_trees(
    distribution: tuple[list[str], list[str]],
) -> None:
    _, sdist_names = distribution
    assert any(name.endswith("/frontend/package.json") for name in sdist_names)
    assert any(name.endswith("/scripts/build_frontend.py") for name in sdist_names)
    assert any(
        name.endswith("/docs/reference/review-openapi-v1.json") for name in sdist_names
    )
    assert any(
        name.endswith(
            "/src/mechanical_librarian/interfaces/web/settings_vocabulary.json"
        )
        for name in sdist_names
    )
    assert not any("frontend/src/generated" in name for name in sdist_names)
    assert not any("frontend/node_modules" in name for name in sdist_names)


def test_wheel_carries_the_built_react_assets_and_neither_carries_source_maps(
    distribution: tuple[list[str], list[str]],
) -> None:
    wheel_names, sdist_names = distribution
    assert any("interfaces/web/react_assets/assets/" in name for name in wheel_names)
    # Precompressed siblings ship beside every hashed asset they encode.
    for suffix in (".js.br", ".js.gz", ".css.br", ".css.gz"):
        assert any(name.endswith(suffix) for name in wheel_names)
    assert any(
        name.endswith("mechanical_librarian/interfaces/web/settings_vocabulary.json")
        for name in wheel_names
    )
    assert not any(name.endswith(".map") for name in wheel_names + sdist_names)


def test_both_carry_the_vendored_inter_face_and_its_licence(
    distribution: tuple[list[str], list[str]],
) -> None:
    wheel_names, sdist_names = distribution
    # woff2 is already Brotli-compressed, so a font ships with no siblings.
    fonts = [name for name in wheel_names if name.endswith(".woff2")]
    assert len(fonts) == 2
    assert all("interfaces/web/react_assets/assets/inter-" in name for name in fonts)
    assert not any(name.endswith((".woff2.br", ".woff2.gz")) for name in wheel_names)
    assert any(
        ".dist-info/licenses/frontend/src/fonts/LICENSE" in name for name in wheel_names
    )
    for file in (
        "inter-latin-wght-normal.woff2",
        "inter-latin-ext-wght-normal.woff2",
        "LICENSE",
    ):
        assert any(name.endswith(f"/frontend/src/fonts/{file}") for name in sdist_names)
