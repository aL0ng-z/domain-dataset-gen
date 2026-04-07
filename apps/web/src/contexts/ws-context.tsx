"use client";

import React, {
  createContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createWsClient, type WsClient, type WsMessageHandler } from "@/lib/ws";

export interface WsContextValue {
  subscribe: (event: string, handler: WsMessageHandler) => () => void;
  lastMessage: unknown;
}

export const WsContext = createContext<WsContextValue>({
  subscribe: () => () => {},
  lastMessage: null,
});

interface WsProviderProps {
  projectId?: string;
  children: React.ReactNode;
}

export function WsProvider({ projectId, children }: WsProviderProps) {
  const clientRef = useRef<WsClient | null>(null);
  const [lastMessage, setLastMessage] = useState<unknown>(null);

  useEffect(() => {
    if (!projectId) return;

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
    const wsBase = apiUrl.replace(/^http/, "ws").replace(/\/api$/, "");
    const url = `${wsBase}/ws/projects/${projectId}/tasks`;

    const client = createWsClient(url);
    clientRef.current = client;
    client.connect();

    // Track all messages for lastMessage
    const unsub = client.subscribe("*", (msg) => {
      setLastMessage(msg);
    });

    return () => {
      unsub();
      client.disconnect();
      clientRef.current = null;
    };
  }, [projectId]);

  const subscribe = useMemo(() => {
    return (event: string, handler: WsMessageHandler) => {
      if (clientRef.current) {
        return clientRef.current.subscribe(event, handler);
      }
      return () => {};
    };
  }, []);

  const value = useMemo(
    () => ({ subscribe, lastMessage }),
    [subscribe, lastMessage]
  );

  return <WsContext.Provider value={value}>{children}</WsContext.Provider>;
}
