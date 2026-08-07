import { PiggyBank, SearchX, Wallet } from "lucide-react";
import type { Basket } from "@/api/types";
import { ProductCard } from "@/components/chat/product-card";
import { Badge } from "@/components/ui/badge";
import { inr } from "@/lib/utils";

export function BasketView({ basket }: { basket: Basket }) {
  const overBudget = basket.headroom_paise != null && basket.headroom_paise < 0;
  // Only worth showing when the optimiser actually traded down - if the top
  // pick of every slot already fit, there was no trade-off to explain.
  const tradedDown =
    basket.naive_total_paise != null && basket.naive_total_paise > basket.total_paise;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 shadow-sm">
        <Wallet size={15} className="text-fk-blue" />
        <span className="text-sm font-bold text-foreground">{inr(basket.total_paise)}</span>
        {basket.budget_paise != null && (
          <span className="text-sm text-muted-foreground">of {inr(basket.budget_paise)} budget</span>
        )}
        {basket.headroom_paise != null && (
          <Badge variant={overBudget ? "destructive" : "success"} className="gap-1">
            <PiggyBank size={11} />
            {overBudget
              ? `${inr(-basket.headroom_paise)} over`
              : `${inr(basket.headroom_paise)} left`}
          </Badge>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {basket.slots.length} item{basket.slots.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {basket.slots.map((slot) => (
          <div key={slot.entry.sku_id} className="space-y-1">
            <p className="eyebrow truncate" title={slot.label}>
              {slot.label}
            </p>
            <ProductCard entry={slot.entry} />
          </div>
        ))}
      </div>

      {tradedDown && (
        <p className="text-[11px] leading-4 text-muted-foreground">
          The top-scoring pick for every slot would have cost{" "}
          <strong>{inr(basket.naive_total_paise)}</strong> — over budget. The solver traded some
          per-item relevance for a basket that actually fits.
        </p>
      )}

      {basket.unfilled.length > 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-[13px] font-medium text-amber-800">
          <SearchX size={14} className="shrink-0" />
          No catalog match for: {basket.unfilled.join(", ")}
        </div>
      )}
    </div>
  );
}
