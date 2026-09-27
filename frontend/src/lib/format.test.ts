// Unit tests for the booking length and price helpers: `npm test`.
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { bookingPrice, formatDayTimes, formatDuration, formatLengthRange, formatRate, lengthOptions } from "./format.ts";

describe("bookingPrice", () => {
  // Expected totals were computed with the server's formula (Decimal
  // rate x minutes x places / 60, rounded half up to the cent), so the price
  // the wizard shows is the price the booking gets.
  const cases: [rate: string, minutes: number, places: number, total: string][] = [
    ["50.00", 90, 1, "75.00"],
    ["50.00", 30, 1, "25.00"],
    ["45.00", 120, 1, "90.00"],
    ["12.00", 60, 3, "36.00"],
    ["10.05", 30, 1, "5.03"],
    ["0.01", 30, 1, "0.01"],
    ["19.99", 45, 1, "14.99"],
    ["33.33", 90, 2, "99.99"],
    ["15.00", 240, 1, "60.00"],
    ["7.77", 150, 1, "19.43"],
  ];
  for (const [rate, minutes, places, total] of cases) {
    it(`${rate} per hour for ${minutes} min x ${places} = ${total}`, () => {
      assert.equal(bookingPrice(rate, minutes, places), total);
    });
  }

  it("reads rates without cents", () => {
    assert.equal(bookingPrice("40", 90), "60.00");
    assert.equal(bookingPrice("12.5", 60), "12.50");
  });

  it("has no price without a rate", () => {
    assert.equal(bookingPrice(null, 60), null);
    assert.equal(bookingPrice(undefined, 60), null);
  });
});

describe("lengthOptions", () => {
  it("steps by the standard length up to the longest", () => {
    assert.deepEqual(lengthOptions(30, 120), [30, 60, 90, 120]);
    assert.deepEqual(lengthOptions(60, 240), [60, 120, 180, 240]);
  });

  it("stops at the last whole step", () => {
    assert.deepEqual(lengthOptions(45, 120), [45, 90]);
  });

  it("offers only the standard length when it is fixed", () => {
    assert.deepEqual(lengthOptions(60, null), [60]);
    assert.deepEqual(lengthOptions(60, undefined), [60]);
    assert.deepEqual(lengthOptions(60, 60), [60]);
    assert.deepEqual(lengthOptions(60, 30), [60]);
  });
});

describe("formatLengthRange", () => {
  it("shows the shortest and longest bookable length", () => {
    assert.equal(formatLengthRange(30, 120), "30 min – 2 h");
    assert.equal(formatLengthRange(45, 120), "45 min – 1 h 30 min");
  });

  it("shows one length when it is fixed", () => {
    assert.equal(formatLengthRange(60, null), "1 h");
    assert.equal(formatLengthRange(90, 90), "1 h 30 min");
  });
});

describe("formatDuration", () => {
  it("uses minutes, hours or both", () => {
    assert.equal(formatDuration(30), "30 min");
    assert.equal(formatDuration(60), "1 h");
    assert.equal(formatDuration(150), "2 h 30 min");
  });
});

describe("formatRate", () => {
  it("marks prices as hourly", () => {
    assert.equal(formatRate("40.00"), "40.00 / h");
    assert.equal(formatRate(null), "—");
  });
});

describe("formatDayTimes", () => {
  it("shows the weekday and times of a booking", () => {
    const text = formatDayTimes("2027-03-01T04:30:00Z", "2027-03-01T06:00:00Z", "Asia/Kolkata");
    assert.match(text, /^Mon · 10:00( AM)? – 11:30( AM)?$/);
  });

  it("gives the end its own date when the booking runs past midnight", () => {
    const text = formatDayTimes("2027-03-01T23:30:00Z", "2027-03-02T00:30:00Z", "UTC");
    assert.match(text, /^Mon · 11:30 PM – Tue, Mar 2, 2027 · 12:30 AM$|^Mon · 23:30 – .*2027.* · 00:30$/);
  });
});
