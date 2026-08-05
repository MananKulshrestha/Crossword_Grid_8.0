export type Action = "SEARCH" | "REFINE" | "PRODUCT_DETAILS" | "COMPARE" | "CHECK_AVAILABILITY" | "UPDATE_CART" | "SHOW_CART" | "RESEARCH_EXTERNAL" | "RESET_SEARCH" | "HELP";
export interface Binding { product_id: string; sku_id: string; offer_id: string; catalog_version: string }
export interface Fact { label: string; typed_value: unknown; status: "VERIFIED" | "UNKNOWN" | "NOT_MODELED" | "DERIVED"; as_of?: string }
export interface Entry { result_entry_id: string; display_position: number; binding: Binding; title: string; facts: Fact[]; matched_criteria: string[]; unknown_criteria: string[] }
export interface ActiveResultBinding { result_entry_id: string; display_position: number; binding: Binding; context_ref?: string | null }
export interface CartItem { cart_item_id: string; binding: Binding; quantity: number; unit_price: { amount_paise: number }; line_subtotal: { amount_paise: number }; availability_status: string }
export interface Cart { cart_id: string; cart_version: number; state_version: number; items: CartItem[]; item_count: number; total_quantity: number; subtotal: { amount_paise: number }; warnings: string[] }
export interface Clarification { question: string; choice_ids: string[] }
export interface Comparison { bindings: Binding[]; rows: { field_id: string; label: string; cells: { value: unknown; status: string }[] }[] }
export interface ProductDetails { binding: Binding; title?: string; facts: Fact[]; variants: Binding[] }
export interface ShopperResponse { response_id: string; action: Action; terminal_state: string; summary: string; language_code?: string; facts: Fact[]; search_entries: Entry[]; result_set_id?: string; details?: ProductDetails; comparison?: Comparison; availability?: { availability_status: string; quantity?: number; as_of?: string; truth_status: string }; cart?: Cart; clarification?: Clarification; clarification_reason_code?: string; warnings: string[]; suggestions?: { suggestions: { suggestion_id: string; label: string; candidate: { action_type: Action; payload: Record<string, unknown> }; signed_action_token: string }[] } }
export interface TurnResult { status: "COMPLETED" | "IN_PROGRESS" | "REJECTED"; http_status: number; response?: ShopperResponse; status_ref?: string }
export interface ActiveResults { result_set_id?: string | null; items: Entry[] }
export interface Session { session_id: string; state_version: number; cart_version: number; cart: Cart; acknowledged_result_set_id?: string | null; acknowledged_entries: ActiveResultBinding[]; query_state: { hard_constraints: { field_id: string; values: unknown[] }[]; soft_preferences: { field_id: string; values: unknown[] }[] }; model: { speech_configured: boolean; configured: boolean; cart_ready: boolean } }
export interface CatalogPage { total: number; offset: number; limit: number; items: (Entry & { catalog_position: number })[] }
export interface CatalogFacets { facets: Record<string, string[]> }
