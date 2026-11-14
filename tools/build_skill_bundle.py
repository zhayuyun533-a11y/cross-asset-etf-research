#!/usr/bin/env python3
"""Export this project's ETF Skill and its local runtime source."""

import argparse
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "cross-asset-etf-research"
SKILL_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "scripts/setup_runtime.py",
    "references/methodology.md",
    "references/price-only.md",
    "references/runtime.md",
    "references/market-data.md",
)
ENGINE_MODULES = (
    "__init__.py",
    "__main__.py",
    "cli.py",
    "compare.py",
    "core.py",
    "coverage.py",
    "demo.py",
    "market_data.py",
)
CASE_STUDY_FILES = ("case-study.md", "holdout-drawdown.png")


def _validate_source_path(root, source):
    for path in (source, *source.parents):
        if path == root:
            break
        if path.is_symlink():
            raise ValueError(f"Symlinks cannot be exported: {path}")
    if not source.resolve().is_relative_to(root):
        raise ValueError(f"Source path must remain inside the project: {source}")


def _copy_file(root, source, destination):
    _validate_source_path(root, source)
    if not source.is_file():
        raise FileNotFoundError(f"Source file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build_skill_bundle(destination, *, project_root=None):
    """Create a fresh parent containing ``cross-asset-etf-research/`` and its ZIP.

    All copying and archiving finishes in temporary storage before publication.
    Existing destination files, directories, and symlinks are never overwritten.
    The caller must supply an existing parent directory for the destination.
    """
    root = Path(project_root).resolve() if project_root is not None else PROJECT_ROOT
    requested_destination = Path(destination).absolute()
    if requested_destination.exists() or requested_destination.is_symlink():
        raise FileExistsError(f"Destination already exists: {requested_destination}")
    destination = requested_destination.resolve()
    source_skill = root / ".agents" / "skills" / SKILL_NAME
    source_package = root / "src" / "etf_backtest"
    source_docs = root / "docs"
    for source in (source_skill, source_package, source_docs):
        _validate_source_path(root, source)
        resolved_source = source.resolve()
        if destination.is_relative_to(resolved_source) or resolved_source.is_relative_to(destination):
            raise ValueError(f"Destination must not overlap a source directory: {source}")
        if not source.is_dir():
            raise FileNotFoundError(f"Source directory is missing: {source}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Destination parent must already exist: {destination.parent}")

    with TemporaryDirectory(prefix=".etf-skill-bundle-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        skill = staging / SKILL_NAME
        for filename in SKILL_FILES:
            _copy_file(root, source_skill / filename, skill / filename)
        engine = skill / "assets" / "engine"
        for module in ENGINE_MODULES:
            _copy_file(root, source_package / module, engine / "src" / "etf_backtest" / module)
        _copy_file(root, root / "pyproject.toml", engine / "pyproject.toml")
        _copy_file(root, root / "LICENSE", engine / "LICENSE")
        _copy_file(root, root / "LICENSE", skill / "LICENSE")
        for filename in CASE_STUDY_FILES:
            _copy_file(root, source_docs / filename, skill / "references" / filename)

        archive = staging / f"{SKILL_NAME}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in sorted(skill.rglob("*")):
                if path.is_file():
                    output.write(path, path.relative_to(staging).as_posix())

        # mkdir is exclusive, including when an empty directory appears while staging.
        destination.mkdir()
        try:
            skill.rename(destination / SKILL_NAME)
            archive.rename(destination / archive.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "destination", type=Path,
        help="fresh destination parent to create; its own parent must already exist",
    )
    arguments = parser.parse_args(argv)
    try:
        destination = build_skill_bundle(arguments.destination)
    except (OSError, ValueError, shutil.Error) as error:
        parser.exit(1, f"Cannot build Skill bundle: {error}\n")
    print(destination / SKILL_NAME)
    print(destination / f"{SKILL_NAME}.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
