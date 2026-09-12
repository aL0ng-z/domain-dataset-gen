import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import { useProjectAccess } from "./use-project-access";

describe("effective project permissions", () => {
  let server: ReturnType<typeof createApiMockServer>;
  beforeEach(() => { server = createApiMockServer(); server.install(); });
  afterEach(() => server.restore());

  it.each([
    ["reviewer", true, true], ["viewer", false, false], ["editor", true, false],
  ])("uses project %s permissions regardless of global role", async (role, edit, review) => {
    server.onGet("/auth/me", { role: role === "viewer" ? "reviewer" : "viewer" });
    server.onGet("/projects/p1/access", { effective_role: role });
    const { result } = renderHook(() => useProjectAccess("p1"));
    expect(result.current.canEdit).toBe(false);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.canEdit).toBe(edit);
    expect(result.current.canReview).toBe(review);
    expect(server.wasCalled("GET", "/auth/me")).toBe(false);
  });

  it("cannot carry a delayed permission response into another project", async () => {
    server.onGet("/projects/p1/access", { effective_role: "reviewer" }, { delay: 80 });
    server.onGet("/projects/p2/access", { effective_role: "viewer" });
    const { result, rerender } = renderHook(({ pid }) => useProjectAccess(pid), { initialProps: { pid: "p1" } });
    rerender({ pid: "p2" });
    await waitFor(() => expect(result.current.role).toBe("viewer"));
    await act(async () => { await new Promise((r) => setTimeout(r, 100)); });
    expect(result.current.canReview).toBe(false);
  });

  it("revocation detected on focus removes permissions", async () => {
    server.onGet("/projects/p1/access", { effective_role: "reviewer" });
    const { result } = renderHook(() => useProjectAccess("p1"));
    await waitFor(() => expect(result.current.canReview).toBe(true));
    server.mock("GET", "/projects/p1/access", { status: 403 });
    act(() => window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.canEdit).toBe(false);
  });
});
