export type Action =
  | "CHITCHAT"
  | "SEARCH"
  | "REFINE"
  | "PRODUCT_DETAILS"
  | "COMPARE"
  | "CHECK_AVAILABILITY"
  | "SHOW_CART"
  | "UPDATE_CART";

export interface SearchEntry {
  sku_id: string;
  product_id: string;
  offer_id: string;
  title: string;
  brand: string | null;
  category: string | null;
  price_paise: number | null;
  rating: number | null;
  availability_status: string | null;
  quantity: number | null;
  rerank_score: number | null;
}

export interface CartItem {
  cart_item_id: string;
  sku_id: string;
  product_id: string;
  offer_id: string;
  title: string;
  quantity: number;
  unit_price_paise: number;
  line_subtotal_paise: number;
  availability_status: string;
}

export interface CartSnapshot {
  cart_version: number;
  item_count: number;
  total_quantity: number;
  subtotal_paise: number;
  items: CartItem[];
}

export interface Comparison {
  rows: Array<{ field: string; cells: Array<{ sku_id: string; value: unknown }> }>;
  summary: string | null;
}

export interface TurnResult {
  status: "OK" | "ERROR";
  message: string;
  action: Action | null;
  search_result: {
    entries: SearchEntry[];
    result_set_id: string | null;
    verified_sku_ids: string[];
    hallucinated_sku_ids: string[];
  } | null;
  product_details: { found: boolean; entry: SearchEntry | null } | null;
  comparison: Comparison | null;
  availability: { found: boolean; availability_status: string | null; quantity: number | null } | null;
  cart: CartSnapshot | null;
  followups: Array<{ label: string; action: Action; params: Record<string, unknown> }>;
  error_code: string | null;
  trace: Array<{ stage: string; input: Record<string, unknown>; output: Record<string, unknown>; ok: boolean }>;
}

export interface SessionState {
  session_id: string;
  chat_history: Array<{ role: "user" | "assistant"; content: string; created_at: string }>;
  last_results: SearchEntry[];
  cart_version: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  result?: TurnResult;
}
