import assert from "node:assert/strict";
import { test } from "node:test";
import { phoneChars, phoneProblem } from "./phone.ts";

test("phone numbers with a country code, spaces or dashes are fine", () => {
  for (const ok of ["+91 98765 43210", "(022) 2345-6789", "9876543210", "98765.43210", ""]) {
    assert.equal(phoneProblem(ok), null, ok);
  }
});

test("anything else is refused", () => {
  assert.match(phoneProblem("call me")!, /digits only/);
  assert.match(phoneProblem("91+98765")!, /digits only/);
  assert.match(phoneProblem("12345")!, /7 to 15 digits/);
  assert.match(phoneProblem("+1 555 0100 2233 99887")!, /7 to 15 digits/);
});

test("letters never make it into the field", () => {
  assert.equal(phoneChars("+91 98a76b5-432x10"), "+91 98765-43210");
});
