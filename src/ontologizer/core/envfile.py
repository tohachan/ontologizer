"""Secrets from dotenv files, with the same worktree fallback as `scripts/oo`.

A linked git worktree has no copy of the gitignored `.env`: each file asked for is tried where it is named,
then at the same place in the main checkout. Values are returned to the caller only; nothing here prints,
logs or raises with a value in it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ontologizer.core.config import _main_checkout_twin


def candidates(files: list[Path]) -> list[Path]:
    """Each file, then its twin in the main checkout when it lies inside a linked worktree; no duplicates."""
    out: list[Path] = []
    for f in files:
        f = Path(f).resolve()
        found = [f]
        if f.parent.is_dir():
            twin = _main_checkout_twin(f.parent)
            if twin is not None:
                found.append((twin / f.name).resolve())
        out += [x for x in found if x not in out]
    return out


def read_dotenv(name: str, path: Path) -> str | None:
    """`name` from one dotenv file (`NAME=value`, `export NAME=value`, quoted or not; the last one wins)."""
    if not path.is_file():
        return None
    val = None
    pat = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}=(.*)$")
    for line in path.read_text().splitlines():
        m = pat.match(line)
        if m:
            v = m.group(1).strip().rstrip("\r")
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            val = v or None
    return val


def read_key(name: str, files: list[Path], *, environ_first: bool = True) -> str | None:
    """The environment variable `name` (first, unless `environ_first` is false: then last, as `scripts/oo`
    does), else the first of `files` (and their main-checkout twins) that sets it."""
    if environ_first and os.environ.get(name):
        return os.environ[name]
    for f in candidates(files):
        v = read_dotenv(name, f)
        if v:
            return v
    return os.environ.get(name) or None
