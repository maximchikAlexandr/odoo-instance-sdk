import { describe, expect, it } from "vitest";
import { formatLocalTimestamp } from "./format";

describe("formatLocalTimestamp", () => {
  it("uses the requested locale and timezone and omits seconds", () => {
    expect(formatLocalTimestamp("2026-08-19T00:00:59Z", "en-GB", "UTC")).toBe(
      "19/08/2026, 00:00",
    );
    expect(formatLocalTimestamp("2026-08-19T00:00:00Z", "en-US", "America/Los_Angeles"))
      .toBe("08/18/2026, 05:00 PM");
  });

  it("keeps an invalid source value visible", () => {
    expect(formatLocalTimestamp("invalid timestamp", "en-GB", "UTC")).toBe("invalid timestamp");
  });
});
