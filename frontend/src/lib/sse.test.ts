import assert from "node:assert/strict";
import { test } from "node:test";
import { createSseParser, type SseEvent } from "./sse.ts";

function parse(chunks: string[]): SseEvent[] {
  const events: SseEvent[] = [];
  const parser = createSseParser((e) => events.push(e));
  chunks.forEach((c) => parser.push(c));
  parser.end();
  return events;
}

test("named events with JSON data", () => {
  assert.deepEqual(parse(['event: status\ndata: {"label":"Thinking…"}\n\n', 'event: reply\ndata: {"text":"Hi"}\n\n']), [
    { event: "status", data: { label: "Thinking…" } },
    { event: "reply", data: { text: "Hi" } },
  ]);
});

test("events split across chunks, CRLF and keep-alive comments", () => {
  assert.deepEqual(parse(["event: rep", 'ly\r\ndata: {"te', 'xt":"a"}\r\n\r\n', ": keep-alive\n\n", "event: done\ndata: {}\n\n"]), [
    { event: "reply", data: { text: "a" } },
    { event: "done", data: {} },
  ]);
});

test("an event without a name is a message; data that isn't JSON stays text", () => {
  assert.deepEqual(parse(["data: hello\ndata: world"]), [{ event: "message", data: "hello\nworld" }]);
});
