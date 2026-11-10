"""Owners, catalog and scope of a view. Hermetic: a temporary hub with two owners, no store."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ontologizer.core.views.scope import Owners, OutOfScope, UnknownOwner, catalog, resolve
from conftest import make_hub, make_study

NS = "urn:ontologizer:graph:"


def _other(tmp_path: Path) -> Path:
    d = tmp_path / "other" / "onto"
    d.mkdir(parents=True)
    (d / "ontologizer.yaml").write_text(yaml.safe_dump({
        "hub": "../../hub", "project": "other-project",
        "graphs": [{"pattern": "projects/other", "mode": "research", "role": "project"}],
        "paths": {"views": "saved/views"}}))
    return d


@pytest.fixture
def owners(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study", "other": "../other/onto"})
    make_study(tmp_path)
    _other(tmp_path)
    return Owners(hub)


STORE = [NS + p for p in (
    "upper/gufo", "kit/core", "shared/domain", "shared/task", "shared/variation",
    "projects/a", "projects/b", "projects/other", "projects/a@0.1", "shared/domain@0.1", "nobody/knows")] + [
    "urn:ontologizer-staging:0123abcd", "urn:elsewhere:graph"]


def test_owners_are_the_registry(owners):
    assert owners.names() == ["other", "study"]
    with pytest.raises(UnknownOwner):
        owners.config("missing")


def test_owner_config_uses_this_hub_not_the_one_its_hub_line_names(owners, tmp_path):
    cfg = owners.config("study")
    assert cfg.kit_root == (tmp_path / "hub").resolve()
    assert cfg.project == "study"


def test_views_dir_comes_from_paths_views(owners, tmp_path):
    assert owners.views_dir("study") == (tmp_path / "study" / "views").resolve()
    assert owners.views_dir("other") == (tmp_path / "other" / "onto" / "saved" / "views").resolve()


def test_catalog_lists_only_kit_graphs_with_a_policy(owners):
    cat = catalog(owners.config("study"), STORE)
    paths = [g.path for g in cat]
    assert paths == sorted(paths)
    assert "nobody/knows" not in paths
    assert not any("staging" in g.iri or "elsewhere" in g.iri for g in cat)
    by = {g.path: g for g in cat}
    assert by["projects/a"].owner == "study" and by["projects/a"].level == "application"
    assert by["projects/other"].owner == "other-project"
    assert by["shared/domain"].level == "domain" and by["shared/variation"].level == "variation"
    assert by["upper/gufo"].owner is None and by["upper/gufo"].level is None
    rel = by["projects/a@0.1"]
    assert rel.release == "0.1" and rel.base == "projects/a" and rel.level == "application" and rel.owner == "study"
    assert by["projects/a"].release is None


def test_glob_primary_expands_to_owned_working_graphs_only(owners):
    cfg = owners.config("study")
    s = resolve(cfg, catalog(cfg, STORE), primary=["projects/*"], context=["upper/gufo", "kit/core"])
    assert [g.path for g in s.primary] == ["projects/a", "projects/b"]
    assert [g.path for g in s.context] == ["kit/core", "upper/gufo"]
    assert s.iris == tuple(sorted(NS + p for p in ("projects/a", "projects/b", "kit/core", "upper/gufo")))


def test_primary_of_another_owner_is_refused(owners):
    cfg = owners.config("study")
    with pytest.raises(OutOfScope, match="projects/other"):
        resolve(cfg, catalog(cfg, STORE), primary=["projects/other"], context=[])


def test_other_owners_graph_may_be_context_when_listed(owners):
    cfg = owners.config("study")
    s = resolve(cfg, catalog(cfg, STORE), primary=["projects/a"], context=["projects/other"])
    assert [g.path for g in s.context] == ["projects/other"]


@pytest.mark.parametrize("primary,context", [
    (["projects/a@0.1"], ["projects/b"]),
    (["projects/a"], ["shared/domain@0.1"]),
    (["projects/a", "projects/a@0.1"], []),
], ids=["release-with-working-context", "working-with-release-context", "mixed-primary"])
def test_working_and_release_never_mix(owners, primary, context):
    cfg = owners.config("study")
    with pytest.raises(OutOfScope, match="release"):
        resolve(cfg, catalog(cfg, STORE), primary=primary, context=context)


def test_release_scope_takes_kit_graphs_and_releases_as_context(owners):
    cfg = owners.config("study")
    s = resolve(cfg, catalog(cfg, STORE), primary=["projects/a@0.1"], context=["shared/domain@0.1", "upper/gufo"])
    assert [g.path for g in s.primary] == ["projects/a@0.1"]
    assert s.release


def test_glob_without_a_version_does_not_reach_releases(owners):
    cfg = owners.config("study")
    s = resolve(cfg, catalog(cfg, STORE), primary=["projects/a*"], context=[])
    assert [g.path for g in s.primary] == ["projects/a"]


@pytest.mark.parametrize("primary,context", [
    ([], []), (["projects/zzz*"], []), (["projects/a"], ["nobody/knows"]), (["projects/a"], ["projects/a"]),
    (["../../etc/passwd"], []), (["projects/a"], ["urn:ontologizer-staging:0123abcd"]),
], ids=["empty", "glob-matches-nothing", "unknown-context", "both-primary-and-context", "path-escape", "staging"])
def test_bad_scopes_are_refused(owners, primary, context):
    cfg = owners.config("study")
    with pytest.raises(OutOfScope):
        resolve(cfg, catalog(cfg, STORE), primary=primary, context=context)


def test_a_literal_owned_graph_missing_from_the_store_stays_in_scope(owners):
    """A saved view may name a graph that is gone; the reader reports it as missing."""
    cfg = owners.config("study")
    s = resolve(cfg, catalog(cfg, STORE), primary=["shared/task"], context=[])
    s2 = resolve(cfg, catalog(cfg, [i for i in STORE if not i.endswith("shared/task")]), primary=["shared/task"], context=[])
    assert [g.path for g in s.primary] == [g.path for g in s2.primary] == ["shared/task"]


def test_a_worktree_without_its_own_registry_uses_the_main_checkouts(tmp_path):
    import subprocess

    def git(*a, cwd):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                       cwd=cwd, check=True, capture_output=True)

    main = tmp_path / "kit"
    main.mkdir()
    (main / "ontologizer.yaml").write_text("namespace: 'urn:ontologizer:'\n")
    git("init", "-q", cwd=main)
    git("add", "ontologizer.yaml", cwd=main)
    git("commit", "-qm", "hub", cwd=main)
    (main / "ontologizer.local.yaml").write_text("projects: {study: ../study}\n")   # gitignored in the kit
    git("worktree", "add", "-q", str(tmp_path / "wt"), cwd=main)
    assert Owners(tmp_path / "wt").names() == ["study"]
    (tmp_path / "wt" / "ontologizer.local.yaml").write_text("projects: {own: ../own}\n")
    assert Owners(tmp_path / "wt").names() == ["own"]
