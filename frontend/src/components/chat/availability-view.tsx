import { PackageCheck, PackageX } from "lucide-react";
import type { Availability } from "@/api/types";
import { display } from "@/lib/utils";

export function AvailabilityView({ availability }: { availability: Availability }) {
  if (!availability.found) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
        <PackageX size={15} /> Could not find that product.
      </div>
    );
  }
  const status = (availability.availability_status ?? "").toUpperCase();
  const available = status === "IN_STOCK" || status === "LOW_STOCK";
  return (
    <div
      className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium ${
        available ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-700"
      }`}
    >
      {available ? <PackageCheck size={15} /> : <PackageX size={15} />}
      {display(availability.availability_status)}
      {availability.quantity != null && <span className="text-xs opacity-80">· {availability.quantity} in stock</span>}
    </div>
  );
}
