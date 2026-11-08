import { describe, expect, it } from "vitest";

import { isServerPath, safeNextPath } from "./navigation";
import { availableViews, chooseView } from "./views";

describe("availableViews", () => {
  it.each([
    [["seller"], ["selling"]],
    [["dealer"], ["buying"]],
    [
      ["dealer", "seller"],
      ["selling", "buying"],
    ],
    [["seller", "admin"], ["selling"]],
    [[], []],
  ])("roles %j give views %j", (roles, expected) => {
    expect(availableViews(roles)).toEqual(expected);
  });
});

describe("chooseView", () => {
  it("keeps a remembered view the user still has", () => {
    expect(chooseView(["dealer", "seller"], "buying")).toBe("buying");
  });

  it("ignores a remembered view the user no longer has", () => {
    expect(chooseView(["seller"], "buying")).toBe("selling");
  });

  it("defaults to the first view", () => {
    expect(chooseView(["dealer", "seller"], null)).toBe("selling");
  });

  it("returns null when the user has no marketplace role", () => {
    expect(chooseView([], null)).toBeNull();
  });
});

describe("safeNextPath", () => {
  it.each(["/", "/admin/", "/admin/accounts/user/?q=1"])("allows %s", (path) => {
    expect(safeNextPath(path)).toBe(path);
  });

  it.each([null, "", "admin", "//evil.example", "/\\evil.example", "https://evil.example"])(
    "rejects %s",
    (path) => {
      expect(safeNextPath(path)).toBeNull();
    },
  );
});

describe("isServerPath", () => {
  it("treats Django admin as a server path", () => {
    expect(isServerPath("/admin/")).toBe(true);
    expect(isServerPath("/admin")).toBe(true);
    expect(isServerPath("/administrator")).toBe(false);
    expect(isServerPath("/")).toBe(false);
  });
});
