import { Sparkles } from "lucide-react";
import type { Comparison } from "@/api/types";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { display, inr } from "@/lib/utils";

function renderValue(field: string, value: unknown) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const key = field.trim().toLowerCase();
  if (key.includes("price")) return inr(value as number);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.map((item) => display(item)).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return display(value);
}

export function CompareTable({ comparison, titleFor }: { comparison: Comparison; titleFor: (skuId: string) => string }) {
  if (!comparison.rows.length) return <p className="text-sm text-muted-foreground">Nothing to compare.</p>;
  const skuIds = comparison.rows[0].cells.map((cell) => cell.sku_id);

  return (
    <div className="space-y-3">
      <div className="overflow-hidden rounded-xl border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-36">Attribute</TableHead>
              {skuIds.map((sku) => (
                <TableHead key={sku} className="min-w-[10rem] text-foreground">
                  <span className="line-clamp-2 whitespace-normal text-xs font-bold normal-case leading-4">{titleFor(sku)}</span>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {comparison.rows.map((row) => (
              <TableRow key={row.field}>
                <TableCell className="font-semibold text-muted-foreground">{display(row.field)}</TableCell>
                {row.cells.map((cell) => (
                  <TableCell key={`${row.field}-${cell.sku_id}`} className="text-sm">
                    {renderValue(row.field, cell.value)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {comparison.summary && (
        <div className="flex gap-2 rounded-lg border border-blue-100 bg-blue-50/70 p-3">
          <Sparkles size={16} className="mt-0.5 shrink-0 text-fk-blue" />
          <p className="text-sm leading-5 text-foreground">{comparison.summary}</p>
        </div>
      )}
    </div>
  );
}
