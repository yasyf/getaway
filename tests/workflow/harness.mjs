// Mirrors the production Workflow wrapper: strip the `export const meta` literal, then evaluate the
// remaining body as an async function whose only free names are the host globals.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
export const PLAN_TRIP = resolve(HERE, '..', '..', 'skills', 'getaway', 'plan-trip.js');

const HOST_GLOBALS = ['args', 'agent', 'pipeline', 'parallel', 'phase', 'log'];

function stripMeta(src) {
  const start = src.indexOf('export const meta');
  if (start < 0) throw new Error('harness: no `export const meta` statement found');
  let i = src.indexOf('{', start);
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) { i++; break; }
  }
  while (i < src.length && src[i] !== ';') i++;
  return src.slice(0, start) + src.slice(i + 1);
}

export function loadWorkflow(path = PLAN_TRIP) {
  const body = stripMeta(readFileSync(path, 'utf8'));
  return new Function(...HOST_GLOBALS, `return (async () => {\n${body}\n})();`);
}

// `script` maps a label (exact) or a `prefix:*` wildcard to a canned payload or a
// `(opts, prompt) => payload` factory. Every call is recorded verbatim.
export function makeHarness(script) {
  const calls = [];
  const phases = [];
  const logs = [];

  const lookup = (label) => {
    if (label in script) return script[label];
    for (const key of Object.keys(script)) {
      if (key.endsWith('*') && label.startsWith(key.slice(0, -1))) return script[key];
    }
    throw new Error(`harness: no scripted agent for label ${label}`);
  };

  const agent = async (prompt, opts) => {
    calls.push({ prompt, opts });
    const entry = lookup(opts.label);
    const payload = typeof entry === 'function' ? entry(opts, prompt) : entry;
    // Schema-required enforcement applies to object payloads only: production schema-forcing has
    // been observed to let a raw prose string (and parallel-failure nulls) through to the caller.
    if (payload !== null && typeof payload === 'object') {
      for (const key of (opts.schema && opts.schema.required) || []) {
        if (!(key in payload)) {
          throw new Error(`harness: agent ${opts.label} payload missing required key ${key}`);
        }
      }
    }
    return payload;
  };

  // Production pipeline()/parallel() resolve a failed branch to null, never a rejection.
  const settle = (p) => Promise.resolve(p).then((v) => v, () => null);
  const pipeline = async (items, fn) => Promise.all(items.map((item, i) => settle(fn(item, i))));
  const parallel = async (branches) => Promise.all(branches.map((b) => settle(typeof b === 'function' ? b() : b)));
  const phase = (name) => { phases.push(name); };
  const log = (msg) => { logs.push(msg); };

  const labels = () => calls.map((c) => c.opts.label);
  const called = (label) => labels().includes(label);
  const withPrefix = (prefix) => calls.filter((c) => c.opts.label.startsWith(prefix));

  return { agent, pipeline, parallel, phase, log, calls, phases, logs, labels, called, withPrefix };
}

export async function runWorkflow(args, script, path = PLAN_TRIP) {
  const fn = loadWorkflow(path);
  const h = makeHarness(script);
  const result = await fn(args, h.agent, h.pipeline, h.parallel, h.phase, h.log);
  return { result, ...h };
}
