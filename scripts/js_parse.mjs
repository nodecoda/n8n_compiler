// js_parse.mjs — strict JS parse bridge for n8n Code nodes.
//
// Two modes:
//   single (default): stdin is one JS source; prints
//     { ok: true,  ast: <ESTree AST with locations> }
//     { ok: false, errors: [{ line, col, message }] }
//   batch (--batch): stdin is JSON {"scripts": [...]}; prints
//     { results: [ { ok, ast, errors }, ... ] }
//
// Batch mode exists because Node process startup (~50ms) dominates single
// parses (~µs); one process parses all Code nodes of a workflow.
//
// n8n Code-node specifics:
//   - top-level `return` is legal (runOnceForAllItems) -> allowReturnOutsideFunction
//   - ESM `import`/`export` are legal in Code v2 -> sourceType 'module'
import { readFileSync } from 'node:fs';
import { parse } from 'acorn';

function parseOne(source) {
  try {
    const ast = parse(source, {
      ecmaVersion: 'latest',
      sourceType: 'module',
      locations: true,
      allowReturnOutsideFunction: true,
      allowAwaitOutsideFunction: true,
    });
    return { ok: true, ast };
  } catch (err) {
    if (err && err.pos !== undefined && err.loc) {
      return { ok: false, errors: [{ line: err.loc.line, col: err.loc.column, message: err.message }] };
    }
    return { ok: false, errors: [{ line: 1, col: 1, message: String((err && err.message) || err) }] };
  }
}

function main() {
  let source;
  try {
    source = readFileSync(0, 'utf8');
  } catch (err) {
    console.log(JSON.stringify({ ok: false, errors: [{ line: 1, col: 1, message: `stdin read failed: ${err.message}` }] }));
    process.exit(1);
  }
  if (process.argv.includes('--batch')) {
    let payload;
    try {
      payload = JSON.parse(source);
    } catch (err) {
      console.log(JSON.stringify({ ok: false, errors: [{ line: 1, col: 1, message: `batch payload is not JSON: ${err.message}` }] }));
      process.exit(1);
    }
    const scripts = Array.isArray(payload && payload.scripts) ? payload.scripts : [];
    const results = scripts.map((s) => parseOne(String(s)));
    console.log(JSON.stringify({ results }));
    return;
  }
  console.log(JSON.stringify(parseOne(source)));
}

main();
