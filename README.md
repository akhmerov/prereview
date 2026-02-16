# prereview

Generate local, static HTML review previews for agent-generated code changes.

Repository: `https://gitlab.kwant-project.org/anton-akhmerov/prereview.git`

## Quick workflow

```bash
prereview
```

This creates a `review/` workspace with:

- `review/review.html` (open this report in the browser)
- `review/review-input.txt` (agent-facing context)
- `review/review-notes.jsonl` (agent-authored notes)

After updating `review/review-notes.jsonl`, run `prereview` again to rebuild the report.

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
