"""Evidence from plain git checkouts: `<root>/<repo>` at its HEAD.

Config: `evidence: [{adapter: checkout, root: ../sources}]` (root relative to the owner config).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class Checkout:
    def __init__(self, conf: dict, *, base: Path):
        self.root = (base / conf.get("root", ".")).resolve()

    def source_dir(self, repo: str) -> Path:
        return self.root / repo

    def source_version(self, repo: str) -> str | None:
        try:
            return subprocess.run(["git", "-C", str(self.source_dir(repo)), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    def query(self, repo: str, question: str, budget: int = 1500) -> str:
        raise NotImplementedError("the checkout adapter has no search; read the source directly")


def factory(conf: dict, *, base: Path) -> Checkout:
    return Checkout(conf, base=base)
