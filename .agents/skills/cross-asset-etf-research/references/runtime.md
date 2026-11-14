# Set up and invoke the local engine

Use Python 3.11 or newer. This release was tested with Python 3.14. Reuse an environment already installed for this copy of the Skill, typically `.venv/bin/python` on macOS or Linux and `.venv/Scripts/python.exe` on Windows. Check that `"<environment-python>" -m etf_backtest compare --help` includes `--fee-bp` and `--verify-us-calendar`. If it does not, set up a fresh environment from this release. Use the exact Python path for every run.

When installation is needed, use the included setup script from the Skill directory:

```bash
"<python-3.11-or-newer>" "<skill-directory>/scripts/setup_runtime.py" "<new-environment-directory>"
```

The setup script installs the local source in `assets/engine` from a portable bundle. When invoked from the source checkout, it validates the adjacent project and installs that local source. It creates a fresh virtual environment and prints the exact Python path to use afterward. On macOS and Linux this is `<environment>/bin/python`, and on Windows it is `<environment>/Scripts/python.exe`.

Initial installation needs access to a package index or a local directory of compatible wheels. For offline installation, prepare wheels for the selected Python and operating system, including the build requirements and all dependencies from `pyproject.toml`. Then tell pip to use that directory. On macOS or Linux, run:

```bash
PIP_NO_INDEX=1 PIP_FIND_LINKS="file:///absolute/path/to/wheelhouse" \
  "<python-3.11-or-newer>" "<skill-directory>/scripts/setup_runtime.py" "<new-environment-directory>"
```

Use a file URL for the wheel directory, with spaces encoded as `%20`. On Windows, set the same environment variables before invoking the setup script. After installation, the synthetic demo generates its own data locally:

```bash
"<environment-python>" -m etf_backtest.demo "<new-demo-folder>"
```

Use the same environment for `compare` and original replay. An existing output directory requires a new name. If setup reports missing engine source, obtain the complete locally built bundle or use the source checkout. If dependencies are unavailable, report the installation error and keep execution pending. Saved tables and the bundled case study can still be read.

## Source checkout and portable bundle

The source checkout maintains one engine under `src/etf_backtest`. Build a copy of the Skill with that engine and the archived case-study explanation using:

```bash
python tools/build_skill_bundle.py "<new-bundle-parent>"
```

The result contains `cross-asset-etf-research/` and `cross-asset-etf-research.zip`. It includes local engine source and reference materials. Historical price snapshots and development weight files remain separate user inputs. Unzip the complete folder when using the zip.

For a project-local installation, place the complete `cross-asset-etf-research` folder under that project's `.agents/skills/`. Codex's official local Skill locations and discovery behavior are documented in [Build skills](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills). Invocation depends on the host's available Python and shell access.
