import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/cross-asset-etf-research/scripts/setup_runtime.py"


def run_setup(script, destination, cwd):
    return subprocess.run(
        [sys.executable, "-B", str(script), str(destination)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_copied_skill_without_engine_reports_missing_source(tmp_path):
    script = tmp_path / "copied skill/scripts/setup_runtime.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, script)
    destination = tmp_path / "runtime"

    result = run_setup(script, destination, tmp_path)

    assert result.returncode == 1
    assert "engine source" in result.stderr.lower()
    assert not destination.exists()


def test_setup_preserves_existing_environment(tmp_path):
    destination = tmp_path / "runtime"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("existing environment", encoding="utf-8")

    result = run_setup(SCRIPT, destination, tmp_path)

    assert result.returncode == 1
    assert "already exists" in result.stderr.lower()
    assert sentinel.read_text(encoding="utf-8") == "existing environment"
    assert list(destination.iterdir()) == [sentinel]


def test_failed_install_removes_only_new_environment(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("setup_runtime", SCRIPT)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)

    class LocalEnvironment:
        def __init__(self, with_pip):
            assert with_pip

        def create(self, destination):
            (destination / "generated.txt").write_text("temporary", encoding="utf-8")

    def fail_install(command, check):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(runtime.venv, "EnvBuilder", LocalEnvironment)
    monkeypatch.setattr(runtime.subprocess, "run", fail_install)
    destination = tmp_path / "new environment"
    untouched = tmp_path / "untouched"
    untouched.write_text("preserve", encoding="utf-8")

    assert runtime.main([str(destination)]) == 1
    assert not destination.exists()
    assert untouched.read_text(encoding="utf-8") == "preserve"


def test_setup_keeps_build_artifacts_out_of_skill_source(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("setup_runtime", SCRIPT)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    skill = tmp_path / "copied skill"
    monkeypatch.setattr(runtime, "__file__", str(skill / "scripts/setup_runtime.py"))
    source = skill / "assets/engine"
    package = source / "src/etf_backtest"
    package.mkdir(parents=True)
    (source / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
    (source / "LICENSE").write_text("Fixture license\n", encoding="utf-8")
    (package / "__init__.py").write_text("__version__ = '0.1.0'\n", encoding="utf-8")
    original = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}

    class LocalEnvironment:
        def __init__(self, with_pip):
            assert with_pip

        def create(self, destination):
            pass

    def build_local_source(command, check):
        local_source = Path(command[-1])
        for relative, content in original.items():
            assert (local_source / relative).read_bytes() == content
        (local_source / "build").mkdir()
        (local_source / "build" / "generated").write_text("build artifact", encoding="utf-8")

    monkeypatch.setattr(runtime.venv, "EnvBuilder", LocalEnvironment)
    monkeypatch.setattr(runtime.subprocess, "run", build_local_source)

    assert runtime.main([str(tmp_path / "new environment")]) == 0
    assert {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()} == original


@pytest.mark.parametrize("script_path", [
    "/etf-backtest/scripts/setup_runtime.py",
    "/tools/etf-backtest/scripts/setup_runtime.py",
])
def test_shallow_skill_path_reports_missing_source_without_crashing(tmp_path, monkeypatch, capsys, script_path):
    spec = importlib.util.spec_from_file_location("setup_runtime", SCRIPT)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(runtime, "__file__", script_path)
    destination = tmp_path / "runtime"

    assert runtime.main([str(destination)]) == 1
    assert "engine source" in capsys.readouterr().err.lower()
    assert not destination.exists()
