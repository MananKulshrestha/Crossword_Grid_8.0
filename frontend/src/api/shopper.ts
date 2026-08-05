import { api } from "./client";
import type { Action, ActiveResults, CatalogFacets, CatalogPage, Session, TurnResult } from "./types";

export const shopperApi = {
  createSession: async () => (await api.post<Session>("/v1/sessions", {})).data,
  getSession: async (id: string) => (await api.get<Session>(`/v1/sessions/${id}`)).data,
  activeResults: async (id: string) => (await api.get<ActiveResults>(`/v1/sessions/${id}/active-results`)).data,
  catalog: async (params: { offset?: number; limit?: number; category?: string; brand?: string } = {}) => (await api.get<CatalogPage>("/v1/catalog", { params })).data,
  facets: async () => (await api.get<CatalogFacets>("/v1/catalog/facets")).data,
  turn: async (sessionId: string, payload: { client_turn_id: string; idempotency_key: string; expected_state_version?: number; expected_cart_version?: number; message?: string; ui_action?: { action: Action; payload: Record<string, unknown>; signed_action_token?: string } }) => (await api.post<TurnResult>(`/v1/sessions/${sessionId}/turns`, payload)).data,
  speech: async (audio: Blob) => (await api.post<{ text: string }>("/v1/speech/transcriptions", audio, { headers: { "Content-Type": audio.type } })).data,
  synthesizeSpeech: async (text: string, languageCode: string) => (await api.post<Blob>("/v1/speech/synthesize", { text, language_code: languageCode }, { responseType: "blob" })).data
};
