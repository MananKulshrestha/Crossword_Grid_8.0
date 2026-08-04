import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));
export const inr = (paise?: number) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format((paise ?? 0) / 100);
export const display = (value: unknown) => String(value ?? "—").replaceAll("_", " ").replace(/\b\w/g, (match: string) => match.toUpperCase());
export const requestId = (prefix: string) => `${prefix}-${crypto.randomUUID()}`;
