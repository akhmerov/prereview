from __future__ import annotations

import shutil
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

SKILL_NAME = "prereview-pipeline"
AGENT_CHOICES = ("codex", "claude", "copilot")

_LOCAL_SKILLS_ROOT = {
    "codex": Path(".codex/skills"),
    "claude": Path(".claude/skills"),
    "copilot": Path(".github/skills"),
}


def local_target_root(agent: str, *, project_root: Path | None = None) -> Path:
    if agent not in _LOCAL_SKILLS_ROOT:
        raise ValueError(f"Unsupported agent: {agent}")
    root = project_root if project_root is not None else Path.cwd()
    return (root / _LOCAL_SKILLS_ROOT[agent]).resolve()


def _copy_resource_tree(source: Traversable, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_dir():
            _copy_resource_tree(entry, target)
            continue
        with entry.open("rb") as handle:
            target.write_bytes(handle.read())


def install_packaged_skill(*, target_root: Path, force: bool = False) -> Path:
    source_root = (
        resources.files("prereview").joinpath("skill_assets").joinpath(SKILL_NAME)
    )
    if not source_root.is_dir():
        raise FileNotFoundError(
            f"Packaged skill assets for '{SKILL_NAME}' were not found."
        )

    target_root = target_root.expanduser().resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    install_path = target_root / SKILL_NAME
    if install_path.exists():
        if not force:
            raise FileExistsError(f"Skill destination already exists: {install_path}")
        if install_path.is_dir():
            shutil.rmtree(install_path)
        else:
            install_path.unlink()

    _copy_resource_tree(source_root, install_path)
    return install_path
