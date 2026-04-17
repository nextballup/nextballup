import { describe, expect, it } from "vitest";
import { resolveActiveTeamId } from "@/lib/active-team";

describe("resolveActiveTeamId", () => {
  it("returns null when the user has no teams", () => {
    expect(resolveActiveTeamId("anything", [])).toBeNull();
  });

  it("uses the cookie value when it maps to a current team", () => {
    const teams = [{ id: "a" }, { id: "b" }];
    expect(resolveActiveTeamId("b", teams)).toBe("b");
  });

  it("falls back to the first team when the cookie is stale", () => {
    const teams = [{ id: "a" }, { id: "b" }];
    expect(resolveActiveTeamId("missing", teams)).toBe("a");
  });

  it("falls back to the first team when the cookie is missing", () => {
    const teams = [{ id: "a" }];
    expect(resolveActiveTeamId(undefined, teams)).toBe("a");
  });
});
