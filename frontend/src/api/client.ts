import axios from "axios";

export const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL, timeout: 12_000, headers: { "Content-Type": "application/json" } });
export const errorMessage = (error: unknown) => axios.isAxiosError(error) ? (typeof error.response?.data?.detail === "string" ? error.response.data.detail : "We could not complete that request. Please try again.") : "We could not connect to FK GRiD. Check that the API is running.";
