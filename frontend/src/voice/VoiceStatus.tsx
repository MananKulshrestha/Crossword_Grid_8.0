import { Languages, LoaderCircle, Mic, TriangleAlert } from "lucide-react";
import type { VoiceCaptureResult, VoiceStage } from "./voice-types";

function stageCopy(stage: VoiceStage) {
  if (stage === "requesting-permission") return "Waiting for microphone permission...";
  if (stage === "listening") return "Listening for up to 7 seconds...";
  if (stage === "processing") return "Converting speech into a shopping request...";
  if (stage === "review") return "English transcript ready for review.";
  if (stage === "error") return "Voice input needs attention.";
  return "Voice input is optional. Use it anytime.";
}

export function VoiceStatus({
  stage,
  voiceError,
  voiceResult,
  onDismissError,
}: {
  stage: VoiceStage;
  voiceError: string | null;
  voiceResult: VoiceCaptureResult | null;
  onDismissError: () => void;
}) {
  if (voiceError) {
    return (
      <div className="voice-status error">
        <TriangleAlert size={15} />
        <span>{voiceError}</span>
        <button type="button" onClick={onDismissError}>Dismiss</button>
      </div>
    );
  }

  return (
    <div className={`voice-status ${stage}`}>
      {stage === "review" ? <Languages size={15} /> : stage === "idle" ? <Mic size={15} /> : <LoaderCircle size={15} className="spin" />}
      <span>{stageCopy(stage)}</span>
      {voiceResult?.detectedLanguage && (
        <small>
          {voiceResult.translationApplied ? `Translated from ${voiceResult.detectedLanguage}` : `Detected ${voiceResult.detectedLanguage}`}
        </small>
      )}
    </div>
  );
}
