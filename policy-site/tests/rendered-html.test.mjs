import assert from "node:assert/strict";
import test from "node:test";

async function render(path = "/", origin = "https://getmemlo.com") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${path}`);
  const { default: worker } = await import(workerUrl.href);
  const host = new URL(origin).host;

  return worker.fetch(
    new Request(`${origin}${path}`, {
      headers: {
        accept: "text/html",
        host,
        "x-forwarded-host": host,
        "x-forwarded-proto": "https",
      },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("redirects the generated Sites address to getmemlo.com", async () => {
  const response = await render(
    "/privacy?source=legacy",
    "https://almanac-trust-center.vicky-fu-7766.chatgpt.site",
  );
  assert.equal(response.status, 308);
  assert.equal(
    response.headers.get("location"),
    "https://getmemlo.com/privacy?source=legacy",
  );
});

test("renders the Memlo product homepage", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Memlo — The group chat that remembers<\/title>/i);
  assert.match(html, /The message<br\s*\/> passes\./);
  assert.match(html, /The memory stays\./);
  assert.match(html, /YOUR STUDY SHELF/);
  assert.match(html, /Clear boundaries\./);
  assert.match(html, /Try the Memlo flow/);
  assert.match(html, /Illustrative preview/);
  assert.match(html, /No AI needed/);
  assert.match(html, /reschedule/);
  assert.match(html, /cancel-deadline/);
  assert.match(html, /private by default/);
  assert.match(html, /Warm-up, Mixed, and Challenge/);
  assert.match(html, /kidwithakrow\._\./);
  assert.match(html, /permissions=84992/);
  assert.doesNotMatch(html, /permissions=117760/);
  assert.match(html, /west ford hs study chat/);
  assert.match(html, /9th-grade/);
  assert.match(html, /Message #9th-grade/);
  assert.match(html, /\/due/);
  assert.match(html, /\/quiz/);
  assert.doesNotMatch(html, /Demo step/);
  assert.match(html, /Add Memlo to Discord/);
  assert.match(html, /Try Memlo live/);
  assert.match(html, /https:\/\/discord\.gg\/7UT2aPf5Us/);
  assert.doesNotMatch(html, /Almanac/);
});

test("renders the separate Memlo trust center", async () => {
  const response = await render("/trust");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Memlo trust center/);
  assert.match(html, /Trust, by default\./);
  assert.match(html, /Nothing until an admin chooses\./);
  assert.doesNotMatch(html, /Almanac/);
});

test("renders the Memlo privacy policy", async () => {
  const response = await render("/privacy");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Privacy Policy/);
  assert.match(html, /What Memlo processes/);
  assert.match(html, /Last updated October 25, 2026/);
  assert.match(html, /Text uploads are retained as extracted text/);
  assert.match(html, /Without AI, slash commands/);
  assert.doesNotMatch(html, /Almanac/);
});

test("renders the Memlo terms", async () => {
  const response = await render("/terms");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Terms of Service/);
  assert.match(html, /What Memlo provides/);
  assert.doesNotMatch(html, /Almanac/);
});
