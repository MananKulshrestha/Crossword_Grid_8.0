import { api } from "./client";
import type { CreateSessionResponse, TurnResult } from "./types";

export type SearchMode = "fast" | "deep";

export const chatApi = {
  createSession: async () => (await api.post<CreateSessionResponse>("/v1/sessions")).data,
  sendTurn: async (sessionId: string, message: string, mode: SearchMode = "deep") =>
    (await api.post<TurnResult>(`/v1/sessions/${sessionId}/turns`, { message, mode })).data,
};
