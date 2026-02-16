from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

LineType = Literal["context", "add", "del"]


@dataclass(slots=True)
class Line:
    line_id: str
    line_type: LineType
    content: str
    old_line: int | None
    new_line: int | None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["type"] = data.pop("line_type")
        return data


@dataclass(slots=True)
class Hunk:
    hunk_id: str
    stable_hunk_id: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: list[Line] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "hunk_id": self.hunk_id,
            "stable_hunk_id": self.stable_hunk_id,
            "old_start": self.old_start,
            "old_count": self.old_count,
            "new_start": self.new_start,
            "new_count": self.new_count,
            "header": self.header,
            "lines": [line.to_dict() for line in self.lines],
        }


@dataclass(slots=True)
class FilePatch:
    file_id: str
    path: str
    old_path: str | None
    new_path: str | None
    is_new: bool = False
    is_deleted: bool = False
    is_binary: bool = False
    is_rename: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(
            1 for hunk in self.hunks for line in hunk.lines if line.line_type == "add"
        )

    @property
    def deletions(self) -> int:
        return sum(
            1 for hunk in self.hunks for line in hunk.lines if line.line_type == "del"
        )

    @property
    def status(self) -> str:
        if self.is_binary:
            return "binary"
        if self.is_new or self.old_path is None:
            return "added"
        if self.is_deleted or self.new_path is None:
            return "deleted"
        if self.is_rename or self.old_path != self.new_path:
            return "renamed"
        return "modified"

    def to_dict(self) -> dict[str, object]:
        return {
            "file_id": self.file_id,
            "path": self.path,
            "old_path": self.old_path,
            "new_path": self.new_path,
            "status": self.status,
            "is_binary": self.is_binary,
            "additions": self.additions,
            "deletions": self.deletions,
            "hunks": [hunk.to_dict() for hunk in self.hunks],
        }
