import axios from "axios";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  timeout: 70 * 60_000,
  headers: { "Content-Type": "application/json" },
});

export const errorMessage = (error: unknown) =>
  axios.isAxiosError(error)
    ? typeof error.response?.data?.detail === "string"
      ? error.response.data.detail
      : (error.response?.data?.detail?.message as string | undefined) ??
        "We could not complete that request. Please try again."
    : "We could not connect to the assistant. Check that the API server is running.";
