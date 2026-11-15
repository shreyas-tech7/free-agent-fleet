import test from "node:test";
import assert from "node:assert/strict";
import worker, { price, exportCsv } from "../worker.js";

test("price: a small order uses the base rate", () => {
  assert.equal(price(10), 25);
});

test("price: a large order gets a discount", () => {
  assert.equal(price(100), 225);
});

test("price: refuses a bad count", () => {
  for (const bad of [0, -3, 1.5, "abc", 1000000]) assert.throws(() => price(bad), RangeError);
});

test("export: has a header and one line for each order", () => {
  const csv = exportCsv([{ id: 1, widgets: 12, status: "open" }]);
  assert.equal(csv, "id,widgets,status\n1,12,open\n");
});

test("the home page answers", async () => {
  const res = await worker.fetch(new Request("https://example.test/"));
  assert.equal(res.status, 200);
  assert.match(await res.text(), /Widget Tracker/);
});

test("the price endpoint answers with a total", async () => {
  const res = await worker.fetch(new Request("https://example.test/api/price", { method: "POST", body: JSON.stringify({ count: 10 }) }));
  assert.deepEqual(await res.json(), { total: 25 });
});

test("an unknown path gives 404", async () => {
  assert.equal((await worker.fetch(new Request("https://example.test/nothing"))).status, 404);
});
