import { describe, expect, it, vi, afterEach } from "vitest";

import { createWsClient, type WsClient } from "@/lib/ws";

/** 内存 WebSocket mock：记录发送消息与 close，模拟 onmessage/onclose。 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;
  static CONNECTING = 0;

  readyState: number = FakeWebSocket.CONNECTING;
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.closed = true;
    this.readyState = 0;
    if (this.onclose) this.onclose();
  }

  /** 测试辅助：模拟服务端推消息。 */
  emit(data: string) {
    if (this.onmessage) this.onmessage({ data });
  }

  /** 测试辅助：模拟连接成功。 */
  open() {
    this.readyState = 1;
    if (this.onopen) this.onopen();
  }
}

afterEach(() => {
  FakeWebSocket.instances = [];
});

describe("ws client", () => {
  it("带 token 时把 token 附加到 URL 并订阅/分发事件", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    localStorage.setItem("access_token", "token-abc");

    const client: WsClient = createWsClient("ws://test/ws/projects/1/tasks");
    const handler = vi.fn();
    const unsub = client.subscribe("task.updated", handler);
    client.connect();

    const ws = FakeWebSocket.instances[0];
    expect(ws.url).toContain("token=token-abc");
    ws.open();

    ws.emit(JSON.stringify({ event: "task.updated", data: { id: 1 } }));
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ event: "task.updated" }));

    unsub();
    ws.emit(JSON.stringify({ event: "task.updated", data: { id: 2 } }));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("断开连接后按退避策略重连", () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal("WebSocket", FakeWebSocket);
      localStorage.removeItem("access_token");

      const client: WsClient = createWsClient("ws://test/ws");
      client.connect();

      // 首次连接 -> onclose 触发重连调度（1s 退避）
      const first = FakeWebSocket.instances[0];
      first.open();
      first.close();
      expect(FakeWebSocket.instances.length).toBe(1);

      vi.advanceTimersByTime(1000);
      expect(FakeWebSocket.instances.length).toBe(2);

      // 主动 disconnect 不应重连
      const second = FakeWebSocket.instances[1];
      second.open();
      client.disconnect();
      const countAfterDisconnect = FakeWebSocket.instances.length;
      vi.advanceTimersByTime(5000);
      expect(FakeWebSocket.instances.length).toBe(countAfterDisconnect);
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });
});
