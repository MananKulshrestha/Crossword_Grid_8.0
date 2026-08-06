import type { SessionState, TurnResult } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message: string, readonly code?: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError("The shopping server could not be reached. Start it with ./run_server.sh.", "NETWORK_ERROR");
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.message || "The server could not complete this request.";
    throw new ApiError(message, detail?.error_code || String(response.status));
  }
  return body as T;
}

export async function createSession(): Promise<string> {
  const result = await request<{ session_id: string }>("/v1/sessions", { method: "POST", body: "{}" });
  return result.session_id;
}

export function getSession(sessionId: string): Promise<SessionState> {
  return request(`/v1/sessions/${sessionId}`);
}

export function sendTurn(sessionId: string, message: string): Promise<TurnResult> {
  return request(`/v1/sessions/${sessionId}/turns`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}
