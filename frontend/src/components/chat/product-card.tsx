import { Star, PackageCheck, PackageX } from "lucide-react";
import type { SearchEntry } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { display, inr } from "@/lib/utils";

export function ProductCard({ entry }: { entry: SearchEntry }) {
  const status = (entry.availability_status ?? "").toUpperCase();
  const available = status === "IN_STOCK" || status === "LOW_STOCK";
  return (
    <article className="group flex w-full flex-col overflow-hidden rounded-xl border border-border bg-card shadow-card transition duration-200 hover:-translate-y-0.5 hover:shadow-lift">
      <div className="relative flex h-32 items-center justify-center bg-fk-mist p-4">
        <div className="grid h-20 w-20 place-items-center rounded-2xl border border-white bg-gradient-to-br from-blue-100 to-slate-100 text-center text-[10px] font-bold leading-4 text-fk-blue">
          {display(entry.category)}
        </div>
        {entry.rerank_score != null && (
          <span className="absolute left-2 top-2 rounded bg-white/90 px-1.5 py-0.5 text-[9px] font-bold text-slate-400">
            match {(entry.rerank_score * 100).toFixed(0)}%
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-1.5 p-3">
        <p className="line-clamp-2 min-h-9 text-[13px] font-semibold leading-4 text-foreground">{entry.title}</p>
        <div className="flex items-center gap-1.5">
          {entry.rating != null && (
            <span className="flex items-center gap-0.5 rounded bg-fk-green px-1.5 py-0.5 text-[11px] font-bold text-white">
              {entry.rating.toFixed(1)} <Star size={9} fill="currentColor" />
            </span>
          )}
          {entry.brand && <span className="truncate text-[11px] text-muted-foreground">{entry.brand}</span>}
        </div>
        <p className="mt-0.5 text-base font-bold text-foreground">{inr(entry.price_paise)}</p>
        <div className="mt-auto flex items-center justify-between pt-1">
          <Badge variant={status === "LOW_STOCK" ? "warning" : available ? "success" : "destructive"} className="gap-1">
            {available ? <PackageCheck size={11} /> : <PackageX size={11} />}
            {display(entry.availability_status)}
          </Badge>
          {entry.quantity != null && <span className="text-[10px] text-muted-foreground">Qty {entry.quantity}</span>}
        </div>
        <p className="truncate pt-0.5 text-[10px] text-slate-400">{entry.sku_id}</p>
      </div>
    </article>
  );
}

export function ProductGrid({ entries }: { entries: SearchEntry[] }) {
  if (!entries.length) return <p className="text-sm text-muted-foreground">No matching products found.</p>;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {entries.map((entry) => (
        <ProductCard key={entry.sku_id} entry={entry} />
      ))}
    </div>
  );
}
