# prereview

Generate local, static HTML review previews for agent-generated code changes.

Repository: `https://gitlab.kwant-project.org/anton-akhmerov/prereview.git`

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
