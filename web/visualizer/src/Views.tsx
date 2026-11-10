import { useRef } from 'react';
import type { Artifact, SavedView } from './types';

type Props = {
  views: SavedView[]; onOpen: (id: string) => void; onImport: (a: Artifact) => void; onError: (m: string) => void;
};

export function Views({ views, onOpen, onImport, onError }: Props) {
  const file = useRef<HTMLInputElement>(null);
  const read = async (f: File) => {
    try {
      const a = JSON.parse(await f.text()) as Artifact;
      if (a?.artifactVersion !== 1 || !a.spec) throw new Error('not a saved view of version 1');
      onImport(a);
    } catch (e) {
      onError(`Cannot open ${f.name}: ${(e as Error).message}`);
    }
  };
  return (
    <fieldset aria-label="saved views"><legend>Saved views</legend>
      {views.length === 0 && <p className="muted">None yet.</p>}
      <ul className="hits">
        {views.map((v) => (
          <li key={v.id}>
            {v.error ? <span className="muted">{v.id}: {v.error}</span>
              : <button type="button" onClick={() => onOpen(v.id)}>{v.title ?? v.id}</button>}
            {v.revision !== null && <span className="tag">rev {v.revision}</span>}
          </li>
        ))}
      </ul>
      <button type="button" onClick={() => file.current?.click()}>Open a JSON file…</button>
      <input ref={file} type="file" accept="application/json,.json" hidden aria-label="view file"
             onChange={(e) => { const f = e.target.files?.[0]; if (f) void read(f); e.target.value = ''; }} />
    </fieldset>
  );
}

export function slug(title: string): string {
  const s = title.normalize('NFKD').replace(/[^\w.-]+/g, '-').replace(/^[^A-Za-z0-9]+|-+$/g, '').slice(0, 64);
  return s || 'view';
}

export function download(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 1) + '\n'], { type: 'application/json' }));
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}
