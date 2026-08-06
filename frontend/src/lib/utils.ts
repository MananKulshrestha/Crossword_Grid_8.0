import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));
export const inr = (paise?: number | null) =>
  paise == null ? "—" : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(paise / 100);
export const display = (value: unknown) =>
  value == null || value === "" ? "—" : String(value).replaceAll("_", " ").replace(/\b\w/g, (match: string) => match.toUpperCase());
