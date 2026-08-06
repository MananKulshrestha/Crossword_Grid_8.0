import axios from "axios";
import { useCallback, useEffect, useRef, useState } from "react";
import { chatApi, type SearchMode } from "@/api/chat";
import { errorMessage } from "@/api/client";
import type { SearchEntry, TurnResult } from "@/api/types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  turn?: TurnResult;
  error?: string;
}

const SESSION_KEY = "fkgrid-session-id";

export function useChat() {
  const [sessionId, setSessionId] = useState<string>();
  const sessionIdRef = useRef<string>();
  sessionIdRef.current = sessionId;
  const [ready, setReady] = useState(false);
  const [bootError, setBootError] = useState<string>();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [mode, setMode] = useState<SearchMode>("deep");
  const skuIndex = useRef(new Map<string, SearchEntry>());

  const indexEntries = useCallback((entries?: SearchEntry[] | null) => {
    for (const entry of entries ?? []) skuIndex.current.set(entry.sku_id, entry);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const cached = sessionStorage.getItem(SESSION_KEY);
        const id = cached ?? (await chatApi.createSession()).session_id;
        if (!cached) sessionStorage.setItem(SESSION_KEY, id);
        if (!cancelled) setSessionId(id);
      } catch (cause) {
        if (!cancelled) setBootError(errorMessage(cause));
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || !sessionId || sending) return;
      const userMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", text: trimmed };
      setMessages((prev) => [...prev, userMsg]);
      setSending(true);
      try {
        let activeSessionId = sessionId;
        let turn: TurnResult;
        try {
          turn = await chatApi.sendTurn(activeSessionId, trimmed, mode);
        } catch (cause) {
          if (axios.isAxiosError(cause) && cause.response?.status === 404) {
            const fresh = await chatApi.createSession();
            activeSessionId = fresh.session_id;
            sessionStorage.setItem(SESSION_KEY, activeSessionId);
            setSessionId(activeSessionId);
            turn = await chatApi.sendTurn(activeSessionId, trimmed, mode);
          } else {
            throw cause;
          }
        }
        indexEntries(turn.search_result?.entries);
        if (turn.product_details?.entry) indexEntries([turn.product_details.entry]);
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "assistant", text: turn.message, turn },
        ]);
      } catch (cause) {
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "assistant", text: "", error: errorMessage(cause) },
        ]);
      } finally {
        setSending(false);
      }
    },
    [sessionId, sending, mode, indexEntries],
  );

  const titleFor = useCallback((skuId: string) => skuIndex.current.get(skuId)?.title ?? skuId, []);

  return { ready, bootError, sessionId, messages, sending, send, titleFor, mode, setMode };
}
