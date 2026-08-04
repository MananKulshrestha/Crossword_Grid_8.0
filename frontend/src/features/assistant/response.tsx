import { AlertCircle, CheckCircle2, Lightbulb, PackageSearch } from "lucide-react";
import { Link } from "react-router-dom";
import type { ShopperResponse } from "../../api/types";
import { Button } from "../../components/ui";
import { useShopper } from "../../app/session-provider";

export function AssistantResponse({ response }: { response?: ShopperResponse }) {
  const { send } = useShopper();
  if (!response) return null;
  const warning = response.terminal_state.includes("NO_") || response.terminal_state.includes("UNAVAILABLE") || response.terminal_state.includes("FAILED");
  return <section className={`rounded-xl border p-4 ${warning ? "border-amber-200 bg-amber-50" : "border-blue-100 bg-blue-50/70"}`}><div className="flex gap-3"><div className={`mt-0.5 rounded-full p-2 ${warning ? "bg-amber-100 text-amber-700" : "bg-white text-fk-blue"}`}>{warning ? <AlertCircle size={18}/> : <CheckCircle2 size={18}/>}</div><div className="min-w-0 flex-1"><p className="eyebrow">Shopping Assistant</p><p className="mt-1 text-sm font-medium leading-6">{response.summary}</p>{response.warnings.length > 0 && <p className="mt-1 text-xs text-slate-500">{response.warnings.map((item) => item.replaceAll("_", " ")).join(" · ")}</p>}{response.clarification && <div className="mt-3 flex flex-wrap gap-2">{response.clarification.choice_ids.map((choice) => <Button key={choice} variant="outline" className="h-8" onClick={() => void send(choice)}>{choice.replaceAll("_", " ")}</Button>)}</div>}{response.suggestions?.suggestions.length ? <div className="mt-3 flex flex-wrap gap-2">{response.suggestions.suggestions.map((suggestion) => <Button key={suggestion.suggestion_id} variant="outline" className="h-8" onClick={() => void send(undefined, suggestion.candidate.action_type, suggestion.candidate.payload)}><Lightbulb size={14}/>{suggestion.label}</Button>)}</div> : null}{response.comparison && <Link className="mt-3 inline-flex items-center gap-1 text-sm font-bold text-fk-blue hover:underline" to="/compare"><PackageSearch size={15}/> View comparison</Link>}</div></div></section>;
}
