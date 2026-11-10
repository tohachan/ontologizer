import { expect, it } from 'vitest';
import { elkConstructor } from '../elkModule';

class Fake { layout() { return Promise.resolve({ id: 'x' }); } }

it('finds the constructor however the bundler wraps the CommonJS module', () => {
  expect(elkConstructor(Fake)).toBe(Fake);
  expect(elkConstructor({ default: Fake })).toBe(Fake);
  expect(elkConstructor({ default: { default: Fake } })).toBe(Fake);
  expect(() => elkConstructor({ default: {} })).toThrow('no layout constructor');
  expect(typeof elkConstructor()).toBe('function');                 // the real module, in this environment
});
