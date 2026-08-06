import { Sparkles } from "lucide-react";
import type { FollowUpSuggestion } from "@/api/types";
import { Button } from "@/components/ui/button";

export function Followups({ items, onPick }: { items: FollowUpSuggestion[]; onPick: (label: string) => void }) {
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap gap-2 pt-1">
      {items.map((item) => (
        <Button key={item.label} variant="outline" size="sm" onClick={() => onPick(item.label)} className="h-8">
          <Sparkles size={12} />
          {item.label}
        </Button>
      ))}
    </div>
  );
}
