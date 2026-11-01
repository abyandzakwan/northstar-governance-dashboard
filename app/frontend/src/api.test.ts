import { describe, it, expect } from "vitest";
import { dateLabel, envLabel } from "./api";
describe("presentation of evidence", () => {
  it("distinguishes governed environment labels and unclassified accounts", () => {
    expect(envLabel("production")).toBe("Production");
    expect(envLabel("dev")).toBe("Development");
    expect(envLabel("unclassified")).toBe("Unclassified");
  });
  it("does not invent a deployment timestamp when one was not collected", () => {
    expect(dateLabel(null)).toBe("Not available");
  });
});
