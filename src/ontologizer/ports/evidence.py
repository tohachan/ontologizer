"""Evidence port: where claims about a project come from, and how they are re-read."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class EvidenceSource(Protocol):
    def source_version(self, repo: str) -> str | None:
        """Commit (or other version) the evidence for `repo` was read at."""
        ...

    def source_dir(self, repo: str) -> Path:
        """Checkout of `repo` at `source_version(repo)`."""
        ...

    def query(self, repo: str, question: str, budget: int = 1500) -> str: ...


def line_ranges(lines: str) -> list[tuple[int, int]] | None:
    """"10-40", "12" or several joined by commas ("10-40,88") as (first, last) pairs; None when malformed."""
    out = []
    for part in str(lines).split(","):
        a, _, b = part.strip().partition("-")
        try:
            lo, hi = int(a), int(b or a)
        except ValueError:
            return None
        out.append((lo, hi))
    return out


def read_lines(root: Path, path: str, lines: str | None, pad: int = 0, limit: int = 400) -> str | None:
    """The cited lines of `root/path` ("10-40", "12", "10-40,88" or None for the head of the file).
    None when the file or any of the lines do not exist."""
    f = (root / path).resolve()
    try:
        f.relative_to(root.resolve())
    except ValueError:
        return None
    if not f.is_file():
        return None
    return slice_lines(f.read_text(errors="replace").splitlines(), lines, pad, limit)


def slice_lines(text: list[str], lines: str | None, pad: int = 0, limit: int = 400) -> str | None:
    if not lines:
        return "\n".join(f"{n}: {text[n - 1]}" for n in range(1, min(len(text), limit) + 1))
    ranges = line_ranges(lines)
    if ranges is None:
        return None
    parts = []
    for lo, hi in ranges:
        if lo < 1 or lo > len(text) or hi < lo:
            return None
        lo, hi = max(1, lo - pad), min(len(text), hi + pad)
        hi = min(hi, lo + limit - 1)
        parts.append("\n".join(f"{n}: {text[n - 1]}" for n in range(lo, hi + 1)))
    return "\n".join(parts)
