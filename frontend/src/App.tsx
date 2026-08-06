import { useEffect, useRef } from "react";
import { AlertTriangle, ShoppingBag, Sparkles } from "lucide-react";
import { useChat } from "@/hooks/use-chat";
import { MessageBubble } from "@/components/chat/message";
import { Composer } from "@/components/chat/composer";
import { ModeToggle } from "@/components/chat/mode-toggle";

const STARTERS = [
  "Show me running shoes under ₹2000",
  "Compare the first two results",
  "What's in my cart?",
  "Is it available in size L?",
];

export default function App() {
  const { ready, bootError, messages, sending, send, titleFor, mode, setMode } = useChat();
  const viewportRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    viewportRef.current?.scrollTo({ top: viewportRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  return (
    <div className="flex min-h-screen flex-col bg-fk-mist">
      <header className="border-b border-border bg-fk-blue shadow-sm">
        <div className="page-shell flex h-14 items-center gap-2.5">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-white/15 text-white">
            <ShoppingBag size={17} />
          </div>
          <div className="leading-tight text-white">
            <p className="text-sm font-extrabold tracking-tight">Flipkart Assistant</p>
            <p className="text-[11px] font-medium text-white/70">AI shopping, grounded in the live catalog</p>
          </div>
          <div className="ml-auto">
            <ModeToggle mode={mode} onChange={setMode} />
          </div>
        </div>
      </header>

      <main className="page-shell flex min-h-0 flex-1 flex-col py-4">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-card">
          <div ref={viewportRef} className="min-h-0 flex-1 overflow-y-auto">
              <div className="mx-auto flex max-w-3xl flex-col gap-4 p-4">
                {bootError && (
                  <div className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700">
                    <AlertTriangle size={16} /> {bootError}
                  </div>
                )}
                {!bootError && messages.length === 0 && (
                  <div className="flex flex-col items-center gap-4 py-16 text-center">
                    <div className="grid h-12 w-12 place-items-center rounded-full bg-fk-blue/10 text-fk-blue">
                      <Sparkles size={22} />
                    </div>
                    <div>
                      <p className="text-base font-bold">What are you shopping for today?</p>
                      <p className="mt-1 text-sm text-muted-foreground">
                        Search, compare, check stock, and manage your cart — all in chat.
                      </p>
                    </div>
                    <div className="flex flex-wrap justify-center gap-2">
                      {STARTERS.map((starter) => (
                        <button
                          key={starter}
                          onClick={() => void send(starter)}
                          className="focus-ring rounded-full border border-border bg-card px-3.5 py-1.5 text-xs font-semibold text-foreground shadow-sm transition hover:border-fk-blue hover:text-fk-blue"
                        >
                          {starter}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {messages.map((msg) => (
                  <MessageBubble key={msg.id} msg={msg} titleFor={titleFor} onFollowup={(label) => void send(label)} />
                ))}
                {sending && (
                  <div className="flex items-center gap-2 px-1 text-sm text-muted-foreground">
                    <span className="flex gap-1">
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-fk-blue [animation-delay:-0.2s]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-fk-blue [animation-delay:-0.1s]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-fk-blue" />
                    </span>
                    thinking…
                  </div>
                )}
              </div>
          </div>
          <div className="mx-auto w-full max-w-3xl">
            <Composer onSend={(text) => void send(text)} disabled={!ready || sending || !!bootError} />
          </div>
        </div>
      </main>
    </div>
  );
}
