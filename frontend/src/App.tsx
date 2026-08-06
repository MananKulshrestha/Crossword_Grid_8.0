import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Bot, Check, ChevronRight, CircleUserRound, GitCompareArrows, History, Menu,
  MessageSquarePlus, PackageCheck, PanelRightClose, Search, Send, ShoppingCart,
  Sparkles, Star, X, Zap,
} from "lucide-react";
import { ApiError, createSession, sendTurn } from "./api";
import type { CartSnapshot, ChatMessage, Comparison, SearchEntry, TurnResult } from "./types";
import { TranscriptReview } from "./voice/TranscriptReview";
import { VoiceButton } from "./voice/VoiceButton";
import { VoiceStatus } from "./voice/VoiceStatus";
import { useVoiceInput } from "./voice/useVoiceInput";

const STARTERS = [
  "Show me highly rated smartphones under 20000",
  "Find running shoes in stock",
  "Show my cart",
];

const money = (paise: number | null | undefined) =>
  paise == null ? "Price unavailable" : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(paise / 100);

function ProductCard({ item, send, busy }: { item: SearchEntry; send: (text: string) => void; busy: boolean }) {
  const initials = (item.category || item.brand || "Product").slice(0, 2).toUpperCase();
  return (
    <article className="product-card">
      <div className="product-art" aria-label={`${item.category || "Product"} image unavailable`}><span>{initials}</span></div>
      <div className="product-body">
        <p className="eyebrow">{item.brand || item.category || "Catalog item"}</p>
        <h3>{item.title}</h3>
        <div className="product-meta">
          {item.rating != null && <span className="rating"><Star size={12} fill="currentColor" /> {item.rating.toFixed(1)}</span>}
          <span className={item.availability_status?.toLowerCase().includes("stock") ? "stock" : "muted"}>{item.availability_status || "Status unavailable"}</span>
        </div>
        <strong className="price">{money(item.price_paise)}</strong>
        <p className="sku">SKU {item.sku_id}</p>
        <div className="card-actions">
          <button className="primary compact" disabled={busy} onClick={() => send(`Add SKU ${item.sku_id} to my cart`)}><ShoppingCart size={15} /> Add</button>
          <button className="secondary compact" disabled={busy} onClick={() => send(`Show details for SKU ${item.sku_id}`)}>Details</button>
          <button className="icon-button" disabled={busy} aria-label={`Check availability for ${item.title}`} onClick={() => send(`Check availability for SKU ${item.sku_id}`)}><PackageCheck size={17} /></button>
        </div>
      </div>
    </article>
  );
}

function ComparisonView({ comparison }: { comparison: Comparison }) {
  const skuIds = Array.from(new Set(comparison.rows.flatMap((row) => row.cells.map((cell) => cell.sku_id))));
  return <section className="result-block">
    {comparison.summary && <p className="summary-callout"><Sparkles size={16} /> {comparison.summary}</p>}
    <div className="table-wrap"><table><thead><tr><th>Feature</th>{skuIds.map((id) => <th key={id}>{id}</th>)}</tr></thead>
      <tbody>{comparison.rows.map((row) => <tr key={row.field}><th>{row.field.replaceAll("_", " ")}</th>{skuIds.map((id) => <td key={id}>{String(row.cells.find((cell) => cell.sku_id === id)?.value ?? "-")}</td>)}</tr>)}</tbody>
    </table></div>
  </section>;
}

function CartView({ cart }: { cart: CartSnapshot }) {
  return <section className="result-block cart-block">
    <div className="cart-heading"><div><span>{cart.total_quantity} item{cart.total_quantity === 1 ? "" : "s"}</span><strong>{money(cart.subtotal_paise)}</strong></div><ShoppingCart size={22} /></div>
    {cart.items.length === 0 ? <p className="muted">Your cart is empty.</p> : cart.items.map((item) => <div className="cart-line" key={item.cart_item_id}><div><strong>{item.title}</strong><small>{item.quantity} × {money(item.unit_price_paise)} · {item.availability_status}</small></div><strong>{money(item.line_subtotal_paise)}</strong></div>)}
  </section>;
}

function ResultContent({ result, send, busy }: { result: TurnResult; send: (text: string) => void; busy: boolean }) {
  const products = result.search_result?.entries || (result.product_details?.entry ? [result.product_details.entry] : []);
  return <>
    {products.length > 0 && <div className="product-grid">{products.map((item) => <ProductCard key={item.sku_id} item={item} send={send} busy={busy} />)}</div>}
    {result.comparison && <ComparisonView comparison={result.comparison} />}
    {result.availability && <div className="availability"><PackageCheck size={18} /><div><strong>{result.availability.availability_status || (result.availability.found ? "Available" : "Not found")}</strong>{result.availability.quantity != null && <span>{result.availability.quantity} units reported by catalog</span>}</div></div>}
    {result.cart && <CartView cart={result.cart} />}
    {result.followups.length > 0 && <div className="chips">{result.followups.map((item, index) => <button key={`${item.label}-${index}`} disabled={busy} onClick={() => send(item.label)}>{item.label}<ChevronRight size={14} /></button>)}</div>}
  </>;
}

function AssistantMessage({ message, send, busy }: { message: ChatMessage; send: (text: string) => void; busy: boolean }) {
  return <div className="message assistant-message"><div className="avatar"><Bot size={18} /></div><div className="message-content"><div className="message-label">GridKart Assistant <span>Live catalog</span></div><p>{message.content}</p>{message.result && <ResultContent result={message.result} send={send} busy={busy} />}</div></div>;
}

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(true);
  const [cart, setCart] = useState<CartSnapshot | null>(null);
  const [lastProducts, setLastProducts] = useState<SearchEntry[]>([]);
  const [voiceDraft, setVoiceDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  async function startSession() {
    setBooting(true); setError(null); setMessages([]); setCart(null); setLastProducts([]);
    try { setSessionId(await createSession()); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not create a shopping session."); }
    finally { setBooting(false); }
  }

  useEffect(() => {
    let active = true;
    createSession()
      .then((id) => { if (active) setSessionId(id); })
      .catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : "Could not create a shopping session."); })
      .finally(() => { if (active) setBooting(false); });
    return () => { active = false; };
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  async function submit(text: string) {
    const clean = text.trim();
    if (!clean || !sessionId || busy) return;
    setQuery(""); setError(null); setBusy(true); setSidebarOpen(false);
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content: clean }]);
    try {
      const result = await sendTurn(sessionId, clean);
      if (result.cart) setCart(result.cart);
      if (result.search_result?.entries) setLastProducts(result.search_result.entries);
      setMessages((current) => [...current, { id: crypto.randomUUID(), role: "assistant", content: result.message, result }]);
    } catch (err) {
      const apiError = err instanceof ApiError ? err : new ApiError("Something went wrong while processing this request.");
      setError(`${apiError.message}${apiError.code ? ` (${apiError.code})` : ""}`);
      setMessages((current) => [...current, { id: crypto.randomUUID(), role: "assistant", content: apiError.message }]);
    } finally { setBusy(false); }
  }

  const voice = useVoiceInput(
    async (text) => {
      await submit(text);
    },
    (text) => {
      setVoiceDraft(text);
    },
  );

  function onSubmit(event: FormEvent) { event.preventDefault(); void submit(query); }
  function submitVoiceReview() {
    const clean = voiceDraft.trim();
    if (!clean || busy) return;
    voice.resetReview();
    void submit(clean);
  }
  const title = messages.find((message) => message.role === "user")?.content || "New shopping session";

  return <div className="app-shell">
    <header className="topbar">
      <button className="mobile-menu" aria-label="Open conversations" onClick={() => setSidebarOpen(true)}><Menu /></button>
      <div className="brand"><div className="brand-mark"><Zap size={19} fill="currentColor" /></div><div><strong>GridKart</strong><span>smart shopping</span></div></div>
      <form className="header-search" onSubmit={(event) => { event.preventDefault(); void submit(query); }}><Search size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search products with your assistant" disabled={booting || busy} /></form>
      <nav className="header-actions">
        <button onClick={() => void submit("Show my cart")} disabled={!sessionId || busy} aria-label="Open cart"><ShoppingCart size={20} />{cart && cart.total_quantity > 0 && <b>{cart.total_quantity}</b>}<span>Cart</span></button>
        <button className="profile" aria-label="Profile"><CircleUserRound size={21} /><span>Account</span></button>
      </nav>
    </header>
    <main className="workspace">
      {sidebarOpen && <button className="scrim" aria-label="Close conversations" onClick={() => setSidebarOpen(false)} />}
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <div className="side-title"><span>Conversations</span><button className="close-mobile" onClick={() => setSidebarOpen(false)}><X size={18} /></button></div>
        <button className="new-chat" onClick={() => void startSession()}><MessageSquarePlus size={17} /> New conversation</button>
        <div className="history-label"><History size={14} /> Current</div>
        <button className="history-item active"><span>{title}</span><small>{sessionId ? "Live session" : "Connecting"}</small></button>
        <div className="source-note"><Check size={15} /><p><strong>Verified catalog</strong><span>Results and cart data come directly from the connected SQL services.</span></p></div>
      </aside>
      <section className="conversation">
        <div className="conversation-head"><div><p>AI SHOPPING WORKSPACE</p><h1>{messages.length ? "Your shopping assistant" : "Find the right product, faster"}</h1></div><button className="context-toggle" onClick={() => setContextOpen((value) => !value)}><PanelRightClose size={18} /><span>Context</span></button></div>
        <div className="thread">
          {!messages.length && !booting && <section className="welcome"><div className="welcome-icon"><Sparkles /></div><p className="kicker">POWERED BY YOUR LIVE CATALOG</p><h2>What are you shopping for today?</h2><p>Describe what you need in plain language. I can search, refine, compare, check stock, and manage your cart.</p><div className="starter-grid">{STARTERS.map((starter, index) => <button key={starter} onClick={() => void submit(starter)}><span>0{index + 1}</span>{starter}<ChevronRight size={17} /></button>)}</div></section>}
          {messages.map((message) => message.role === "user" ? <div className="message user-message" key={message.id}><div className="message-content"><div className="message-label">You</div><p>{message.content}</p></div></div> : <AssistantMessage key={message.id} message={message} send={(text) => void submit(text)} busy={busy} />)}
          {busy && <div className="message assistant-message"><div className="avatar"><Bot size={18} /></div><div className="thinking"><div><i /><i /><i /></div><span>Checking the live catalog. This can take up to a minute.</span></div></div>}
          {error && <div className="error-banner"><span>{error}</span><button onClick={() => setError(null)}><X size={16} /></button></div>}
          <div ref={endRef} />
        </div>
        <form className="composer" onSubmit={onSubmit}>
          <div className="composer-inner">
            <textarea rows={1} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); onSubmit(event); } }} placeholder={booting ? "Connecting to the shopping server…" : "Ask about products, compare items, or manage your cart…"} disabled={booting || busy || !sessionId} />
            <VoiceButton
              stage={voice.stage}
              disabled={booting || busy || !sessionId || (!voice.browserSpeechSupported && !voice.pipelineConfigured)}
              onClick={() => void voice.start()}
            />
            <button type="submit" disabled={!query.trim() || booting || busy || !sessionId} aria-label="Send message"><Send size={18} /></button>
          </div>
          <VoiceStatus
            stage={voice.stage}
            voiceError={voice.voiceError || (!voice.browserSpeechSupported && !voice.pipelineConfigured ? "Voice input needs either browser speech recognition or a configured VITE_VOICE_PIPELINE_URL." : null)}
            voiceResult={voice.voiceResult}
            onDismissError={voice.dismissError}
          />
          {voice.stage === "review" && voice.voiceResult && (
            <TranscriptReview
              voiceResult={voice.voiceResult}
              value={voiceDraft}
              onChange={setVoiceDraft}
              onCancel={() => {
                setVoiceDraft("");
                voice.resetReview();
              }}
              onSubmit={submitVoiceReview}
              disabled={busy}
            />
          )}
          <small>Responses use live backend and SQL catalog data · Session {sessionId ? sessionId.slice(0, 8) : "not connected"}</small>
        </form>
      </section>
      <aside className={`context-panel ${contextOpen ? "" : "closed"}`}>
        <div className="context-title"><span>Session context</span><button onClick={() => setContextOpen(false)}><X size={17} /></button></div>
        <section><p className="panel-label">CATALOG CONNECTION</p><div className="status-card"><i className={sessionId ? "online" : ""} /><div><strong>{booting ? "Connecting" : sessionId ? "Live session" : "Disconnected"}</strong><span>{sessionId ? "SQL-backed services ready" : "Start the backend and retry"}</span></div></div></section>
        <section><p className="panel-label">RECENT RESULTS</p>{lastProducts.length ? <div className="shortlist">{lastProducts.slice(0, 4).map((item, index) => <button key={item.sku_id} onClick={() => void submit(`Show details for SKU ${item.sku_id}`)}><b>{index + 1}</b><span>{item.title}<small>{money(item.price_paise)}</small></span></button>)}</div> : <p className="empty-panel">Products from your latest search will appear here.</p>}</section>
        <section><p className="panel-label">QUICK ACTIONS</p><div className="quick-actions"><button disabled={busy || !sessionId} onClick={() => void submit("Show my cart")}><ShoppingCart size={16} /> View cart</button><button disabled={busy || !sessionId || lastProducts.length < 2} onClick={() => void submit("Compare the first two results")}><GitCompareArrows size={16} /> Compare first two</button></div></section>
        <section className="session-card"><Bot size={18} /><div><strong>One continuous conversation</strong><p>References like “the second one” use this session’s verified results.</p></div></section>
      </aside>
    </main>
  </div>;
}
