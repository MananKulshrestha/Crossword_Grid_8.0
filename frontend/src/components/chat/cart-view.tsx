import { ShoppingCart } from "lucide-react";
import type { CartSnapshot } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { display, inr } from "@/lib/utils";

export function CartView({ cart }: { cart: CartSnapshot }) {
  if (!cart.items.length) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">
        <ShoppingCart size={16} /> Your cart is empty.
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border bg-fk-mist px-4 py-2.5">
        <span className="flex items-center gap-1.5 text-sm font-bold">
          <ShoppingCart size={15} /> Cart · {cart.item_count} item{cart.item_count === 1 ? "" : "s"}
        </span>
        <span className="text-sm font-bold text-fk-blue">{inr(cart.subtotal_paise)}</span>
      </div>
      <div className="divide-y divide-border">
        {cart.items.map((item) => (
          <div key={item.cart_item_id} className="flex items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">{item.title}</p>
              <p className="text-xs text-muted-foreground">
                Qty {item.quantity} · {inr(item.unit_price_paise)} each
              </p>
            </div>
            <div className="flex flex-col items-end gap-1">
              <span className="text-sm font-bold">{inr(item.line_subtotal_paise)}</span>
              <Badge variant={item.availability_status === "IN_STOCK" ? "success" : item.availability_status === "LOW_STOCK" ? "warning" : "destructive"}>
                {display(item.availability_status)}
              </Badge>
            </div>
          </div>
        ))}
      </div>
      <Separator />
      <div className="flex items-center justify-between px-4 py-3 text-sm font-bold">
        <span>Subtotal ({cart.total_quantity} units)</span>
        <span>{inr(cart.subtotal_paise)}</span>
      </div>
    </div>
  );
}
