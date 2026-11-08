import { describe, expect, it } from "vitest";

import { csrfTokenFrom } from "./client";

describe("csrfTokenFrom", () => {
  it("finds the token among other cookies", () => {
    expect(csrfTokenFrom("sessionid=abc; csrftoken=tok123; theme=dark")).toBe("tok123");
  });

  it("returns null when there is no token", () => {
    expect(csrfTokenFrom("sessionid=abc")).toBeNull();
    expect(csrfTokenFrom("")).toBeNull();
  });

  it("does not match a cookie whose name only contains csrftoken", () => {
    expect(csrfTokenFrom("xcsrftoken=nope")).toBeNull();
  });
});
