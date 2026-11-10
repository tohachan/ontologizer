// Display helpers. Display text never takes part in identity: IRIs stay whole everywhere else.

const PREFIXES: [string, string][] = [
  ['http://purl.org/nemo/gufo#', 'gufo:'],
  ['urn:ontologizer:kit:', 'ok:'],
  ['http://www.w3.org/2000/01/rdf-schema#', 'rdfs:'],
  ['http://www.w3.org/1999/02/22-rdf-syntax-ns#', 'rdf:'],
  ['http://www.w3.org/2002/07/owl#', 'owl:'],
  ['http://www.w3.org/2004/02/skos/core#', 'skos:'],
];

export function short(iri: string): string {
  for (const [ns, p] of PREFIXES) if (iri.startsWith(ns)) return p + iri.slice(ns.length);
  return iri;
}

export function localName(iri: string): string {
  const s = short(iri);
  const cut = Math.max(s.lastIndexOf('#'), s.lastIndexOf('/'), s.lastIndexOf(':'));
  return cut >= 0 && cut < s.length - 1 ? s.slice(cut + 1) : s;
}

// Status is shown as a word and a symbol, never by colour alone.
const STATUS: Record<string, string> = { asserted: '● asserted', inferred: '◇ inferred', hypothesis: '◌ hypothesis',
                                          disputed: '⚠ disputed' };
export function statusText(status: string | null | undefined): string {
  return status ? (STATUS[status] ?? status) : '';
}

export function edgeText(kind: string, type: string, label: string | null): string {
  if (kind === 'summary') return `⋯ ${label ?? 'path'}`;
  const name = label ?? localName(type);
  return kind === 'kit-mapping' ? `↦ ${name}` : kind === 'class-inheritance' ? `▷ ${name}` : name;
}
