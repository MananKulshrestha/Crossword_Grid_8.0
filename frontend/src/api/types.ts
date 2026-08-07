// Mirrors src/fkgrid/contracts.py exactly. Keep in sync with the backend.

export type Action =
  | "CHITCHAT"
  | "SEARCH"
  | "REFINE"
  | "PRODUCT_DETAILS"
  | "COMPARE"
  | "CHECK_AVAILABILITY"
  | "SHOW_CART"
  | "UPDATE_CART"
  | "CONSTRAINT_BASKET";

export interface SearchEntry {
  sku_id: string;
  product_id: string;
  offer_id: string;
  title: string;
  brand?: string | null;
  category?: string | null;
  price_paise?: number | null;
  rating?: number | null;
  availability_status?: string | null;
  quantity?: number | null;
  rerank_score?: number | null;
}

export interface SearchResult {
  entries: SearchEntry[];
  result_set_id?: string | null;
  verified_sku_ids: string[];
  hallucinated_sku_ids: string[];
}

export interface ProductDetails {
  found: boolean;
  entry?: SearchEntry | null;
}

export interface ComparisonCell {
  sku_id: string;
  value: unknown;
}

export interface ComparisonRow {
  field: string;
  cells: ComparisonCell[];
}

export interface Comparison {
  rows: ComparisonRow[];
  summary?: string | null;
}

export interface Availability {
  found: boolean;
  availability_status?: string | null;
  quantity?: number | null;
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

export interface BasketSlot {
  label: string;
  entry: SearchEntry;
  candidate_count: number;
  best_available_score: number;
  chosen_score: number;
}

export interface Basket {
  slots: BasketSlot[];
  budget_paise?: number | null;
  total_paise: number;
  headroom_paise?: number | null;
  naive_total_paise?: number | null;
  unfilled: string[];
  explanation?: string | null;
}

export interface FollowUpSuggestion {
  label: string;
  action: Action;
  params: Record<string, unknown>;
}

export type TurnStatus = "OK" | "ERROR";

export interface TraceStep {
  stage: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  ok: boolean;
}

export interface TurnResult {
  status: TurnStatus;
  message: string;
  action?: Action | null;
  search_result?: SearchResult | null;
  product_details?: ProductDetails | null;
  comparison?: Comparison | null;
  availability?: Availability | null;
  cart?: CartSnapshot | null;
  basket?: Basket | null;
  followups: FollowUpSuggestion[];
  error_code?: string | null;
  trace: TraceStep[];
}

export interface CreateSessionResponse {
  session_id: string;
}
