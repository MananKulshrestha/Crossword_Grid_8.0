import { useState } from "react";
import { recordAudio } from "./audio-recorder";
import { browserSpeechSupported, transcribeInBrowser } from "./browser-speech-provider";
import { VoiceError } from "./voice-errors";
import { hasVoicePipeline, runVoicePipeline } from "./voice-api";
import type { VoiceCaptureResult, VoiceStage } from "./voice-types";

const DEFAULT_LANGUAGE = "en-IN";

export function useVoiceInput(
  onAutoSubmit: (text: string) => Promise<void>,
  onReviewReady?: (text: string) => void,
) {
  const [stage, setStage] = useState<VoiceStage>("idle");
  const [voiceResult, setVoiceResult] = useState<VoiceCaptureResult | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);

  async function start() {
    if (stage === "listening" || stage === "processing") return;

    setVoiceError(null);
    setVoiceResult(null);
    setStage("requesting-permission");

    try {
      let result;
      if (hasVoicePipeline()) {
        setStage("listening");
        const audioBlob = await recordAudio();
        setStage("processing");
        result = await runVoicePipeline(audioBlob);
      } else {
        setStage("listening");
        result = await transcribeInBrowser(DEFAULT_LANGUAGE);
      }

      const capture = {
        originalTranscript: result.originalTranscript,
        detectedLanguage: result.detectedLanguage,
        detectedLanguageConfidence: result.detectedLanguageConfidence,
        englishText: result.englishText,
        translationApplied: result.translationApplied,
        provider: result.provider,
      };

      setVoiceResult(capture);

      if (result.submitDirectly) {
        setStage("idle");
        await onAutoSubmit(result.englishText);
        return;
      }

      onReviewReady?.(result.englishText);
      setStage("review");
    } catch (error) {
      const message = error instanceof VoiceError ? error.message : "Voice input could not be completed.";
      setVoiceError(message);
      setStage("error");
    }
  }

  function dismissError() {
    setVoiceError(null);
    setStage("idle");
  }

  function resetReview() {
    setVoiceResult(null);
    setVoiceError(null);
    setStage("idle");
  }

  return {
    stage,
    voiceError,
    voiceResult,
    browserSpeechSupported: browserSpeechSupported(),
    pipelineConfigured: hasVoicePipeline(),
    start,
    dismissError,
    resetReview,
  };
}
