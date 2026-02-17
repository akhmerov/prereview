# prereview

Generate local, static HTML review previews for agent-generated code changes.

## Problem it solves

AI agents can produce large diffs quickly, but raw patches are hard to review:

- context is scattered across hunks and files
- intent ("what changed" and "why") is often missing
- review comments from iterative agent runs are not captured in one stable artifact

`prereview` turns this into a repeatable local workflow:

- build one deterministic review workspace from the current diff
- let an agent attach structured notes to stable anchors
- regenerate a single static HTML report for human review

This reduces review friction and makes agent-driven change review auditable and consistent.

Repository: `https://gitlab.kwant-project.org/anton-akhmerov/prereview.git`

## Quick workflow

```bash
prereview
```

This creates a `prereview/` workspace with:

- `prereview/review.html` (open this report in the browser)
- `prereview/review-input.txt` (agent-facing context)
- `prereview/review-notes.jsonl` (agent-authored notes)

After updating `prereview/review-notes.jsonl`, run `prereview` again to rebuild the report.

To remove artifacts and unregister local git excludes:

```bash
prereview clean
```

## Install / run

### `uv` tool install (global)

```bash
uv tool install git+https://gitlab.kwant-project.org/anton-akhmerov/prereview.git
prereview --help
```

### `uvx` (ephemeral)

```bash
uvx --from git+https://gitlab.kwant-project.org/anton-akhmerov/prereview.git prereview --help
```

### `pixi global` install (global)

```bash
pixi global install --git https://gitlab.kwant-project.org/anton-akhmerov/prereview.git --expose prereview
prereview --help
```

### `pixi exec` (ephemeral)

```bash
pixi exec --spec uv uvx --from git+https://gitlab.kwant-project.org/anton-akhmerov/prereview.git prereview --help
```

## Install bundled skill

After installing `prereview`, install the bundled `prereview-pipeline` skill
for Codex, Claude, or Copilot.

Project-local install (recommended, run from project root):

```bash
prereview install-skill --agent codex --local
```

Local targets by agent:

- `codex` -> `.codex/skills/prereview-pipeline`
- `claude` -> `.claude/skills/prereview-pipeline`
- `copilot` -> `.github/skills/prereview-pipeline`

Global/user-level install (explicit target dir):

```bash
prereview install-skill --agent codex --target-dir ~/.codex/skills
```

You can also omit both `--local` and `--target-dir` and the CLI will prompt for
a destination folder. Use `--force` to overwrite an existing installation.
