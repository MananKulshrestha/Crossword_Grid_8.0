import { Rabbit, Telescope } from "lucide-react";
import type { SearchMode } from "@/api/chat";
import { cn } from "@/lib/utils";

export function ModeToggle({ mode, onChange }: { mode: SearchMode; onChange: (mode: SearchMode) => void }) {
  return (
    <div className="flex items-center rounded-full bg-white/15 p-0.5 text-[11px] font-bold text-white">
      <button
        type="button"
        onClick={() => onChange("fast")}
        title="Fast: SQL filter + BM25 ranking, skips the reranker for quicker results"
        className={cn(
          "focus-ring flex items-center gap-1 rounded-full px-2.5 py-1 transition",
          mode === "fast" ? "bg-white text-fk-blue shadow-sm" : "text-white/80 hover:text-white",
        )}
      >
        <Rabbit size={13} /> Fast
      </button>
      <button
        type="button"
        onClick={() => onChange("deep")}
        title="Deep: full reranker pipeline, slower but more thorough"
        className={cn(
          "focus-ring flex items-center gap-1 rounded-full px-2.5 py-1 transition",
          mode === "deep" ? "bg-white text-fk-blue shadow-sm" : "text-white/80 hover:text-white",
        )}
      >
        <Telescope size={13} /> Deep
      </button>
    </div>
  );
}
