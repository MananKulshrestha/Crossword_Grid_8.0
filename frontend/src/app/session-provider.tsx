import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { errorMessage } from "../api/client";
import { shopperApi } from "../api/shopper";
import type { Action, Entry, Session, ShopperResponse } from "../api/types";
import { requestId } from "../lib/utils";

type Context = { session?: Session; ready: boolean; error?: string; latest?: ShopperResponse; history: ShopperResponse[]; compare: Entry[]; send: (message?: string, action?: Action, payload?: Record<string, unknown>) => Promise<ShopperResponse | undefined>; toggleCompare: (entry: Entry) => void; refresh: () => Promise<void> };
const ShopperContext = createContext<Context | null>(null);
const storageKey = "fkgrid-session-id";

export function ShopperProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session>(); const [ready, setReady] = useState(false); const [error, setError] = useState<string>();
  const [latest, setLatest] = useState<ShopperResponse>(); const [history, setHistory] = useState<ShopperResponse[]>([]); const [compare, setCompare] = useState<Entry[]>([]);
  const bootstrap = useCallback(async () => { try { const cached = localStorage.getItem(storageKey); const next = cached ? await shopperApi.getSession(cached).catch(() => shopperApi.createSession()) : await shopperApi.createSession(); localStorage.setItem(storageKey, next.session_id); setSession(next); setError(undefined); } catch (cause) { setError(errorMessage(cause)); } finally { setReady(true); } }, []);
  useEffect(() => { void bootstrap(); }, [bootstrap]);
  useEffect(() => {
    if (!session || latest || history.length || !session.acknowledged_entries.length) return;
    void shopperApi.activeResults(session.session_id).then((page) => {
      if (!page.items.length) return;
      const restoredSearch = {
        response_id: "restored-search",
        action: "SEARCH",
        terminal_state: "ANSWERED_WITH_GROUNDED_RESULTS",
        summary: "Restored the active shortlist from this shopping session.",
        facts: [],
        search_entries: page.items,
        result_set_id: page.result_set_id ?? undefined,
        warnings: [],
      } satisfies ShopperResponse;
      setHistory([restoredSearch]);
    }).catch(() => undefined);
  }, [history.length, latest, session]);
  const refresh = useCallback(async () => { if (!session) return; const next = await shopperApi.getSession(session.session_id); setSession(next); }, [session]);
  const send = useCallback(async (message?: string, action?: Action, payload: Record<string, unknown> = {}) => {
    if (!session) return; setError(undefined);
    try { const result = await shopperApi.turn(session.session_id, { client_turn_id: requestId("turn"), idempotency_key: requestId("idem"), expected_state_version: session.state_version, expected_cart_version: session.cart_version, ...(message ? { message } : { ui_action: { action: action!, payload } }) });
      if (!result.response) throw new Error("The assistant did not return a shopper response.");
      setLatest(result.response); setHistory((items) => [...items, result.response!].slice(-8)); await refresh(); return result.response;
    } catch (cause) { setError(errorMessage(cause)); }
  }, [refresh, session]);
  const toggleCompare = useCallback((entry: Entry) => setCompare((items) => items.some((item) => item.result_entry_id === entry.result_entry_id) ? items.filter((item) => item.result_entry_id !== entry.result_entry_id) : items.length < 4 ? [...items, entry] : items), []);
  const value = useMemo(() => ({ session, ready, error, latest, history, compare, send, toggleCompare, refresh }), [session, ready, error, latest, history, compare, send, toggleCompare, refresh]);
  return <ShopperContext.Provider value={value}>{children}</ShopperContext.Provider>;
}
export function useShopper() { const value = useContext(ShopperContext); if (!value) throw new Error("useShopper must be inside ShopperProvider"); return value; }
