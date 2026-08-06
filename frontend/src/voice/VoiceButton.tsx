import { Mic, Square } from "lucide-react";
import type { VoiceStage } from "./voice-types";

const LABELS: Record<VoiceStage, string> = {
  idle: "Start voice input",
  "requesting-permission": "Requesting microphone access",
  listening: "Listening",
  processing: "Processing voice input",
  review: "Voice transcript ready",
  error: "Retry voice input",
};

export function VoiceButton({
  stage,
  disabled,
  onClick,
}: {
  stage: VoiceStage;
  disabled: boolean;
  onClick: () => void;
}) {
  const active = stage === "requesting-permission" || stage === "listening" || stage === "processing";

  return (
    <button
      type="button"
      className={`voice-button ${active ? "active" : ""}`}
      disabled={disabled}
      aria-label={LABELS[stage]}
      title={LABELS[stage]}
      onClick={onClick}
    >
      {stage === "listening" ? <Square size={17} /> : <Mic size={17} />}
    </button>
  );
}
