"""Evidence from git: `<root>/<repo>` is a checkout at its HEAD, or a directory committed inside a
larger repository (the owner's own, for instance) at the last commit that touched it.

Config: `evidence: [{adapter: checkout, root: ../sources}]` (root relative to the owner config).
"""

from __future__ import annotations

from pathlib import Path

from ontologizer.ports.evidence import git_version


class Checkout:
    def __init__(self, conf: dict, *, base: Path):
        self.root = (base / conf.get("root", ".")).resolve()

    def source_dir(self, repo: str) -> Path:
        return self.root / repo

    def source_version(self, repo: str) -> str | None:
        return git_version(self.source_dir(repo))

    def query(self, repo: str, question: str, budget: int = 1500) -> str:
        raise NotImplementedError("the checkout adapter has no search; read the source directly")


def factory(conf: dict, *, base: Path) -> Checkout:
    return Checkout(conf, base=base)
