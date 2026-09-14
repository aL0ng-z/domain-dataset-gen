import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useGenerationTracking, type GenerationTrack } from "@/hooks/use-generation-tracking";
import { createApiMockServer } from "@/lib/__mocks__/api-server";

const server = createApiMockServer();

function TrackingProbe({ onTerminal }: { onTerminal: (track: GenerationTrack) => void }) {
  const { track, startTrack, cancelTask } = useGenerationTracking("p1", { onTerminal });
  return (
    <div>
      <button onClick={() => startTrack("task-1", "batch-1")}>开始</button>
      <button onClick={() => void cancelTask()} disabled={!track}>取消</button>
      <span data-testid="status">{track?.status ?? "none"}</span>
    </div>
  );
}

beforeEach(() => {
  server.reset();
  server.install();
  window.history.replaceState(null, "", "/projects/p1/documents/d1");
});

afterEach(() => {
  server.restore();
});

describe("useGenerationTracking", () => {
  it("queued 任务被同步取消时仍加载批次汇总并只回调一次", async () => {
    server.onGet("/projects/p1/tasks/task-1", {
      id: "task-1",
      status: "queued",
      can_cancel: true,
    });
    const cancel = server.onPost("/projects/p1/tasks/task-1/cancel", {
      id: "task-1",
      status: "cancelled",
    });
    const batch = server.onGet("/projects/p1/generation-batches/batch-1", {
      id: "batch-1",
      status: "cancelled",
      completed_chunks: 0,
      total_chunks: 1,
      summary_json: { succeeded: 0, failed: 0, cancelled: 1 },
    });
    const onTerminal = vi.fn<(track: GenerationTrack) => void>();
    render(<TrackingProbe onTerminal={onTerminal} />);

    await userEvent.click(screen.getByRole("button", { name: "开始" }));
    await screen.findByText("queued");
    await userEvent.click(screen.getByRole("button", { name: "取消" }));

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("cancelled"));
    expect(cancel.callCount).toBe(1);
    expect(batch.callCount).toBe(1);
    expect(onTerminal).toHaveBeenCalledTimes(1);
    expect(onTerminal).toHaveBeenCalledWith(expect.objectContaining({
      status: "cancelled",
      batch: expect.objectContaining({ id: "batch-1" }),
    }));
  });
});
