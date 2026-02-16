# Repository Guidelines

## Project Structure & Module Organization
- Core package: `src/prereview/`
  - `cli.py`: CLI entrypoint (`prereview`).
  - `prepare.py`, `diff_parser.py`, `validate.py`, `renderer.py`: main pipeline stages.
  - `review_io.py`, `annotations.py`, `models.py`, `util.py`: IO/schema/helpers.
  - `templates/review.html.j2`: HTML output template.
- Tests: `tests/test_pipeline.py` (end-to-end and unit-style coverage).
- Skills and assets: `skills/` and packaged copies under `src/prereview/skill_assets/`.
- Generated artifacts: `prereview/` (local run outputs; do not hand-edit generated JSON/HTML).

## Build, Test, and Development Commands
- `pixi run test`: run test suite (`pytest -q`).
- `pixi run -- prereview`: run default local pipeline against working tree.
- `pixi run -- prereview --help`: inspect CLI options.
- `pixi run build-conda`: build conda package artifact.
- `pixi run -e precommit precommit-run`: run lint/hooks (`ruff`, `yamllint`, `codespell`, etc.).

Example local flow:
1. `pixi run -- prereview`
2. Edit `prereview/review-notes.jsonl`
3. `pixi run -- prereview`

## Coding Style & Naming Conventions
- Python 3.11+; 4-space indentation; type hints required for public/internal functions.
- Prefer explicit, homogeneous data contracts in internal flows (avoid defensive branching once data is validated).
- Keep module/function names `snake_case`; classes `PascalCase`; constants `UPPER_SNAKE_CASE`.
- Keep comments minimal and explanatory (why, not what).

## Testing Guidelines
- Framework: `pytest`.
- Add/update tests in `tests/test_pipeline.py` for behavior changes.
- Test names: `test_<behavior>()`.
- Before committing: run `pixi run test` and pre-commit hooks in the `precommit` env.

## Commit & Pull Request Guidelines
- Follow concise, imperative commit subjects (e.g., `Refactor prereview internals to strict typed contracts`).
- Include trailer when using agent-generated changes:
  - `Generated-by: GPT-5.3 Codex`
- Prefer signed commits (`git commit -S`) when possible.
- In this workspace, signed commits may require escalated permissions so GPG can access the user keyring.
- Never run commits with `--no-verify`.
