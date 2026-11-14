"""Portable Skill exports keep runtime source and leave local data behind."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


REPO = Path(__file__).resolve().parents[1]
BUILDER = REPO / "tools" / "build_skill_bundle.py"


@pytest.fixture
def builder():
    assert BUILDER.is_file(), "The portable Skill builder has not been implemented"
    spec = importlib.util.spec_from_file_location("build_skill_bundle", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    files = {
        ".agents/skills/cross-asset-etf-research/SKILL.md": "---\nname: cross-asset-etf-research\n---\nUse the runtime.\n",
        ".agents/skills/cross-asset-etf-research/agents/openai.yaml": "display_name: ETF backtest\n",
        ".agents/skills/cross-asset-etf-research/scripts/setup_runtime.py": "print('setup helper')\n",
        ".agents/skills/cross-asset-etf-research/references/methodology.md": "Methodology\n",
        ".agents/skills/cross-asset-etf-research/references/price-only.md": "Price-only comparison\n",
        ".agents/skills/cross-asset-etf-research/references/runtime.md": "Runtime setup\n",
        ".agents/skills/cross-asset-etf-research/references/market-data.md": "Market data\n",
        ".agents/skills/cross-asset-etf-research/__pycache__/setup.pyc": "cache",
        ".agents/skills/cross-asset-etf-research/.venv/private.txt": "environment",
        "src/etf_backtest/__init__.py": "__version__ = '0.1.0'\n",
        "src/etf_backtest/__main__.py": "from .cli import main\n",
        "src/etf_backtest/cli.py": "def main():\n    print('runtime ready')\n",
        "src/etf_backtest/compare.py": "# Comparison\n",
        "src/etf_backtest/core.py": "# Backtest\n",
        "src/etf_backtest/coverage.py": "# Exchange calendar coverage\n",
        "src/etf_backtest/demo.py": "# Demo\n",
        "src/etf_backtest/market_data.py": "# Market data\n",
        "src/etf_backtest/__pycache__/cli.pyc": "cache",
        "docs/case-study.md": "# Case study\n\n![Drawdown](holdout-drawdown.png)\n",
        "docs/holdout-drawdown.png": "canonical image",
        "docs/unrelated.md": "private notes",
        "prices.csv": "private input",
        "LICENSE": "MIT license fixture\n",
        "pyproject.toml": (REPO / "pyproject.toml").read_text(encoding="utf-8"),
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_exports_source_runtime_and_canonical_references_without_local_data(builder, project, tmp_path):
    destination = tmp_path / "portable"
    builder.build_skill_bundle(destination, project_root=project)
    skill = destination / "cross-asset-etf-research"
    expected = {
        "SKILL.md", "agents/openai.yaml", "scripts/setup_runtime.py",
        "references/methodology.md", "references/price-only.md", "references/runtime.md",
        "references/market-data.md",
        "references/case-study.md",
        "references/holdout-drawdown.png", "LICENSE",
        "assets/engine/pyproject.toml", "assets/engine/LICENSE",
        "assets/engine/src/etf_backtest/__init__.py",
        "assets/engine/src/etf_backtest/__main__.py",
        "assets/engine/src/etf_backtest/cli.py",
        "assets/engine/src/etf_backtest/compare.py",
        "assets/engine/src/etf_backtest/core.py",
        "assets/engine/src/etf_backtest/coverage.py",
        "assets/engine/src/etf_backtest/demo.py",
        "assets/engine/src/etf_backtest/market_data.py",
    }
    actual = {path.relative_to(skill).as_posix() for path in skill.rglob("*") if path.is_file()}
    assert actual == expected
    assert (skill / "assets/engine/pyproject.toml").read_bytes() == (project / "pyproject.toml").read_bytes()
    assert (skill / "LICENSE").read_bytes() == (project / "LICENSE").read_bytes()
    assert (skill / "assets/engine/LICENSE").read_bytes() == (project / "LICENSE").read_bytes()
    assert (skill / "references/case-study.md").read_bytes() == (project / "docs/case-study.md").read_bytes()
    assert (skill / "references/holdout-drawdown.png").read_bytes() == (project / "docs/holdout-drawdown.png").read_bytes()
    with zipfile.ZipFile(destination / "cross-asset-etf-research.zip") as archive:
        archived = {name for name in archive.namelist() if not name.endswith("/")}
        assert archived == {f"cross-asset-etf-research/{relative}" for relative in expected}
        for relative in expected:
            assert archive.read(f"cross-asset-etf-research/{relative}") == (skill / relative).read_bytes()


@pytest.mark.parametrize("existing_kind", ["file", "empty-directory", "nonempty-directory"])
def test_existing_destination_is_never_replaced(builder, project, tmp_path, existing_kind):
    destination = tmp_path / "existing"
    if existing_kind == "file":
        destination.write_text("keep me", encoding="utf-8")
    else:
        destination.mkdir()
        if existing_kind == "nonempty-directory":
            (destination / "keep.txt").write_text("keep me", encoding="utf-8")
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(FileExistsError):
        builder.build_skill_bundle(destination, project_root=project)
    assert {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    assert destination.exists()


@pytest.mark.parametrize("source", [".agents/skills/cross-asset-etf-research", "src/etf_backtest", "docs"])
def test_destination_inside_copied_source_is_rejected(builder, project, source):
    destination = project / source / "export"
    with pytest.raises(ValueError, match="overlap"):
        builder.build_skill_bundle(destination, project_root=project)
    assert not destination.exists()


def test_missing_reference_leaves_no_partial_export(builder, project, tmp_path):
    (project / "docs/holdout-drawdown.png").unlink()
    destination = tmp_path / "portable"
    before = set(tmp_path.iterdir())
    with pytest.raises(FileNotFoundError):
        builder.build_skill_bundle(destination, project_root=project)
    assert set(tmp_path.iterdir()) == before
    assert not destination.exists()


def test_export_excludes_unintended_files_inside_copied_directories(builder, project, tmp_path):
    (project / ".agents/skills/cross-asset-etf-research/.env").write_text("PRIVATE_MARKER=fake")
    (project / "src/etf_backtest/price-input.csv").write_text("private input")
    (project / "src/etf_backtest/local_config.py").write_text("PRIVATE_MARKER = 'fake'\n")
    destination = tmp_path / "portable"

    builder.build_skill_bundle(destination, project_root=project)

    with zipfile.ZipFile(destination / "cross-asset-etf-research.zip") as archive:
        archived = {Path(name).name for name in archive.namelist()}
    assert ".env" not in archived
    assert "price-input.csv" not in archived
    assert "local_config.py" not in archived


def test_missing_engine_module_leaves_no_partial_export(builder, project, tmp_path):
    (project / "src/etf_backtest/compare.py").unlink()
    destination = tmp_path / "portable"
    before = set(tmp_path.iterdir())

    with pytest.raises(FileNotFoundError, match=r"compare\.py"):
        builder.build_skill_bundle(destination, project_root=project)

    assert set(tmp_path.iterdir()) == before


def test_symlink_for_allowed_reference_is_rejected_without_export(builder, project, tmp_path):
    private = tmp_path / "private.txt"
    private.write_text("do not distribute", encoding="utf-8")
    reference = project / ".agents/skills/cross-asset-etf-research/references/methodology.md"
    reference.unlink()
    reference.symlink_to(private)
    destination = tmp_path / "portable"
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        builder.build_skill_bundle(destination, project_root=project)
    assert not destination.exists()


def test_symlink_in_source_parent_is_rejected_without_export(builder, project, tmp_path):
    source_parent = project / ".agents"
    external = tmp_path / "outside"
    source_parent.rename(external)
    source_parent.symlink_to(external, target_is_directory=True)
    destination = tmp_path / "portable"

    with pytest.raises(ValueError, match="[Ss]ymlink"):
        builder.build_skill_bundle(destination, project_root=project)

    assert not destination.exists()


def test_cli_works_from_other_directory_and_exported_runtime_runs(tmp_path):
    destination = tmp_path / "portable"
    result = subprocess.run(
        [sys.executable, "-B", str(BUILDER), str(destination)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    skill = destination / "cross-asset-etf-research"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "references/case-study.md").is_file()
    assert (destination / "cross-asset-etf-research.zip").is_file()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(skill / "assets/engine/src")
    help_result = subprocess.run(
        [sys.executable, "-B", "-m", "etf_backtest", "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "compare" in help_result.stdout


def test_cli_rejects_existing_destination_without_overwriting(tmp_path):
    destination = tmp_path / "portable"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", str(BUILDER), str(destination)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert {path.name for path in destination.iterdir()} == {"keep.txt"}
