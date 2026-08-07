import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Mic, SendHorizonal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export function Composer({ onSend, disabled }: { onSend: (text: string) => void; disabled?: boolean }) {
  const [value, setValue] = useState("");
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const [voiceSupported, setVoiceSupported] = useState(true);

  useEffect(() => {
    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setVoiceSupported(false);
      return;
    }

    const recognition: SpeechRecognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";

    recognition.onresult = (event) => {
      const transcript = event.results[0]?.[0]?.transcript ?? "";
      setValue((prev) => (prev ? `${prev} ${transcript}` : transcript));
    };
    recognition.onend = () => setListening(false);
    recognition.onerror = () => setListening(false);

    recognitionRef.current = recognition;
    return () => recognition.stop();
  }, []);

  const toggleListening = () => {
    if (disabled || !recognitionRef.current) return;
    if (listening) {
      recognitionRef.current.stop();
      setListening(false);
    } else {
      recognitionRef.current.start();
      setListening(true);
    }
  };

  const submit = () => {
    if (!value.trim() || disabled) return;
    onSend(value);
    setValue("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="flex items-end gap-2 border-t border-border bg-card p-3">
      <Textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask for anything — jeans under 1000, compare these, add to cart…"
        rows={1}
        className="max-h-32 min-h-11"
        disabled={disabled}
      />
      <Button
        size="icon"
        variant={listening ? "default" : "outline"}
        onClick={toggleListening}
        disabled={disabled || !voiceSupported}
        aria-label={listening ? "Stop voice input" : "Start voice input"}
        title={voiceSupported ? undefined : "Voice input isn't supported in this browser"}
        className={cn(listening && "animate-pulse")}
      >
        <Mic size={17} />
      </Button>
      <Button size="icon" onClick={submit} disabled={disabled || !value.trim()} aria-label="Send">
        <SendHorizonal size={17} />
      </Button>
    </div>
  );
}
