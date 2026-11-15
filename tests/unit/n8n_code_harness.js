// Runs the body of one n8n Code node with the same inputs that n8n gives it. Used by the workflow tests.
// stdin: {"code": "...", "input": [json, ...], "nodes": {"Node Name": [json, ...]}, "staticData": {}}
// stdout: {"out": [...], "staticData": {...}}
//
// n8n runs a Code node in a sandbox. The sandbox has the JavaScript built-ins and a few helpers.
// It has NO URL, URLSearchParams, structuredClone, or fetch. Checked live on n8n 2.47.4. See D-054.
// So this harness runs the code in a fresh vm context, where those names do not exist either.
const vm = require('vm');
const payload = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const wrap = (arr) => (arr || []).map((j) => ({ json: j }));
const $input = { first: () => wrap(payload.input)[0], all: () => wrap(payload.input), last: () => wrap(payload.input).slice(-1)[0] };
const $ = (name) => {
  if (!(name in (payload.nodes || {}))) throw new Error('no node named ' + name);
  return { first: () => wrap(payload.nodes[name])[0], all: () => wrap(payload.nodes[name]) };
};
const staticData = payload.staticData || {};
const $getWorkflowStaticData = () => staticData;
const context = vm.createContext({ $input, $, $getWorkflowStaticData, console, Buffer, TextEncoder, TextDecoder, setTimeout, clearTimeout });
const out = vm.runInContext('(function ($input, $, $getWorkflowStaticData) {\n' + payload.code + '\n})($input, $, $getWorkflowStaticData)', context, { timeout: 5000 });
console.log(JSON.stringify({ out, staticData }));
