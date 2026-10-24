// Tiny Node module-customization-hook used ONLY to run
// scripts/sync_proof_subscriber.ts directly with plain `node`, without
// adding a bundler dependency. The generated SpacetimeDB bindings in
// src/spacetime_bindings/**.ts use extensionless relative imports (the
// normal TS/bundler convention — they are consumed by Vite everywhere
// else in this app), which Node's native ESM loader cannot resolve on its
// own. This hook retries a failed resolution by appending `.ts` before
// giving up. It does not transform syntax; `--experimental-strip-types`
// (passed alongside `--import` on the command line) does that part.
export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (err) {
    if (err?.code === 'ERR_MODULE_NOT_FOUND' && !specifier.endsWith('.ts')) {
      return nextResolve(`${specifier}.ts`, context);
    }
    throw err;
  }
}
