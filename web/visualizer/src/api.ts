// Same-origin client of the visualizer API. Every failure is an ApiFailure: a failed call never
// looks like an empty result.
import type { Artifact, Catalog, Compilation, ElementDetail, Layout, OwnerCatalog, SavedView, SearchHit, ViewSpec } from './types';

const BASE = '/visualizer/api';

export class ApiFailure extends Error {
  constructor(public status: number, public code: string, message: string, public details: unknown = null) {
    super(message);
  }
}

async function call<T>(path: string, body?: unknown, signal?: AbortSignal, method = 'POST'): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, body === undefined ? { signal } : {
      method, signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new ApiFailure(0, 'network', 'the visualizer service does not answer');
  }
  let data: unknown;
  try {
    data = await res.json();
  } catch {
    throw new ApiFailure(res.status, 'bad-response', 'the service answered with something that is not JSON');
  }
  if (!res.ok) {
    const err = (data as { error?: { code: string; message: string; details: unknown } }).error;
    throw new ApiFailure(res.status, err?.code ?? 'error', err?.message ?? `HTTP ${res.status}`, err?.details);
  }
  if (data === null || typeof data !== 'object') throw new ApiFailure(res.status, 'bad-response', 'empty answer');
  return data as T;
}

export const api = {
  catalog: () => call<Catalog>('/catalog'),
  ownerCatalog: (owner: string) => call<OwnerCatalog>(`/catalog?owner=${encodeURIComponent(owner)}`),
  search: (owner: string, scope: ViewSpec['scope'], query: string, languages: string[], snapshotId?: string) =>
    call<{ snapshotId: string; results: SearchHit[]; total: number }>('/search',
      { owner, scope, query, languages, limit: 20, ...(snapshotId ? { snapshotId } : {}) }),
  compile: (owner: string, spec: ViewSpec, opts: { snapshotId?: string; mode?: 'strict' | 'tolerant'; compareTo?: string } = {}) =>
    call<Compilation>('/compile', { owner, spec, mode: opts.mode ?? 'strict',
      ...(opts.snapshotId ? { snapshotId: opts.snapshotId } : {}), ...(opts.compareTo ? { compareTo: opts.compareTo } : {}) }),
  plan: (owner: string, spec: ViewSpec, request: string, snapshotId?: string) =>
    call<{ snapshotId: string; spec: ViewSpec | null; summary: string; candidates: { name: string; iris: string[] }[]; repaired: boolean }>(
      '/plan', { owner, spec, request, ...(snapshotId ? { snapshotId } : {}) }),
  overview: (owner: string, version: string, languages: string[]) =>
    call<{ spec: ViewSpec; snapshotId: string; mappingTargets: Record<string, number> }>('/overview', { owner, version, languages }),
  views: (owner: string) => call<{ views: SavedView[] }>(`/views?owner=${encodeURIComponent(owner)}`),
  view: (owner: string, id: string) => call<Artifact>(`/views/${encodeURIComponent(id)}?owner=${encodeURIComponent(owner)}`),
  save: (owner: string, id: string, compileId: string, layout: Layout, expectedRevision: number | null) =>
    call<{ id: string; revision: number }>(`/views/${encodeURIComponent(id)}`, { owner, compileId, layout, expectedRevision }, undefined, 'PUT'),
  element: (compileId: string, elementId: string) =>
    call<ElementDetail>(`/compilations/${encodeURIComponent(compileId)}/elements/${encodeURIComponent(elementId)}`),
};
