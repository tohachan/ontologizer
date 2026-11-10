"""What make viz-up generates for the container, from a temporary hub. No Docker."""

from __future__ import annotations

import yaml

from conftest import make_hub, make_study
from ontologizer.adapters.visualization.deploy import generate


def test_registry_and_mounts(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study", "ghost": "../nowhere", "bad name": "../study"})
    make_study(tmp_path)
    out = tmp_path / "out"
    notes = generate(hub, out, "http://engine:8080")
    reg = yaml.safe_load((out / "ontologizer.local.yaml").read_text())
    assert reg == {"projects": {"study": "/owners/study"}, "store": {"url": "http://engine:8080"}}
    vols = yaml.safe_load((out / "compose.mounts.yaml").read_text())["services"]["visualizer"]["volumes"]
    root = (tmp_path / "study").resolve()
    assert vols == [f"{root}:/owners/study:ro", f"{root / 'views'}:/owners/study/views:rw",
                    f"{(out / 'ontologizer.local.yaml').resolve()}:/app/ontologizer.local.yaml:ro"]
    assert (root / "views").is_dir()
    assert any("ghost" in n for n in notes) and any("bad name" in n for n in notes)
    assert str(tmp_path / "hub") not in (out / "compose.mounts.yaml").read_text()


def test_views_outside_the_owner_root_are_refused(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study"})
    study = make_study(tmp_path)
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["paths"] = {"views": "../elsewhere"}
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    notes = generate(hub, tmp_path / "out", "http://engine:8080")
    assert any("outside" in n for n in notes)
    assert yaml.safe_load((tmp_path / "out" / "ontologizer.local.yaml").read_text())["projects"] == {}
    assert not (tmp_path / "elsewhere").exists()


def test_the_planner_and_its_key_reach_the_container_without_the_key_in_any_file(tmp_path, monkeypatch):
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    (hub / "ontologizer.local.yaml").write_text(yaml.safe_dump({"projects": {"study": "../study"}, "visualizer": {
        "planner": {"adapter": "chat", "model": "m", "env": "MY_KEY"}}}))
    (hub / ".env").write_text("MY_KEY=sk-not-in-files\n")
    out = tmp_path / "out"
    generate(hub, out, "http://engine:8080")
    reg = yaml.safe_load((out / "ontologizer.local.yaml").read_text())
    assert reg["visualizer"] == {"planner": {"adapter": "chat", "model": "m", "env": "MY_KEY"}}
    svc = yaml.safe_load((out / "compose.mounts.yaml").read_text())["services"]["visualizer"]
    assert svc["environment"] == ["MY_KEY"]
    assert f"{(hub / '.env').resolve()}:/app/.env:ro" in svc["volumes"]
    for f in out.iterdir():
        assert "sk-not-in-files" not in f.read_text()


def test_without_a_planner_no_key_is_passed(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    generate(hub, tmp_path / "out", "http://engine:8080")
    svc = yaml.safe_load((tmp_path / "out" / "compose.mounts.yaml").read_text())["services"]["visualizer"]
    assert "environment" not in svc and not any(v.endswith("/app/.env:ro") for v in svc["volumes"])
