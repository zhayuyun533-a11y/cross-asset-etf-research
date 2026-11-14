"""Install this Skill's local engine source into a new Python environment."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import venv


def _install_local_source(python, source):
    # pip's build backend creates artifacts beside the source it installs.
    with TemporaryDirectory(prefix="etf-backtest-build-") as temporary:
        staged = Path(temporary)
        shutil.copy2(source / "pyproject.toml", staged)
        license_file = source / "LICENSE"
        if license_file.is_file():
            shutil.copy2(license_file, staged)
        package = Path("src") / "etf_backtest"
        (staged / package).mkdir(parents=True)
        for module in (source / package).glob("*.py"):
            shutil.copy2(module, staged / package / module.name)
        subprocess.run([str(python), "-m", "pip", "install", str(staged)], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "environment", type=Path,
        help="New virtual environment directory. Requires Python 3.11 or newer.",
    )
    destination = parser.parse_args(argv).environment.resolve()
    if sys.version_info < (3, 11):
        print("Use Python 3.11 or newer to set up the ETF runtime.", file=sys.stderr)
        return 1
    if destination.exists():
        print(f"Environment directory already exists: {destination}", file=sys.stderr)
        return 1

    skill = Path(__file__).resolve().parents[1]
    source = skill / "assets" / "engine"
    if not source.is_dir() and len(skill.parents) > 2:
        source = skill.parents[2]
    if not (
        (source / "pyproject.toml").is_file()
        and (source / "src" / "etf_backtest" / "__init__.py").is_file()
    ):
        print(
            "Local engine source is missing. Use the complete Skill bundle "
            "or run setup_runtime.py from the project checkout.",
            file=sys.stderr,
        )
        return 1
    if source.is_relative_to(destination):
        print("Choose an environment directory outside the engine source's parents.", file=sys.stderr)
        return 1

    created = False
    try:
        destination.mkdir(parents=True, exist_ok=False)
        created = True
        venv.EnvBuilder(with_pip=True).create(destination)
        python = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        _install_local_source(python, source)
    except (OSError, subprocess.CalledProcessError) as error:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        print(f"ETF runtime setup failed: {error}", file=sys.stderr)
        return 1

    print(f"Runtime ready at {python}")
    print("Run the synthetic demo in a fresh output directory:")
    print(f'\"{python}\" -m etf_backtest.demo \"my-first-demo\"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
