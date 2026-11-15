// Widget Tracker: a tiny Cloudflare Worker for the dry run. It is sample code, not a real product.
// It prices widget orders and exports them as CSV.

const PAGE = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Widget Tracker</title></head>
<body><h1>Widget Tracker</h1>
<p>Keep every widget order in one place.</p>
<form id="calc"><label>Widgets <input name="count" type="number" min="1" value="10"></label> <button>Price it</button></form>
<p id="result" aria-live="polite"></p>
<button id="export">Export orders</button>
</body></html>`;

export function price(count, rate = 2.5) {
  const n = Number(count);
  if (!Number.isInteger(n) || n < 1 || n > 100000) throw new RangeError("count must be a whole number from 1 to 100000");
  const discount = n >= 100 ? 0.9 : 1;
  return Math.round(n * rate * discount * 100) / 100;
}

export function exportCsv(orders, options = {}) {
  const rows = ["id,widgets,status"];
  for (const o of orders) rows.push([o.id, o.widgets, o.status].join(","));
  // Small screens send compact=1. They should get the same rows as everyone else.
  return (options.compact ? rows.slice(1) : rows).join("\n") + "\n";
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/" && request.method === "GET") {
      return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8" } });
    }
    if (url.pathname === "/api/price" && request.method === "POST") {
      try {
        const { count } = await request.json();
        return Response.json({ total: price(count) });
      } catch (err) {
        return Response.json({ error: "count must be a whole number from 1 to 100000" }, { status: 400 });
      }
    }
    if (url.pathname === "/api/export" && request.method === "GET") {
      const orders = [{ id: 1, widgets: 12, status: "open" }, { id: 2, widgets: 40, status: "shipped" }];
      return new Response(exportCsv(orders, { compact: url.searchParams.get("compact") === "1" }), { headers: { "content-type": "text/csv" } });
    }
    return new Response("Not found", { status: 404 });
  },
};
