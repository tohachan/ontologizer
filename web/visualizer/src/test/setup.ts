// jsdom lacks what React Flow measures with.
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

class RO { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
class DM { m22 = 1; constructor() {} }
(globalThis as unknown as { DOMMatrixReadOnly: typeof DM }).DOMMatrixReadOnly = DM;
afterEach(() => cleanup());
