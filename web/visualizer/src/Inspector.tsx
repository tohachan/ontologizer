import { useEffect, useState } from 'react';
import { api, ApiFailure } from './api';
import { edgeText, localName, short, statusText } from './format';
import type { ElementDetail, EntityDetail, Evidence, Term, Why } from './types';

type Props = { compileId: string; elementId: string | null };

export function Inspector({ compileId, elementId }: Props) {
  const [detail, setDetail] = useState<ElementDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDetail(null);
    setError(null);
    if (!elementId) return;
    let live = true;
    api.element(compileId, elementId).then(
      (d) => live && setDetail(d),
      (e: ApiFailure) => live && setError(e.code === 'compilation-expired' ? 'This diagram is no longer held by the service; compile again.' : e.message),
    );
    return () => { live = false; };
  }, [compileId, elementId]);

  if (!elementId) return <aside className="inspector" aria-label="inspector"><p className="muted">Select a card or an edge.</p></aside>;
  return (
    <aside className="inspector" aria-label="inspector">
      {error && <p role="alert">{error}</p>}
      {!detail && !error && <p className="muted">Loading…</p>}
      {detail?.kind === 'node' && (
        <>
          <h2>{detail.element.label}</h2>
          <WhyHere why={detail.why} />
          {detail.entities.map((e) => <EntityView key={e.iri} e={e} />)}
        </>
      )}
      {detail?.kind === 'edge' && (
        <>
          <h2>{edgeText(detail.element.kind, detail.element.type, detail.element.label)}</h2>
          <p className="muted">{detail.element.origin === 'summary' ? 'A path summary: a visual abbreviation, not an asserted relation.'
             : detail.element.origin === 'aggregate' ? 'Redirected into a collapsed group.' : 'Drawn from the relationships below.'}</p>
          {detail.relationships.map((r) => (
            <section key={r.id} className="block">
              <div><b>{r.kind}</b> {short(r.type)} {statusText(r.status)}</div>
              <div className="mono">{localName(r.source)} → {r.target ? localName(r.target) : '—'}</div>
              {r.record && <div className="mono small">record {r.record}</div>}
              <div className="mono small">graph {r.graph}</div>
              <EvidenceList items={r.evidence} />
            </section>
          ))}
        </>
      )}
    </aside>
  );
}

function WhyHere({ why }: { why: Why }) {
  const reasons: string[] = [];
  if (why.seed) reasons.push('a starting concept of this view');
  if (why.levelSeed) reasons.push('selected by its level');
  if (why.groups?.length) reasons.push(`a member of ${why.groups.join(', ')}`);
  if (why.summaryEnd) reasons.push('an end of a path summary');
  if (why.hop) reasons.push(`reached in ${why.hop} hop${why.hop > 1 ? 's' : ''}${why.via?.length ? ` through ${why.via.length} relationship(s)` : ''}`);
  if (why.context) reasons.push('from a context graph: shown, not expanded further');
  return (
    <section className="block" aria-label="why this element is here">
      <h3>Why it is here</h3>
      <ul>{(reasons.length ? reasons : [why.origin ?? 'part of the view']).map((r) => <li key={r}>{r}</li>)}</ul>
    </section>
  );
}

function EntityView({ e }: { e: EntityDetail }) {
  return (
    <section className="block">
      <div className="mono small">{e.iri}</div>
      <div>{e.level && <span className="tag">[{e.level}]</span>} {e.upperCategory && <span className="tag">{short(e.upperCategory)}</span>} {statusText(e.status)}</div>
      {e.owlStructure && <p className="muted">Has OWL structure the diagram does not show; the statements below are the full source.</p>}
      {e.definitions.map((d) => <p key={d.value + d.language}>{d.value}{d.language && <span className="tag">{d.language}</span>}</p>)}
      <h3>Labels</h3>
      <ul>{e.labels.map((l) => <li key={l.prop + l.value + l.language}>{l.value} <span className="tag">{l.prop}{l.language ? ` @${l.language}` : ''}</span></li>)}</ul>
      <EvidenceList items={e.evidence} />
      {e.candidates && e.candidates.length > 0 && (
        <>
          <h3>Candidate mappings (no target yet)</h3>
          <ul>{e.candidates.map((c) => (
            <li key={c.id}>{short(c.type)} → <i>{c.candidateLabel}</i>{c.candidateLevel && <span className="tag">[{c.candidateLevel}]</span>} {statusText(c.status)}
              <EvidenceList items={c.evidence} /></li>
          ))}</ul>
        </>
      )}
      {e.project && typeof e.project === 'object' && (
        <>
          <h3>Project</h3>
          <div className="mono small">{e.project.iri}{e.project.repository ? ` · ${e.project.repository}` : ''}{e.project.commit ? `@${e.project.commit.slice(0, 8)}` : ''}</div>
          {e.project.variation.length > 0 && (
            <table className="statements" aria-label="variation"><tbody>
              {e.project.variation.map((v) => <tr key={v.dimension + v.value}><td>{localName(v.dimension)}</td><td>{localName(v.value)} {statusText(v.status)}{v.note ? ` — ${v.note}` : ''}</td></tr>)}
            </tbody></table>
          )}
        </>
      )}
      <h3>Statements</h3>
      <table className="statements"><tbody>
        {e.statements.map((s, i) => <tr key={i}><td>{short(s.predicate)}</td><td><TermView t={s.object} /></td></tr>)}
      </tbody></table>
      {e.statementsOmitted > 0 && <p className="muted">{e.statementsOmitted} more statements not shown.</p>}
      <div className="mono small">graphs: {e.graphs.join(', ')}</div>
    </section>
  );
}

function TermView({ t }: { t: Term }) {
  if (t.kind === 'literal') return <span>"{t.value}"{t.language ? <span className="tag">@{t.language}</span> : null}</span>;
  return <span className="mono">{t.kind === 'blank' ? '_:' + t.value : short(t.value)}</span>;
}

function EvidenceList({ items }: { items: Evidence[] }) {
  if (!items.length) return null;
  return (
    <>
      <h3>Evidence</h3>
      <ul>{items.map((ev, i) => (
        <li key={i} className="mono small">{ev.repo ? ev.repo + ':' : ''}{ev.path}{ev.lines ? ':' + ev.lines : ''}{ev.commit ? '@' + ev.commit.slice(0, 8) : ''}{ev.kind ? ` (${ev.kind})` : ''}</li>
      ))}</ul>
    </>
  );
}
