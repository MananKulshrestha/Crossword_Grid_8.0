import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: { colors: { fk: { blue: "#2874f0", ink: "#172337", yellow: "#ffe500", mist: "#f1f3f6" } }, boxShadow: { card: "0 2px 12px rgba(23,35,55,.08)", lift: "0 10px 28px rgba(23,35,55,.14)" } } },
  plugins: []
} satisfies Config;
