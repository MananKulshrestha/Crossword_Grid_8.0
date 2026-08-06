import { AlertTriangle, ShoppingBag, Sparkles } from "lucide-react";
import type { ChatMessage } from "@/hooks/use-chat";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { ProductGrid } from "@/components/chat/product-card";
import { CompareTable } from "@/components/chat/compare-table";
import { CartView } from "@/components/chat/cart-view";
import { AvailabilityView } from "@/components/chat/availability-view";
import { Followups } from "@/components/chat/followups";

export function MessageBubble({ msg, titleFor, onFollowup }: {
  msg: ChatMessage;
  titleFor: (skuId: string) => string;
  onFollowup: (label: string) => void;
}) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end gap-2.5 px-1">
        <div className="max-w-[80%] rounded-2xl rounded-tr-sm bg-fk-blue px-4 py-2.5 text-sm font-medium text-white shadow-sm">
          {msg.text}
        </div>
      </div>
    );
  }

  const turn = msg.turn;
  return (
    <div className="flex gap-2.5 px-1">
      <Avatar className="mt-0.5 shrink-0 bg-fk-blue/10">
        <AvatarFallback className="bg-fk-blue/10 text-fk-blue">
          <Sparkles size={15} />
        </AvatarFallback>
      </Avatar>
      <div className="min-w-0 max-w-[92%] flex-1 space-y-3">
        {msg.error ? (
          <div className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm font-medium text-red-700">
            <AlertTriangle size={15} /> {msg.error}
          </div>
        ) : (
          <>
            {msg.text && (
              <div className="rounded-2xl rounded-tl-sm border border-border bg-card px-4 py-2.5 text-sm leading-6 shadow-sm">
                {msg.text}
              </div>
            )}
            {turn?.search_result && turn.search_result.entries.length > 0 && (
              <div className="space-y-1.5">
                <p className="eyebrow flex items-center gap-1.5">
                  <ShoppingBag size={12} /> {turn.search_result.entries.length} result
                  {turn.search_result.entries.length === 1 ? "" : "s"}
                </p>
                <ProductGrid entries={turn.search_result.entries} />
              </div>
            )}
            {turn?.product_details?.found && turn.product_details.entry && (
              <ProductGrid entries={[turn.product_details.entry]} />
            )}
            {turn?.comparison && <CompareTable comparison={turn.comparison} titleFor={titleFor} />}
            {turn?.availability && <AvailabilityView availability={turn.availability} />}
            {turn?.cart && <CartView cart={turn.cart} />}
            {turn?.followups && <Followups items={turn.followups} onPick={onFollowup} />}
          </>
        )}
      </div>
    </div>
  );
}
