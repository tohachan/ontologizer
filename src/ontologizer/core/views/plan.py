"""Planning a view from a request: the catalog it may use and the checks its proposal must pass.

The planner sees only the catalog of the current scope and may change only the view: the scope,
id and title stay the current view's. Its proposal goes through the same schema and strict checks
as any other new spec; one repair attempt is allowed, with the errors, before it fails. Matching a
request to concepts is the planner's job, because labels in the store may be in another language
than the request.
"""

from __future__ import annotations

from dataclasses import replace

from ontologizer.core.config import Config
from ontologizer.core.views.models import SourceModel
from ontologizer.core.views.project import DisplayLimitExceeded, compile_view
from ontologizer.core.views.scope import GraphEntry, Scope
from ontologizer.core.views.spec import SpecError, check, parse
from ontologizer.ports.view_planner import (CONTRACT_VERSION, CatalogEntity, CatalogRelationship, PlannerError,
                                            PlanRequest, PlanResult, ViewPlanner)

MAX_ENTITIES = 2000
MAX_RELATIONSHIPS = 5000


class CatalogTooLarge(ValueError):
    pass


class PlanInvalid(ValueError):
    def __init__(self, issues):
        super().__init__("the planner's proposal is invalid")
        self.issues = issues


def catalog_of(cfg: Config, scope: Scope, model: SourceModel) -> tuple[tuple[CatalogEntity, ...], tuple[CatalogRelationship, ...]]:
    primary = {g.iri for g in scope.primary}
    ents = [e for e in model.entities.values() if set(e.graphs) & primary]
    if len(ents) > MAX_ENTITIES:
        raise CatalogTooLarge(f"{len(ents)} concepts exceed the planner's catalog of {MAX_ENTITIES}; select fewer graphs")
    iris = {e.iri for e in ents}
    rels = [r for r in model.relationships if r.target and r.source in iris and r.target in iris]
    if len(rels) > MAX_RELATIONSHIPS:
        raise CatalogTooLarge(f"{len(rels)} relationships exceed the planner's catalog of {MAX_RELATIONSHIPS}")
    entities = tuple(CatalogEntity(
        iri=e.iri, labels=tuple((l.value, l.language) for l in e.labels if l.prop in ("pref", "rdfs", "alt")),
        definition=next((d.value for d in e.definitions if d.language in ("en", None)), None) or
        (e.definitions[0].value if e.definitions else None),
        level=e.level, graph=cfg.graph_path(sorted(set(e.graphs) & primary)[0])) for e in ents)
    relationships = tuple(CatalogRelationship(r.id, r.kind, r.source, r.target, r.type) for r in rels)
    return entities, relationships


def plan_view(planner: ViewPlanner, cfg: Config, graphs: list[GraphEntry], scope: Scope, model: SourceModel,
              request: str, current: dict, languages: tuple[str, ...], levels: tuple[str, ...]):
    """Ask the planner, check its proposal, allow one repair. Returns (PlanResult with a checked spec
    or candidates, repaired: bool)."""
    entities, relationships = catalog_of(cfg, scope, model)
    req = PlanRequest(CONTRACT_VERSION, request, languages, entities, relationships, levels, current)
    repaired = False
    for attempt in range(2):
        result = planner.plan(req)
        if not isinstance(result, PlanResult) or result.contract_version != CONTRACT_VERSION:
            raise PlannerError("the planner answered another contract version")
        if result.spec is None:
            if not result.candidates:
                raise PlannerError("the planner proposed nothing")
            known = {e.iri for e in entities}
            cands = tuple({"name": str(c.get("name", ""))[:200], "iris": [i for i in c.get("iris", []) if i in known][:20]}
                          for c in result.candidates[:20])
            return replace(result, candidates=cands), repaired
        issues = _issues(result.spec, current, cfg, graphs, model)
        if not issues:
            return result, repaired
        if attempt == 0:
            repaired = True
            req = replace(req, errors=tuple(f"{i['path']}: {i['message']}" for i in issues)[:50])
    raise PlanInvalid(issues)


def _issues(proposal, current: dict, cfg, graphs, model) -> list[dict]:
    if not isinstance(proposal, dict):
        return [{"code": "schema", "path": "(root)", "message": "the proposal is not an object"}]
    fixed = [k for k in ("scope", "id", "title", "schemaVersion") if proposal.get(k) != current.get(k)]
    if fixed:
        return [{"code": "out-of-scope", "path": k, "message": "the planner may not change this"} for k in fixed]
    try:
        checked = check(parse(proposal), cfg, graphs, model, mode="strict")
        compile_view(checked.spec, checked.scope, model)       # a proposal that cannot be shown is not valid
    except SpecError as e:
        return [{"code": i.code, "path": i.path, "message": i.message} for i in e.issues]
    except DisplayLimitExceeded as e:
        return [{"code": "display-limit", "path": "presentation.maxNodes", "message": str(e)}]
    return []
