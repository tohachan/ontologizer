"""Where a project's source is: the first evidence adapter of the owner config."""

from __future__ import annotations

from pathlib import Path

from ontologizer.core import plugins
from ontologizer.core.config import Config


def evidence_source(cfg: Config):
    entries = cfg.data.get("evidence") or []
    return plugins.load("evidence", entries[0], base=cfg.root) if entries else None


def source_info(cfg: Config, project: str, src=None) -> dict:
    src = src if src is not None else evidence_source(cfg)
    if src is None:
        return {"dir": None, "commit": None}
    try:
        d = Path(src.source_dir(project))
    except (KeyError, FileNotFoundError, OSError):
        d = None
    try:
        commit = src.source_version(project)
    except (KeyError, FileNotFoundError, OSError, RuntimeError):
        commit = None
    return {"dir": str(d) if d else None, "commit": commit}
