import { VoiceError } from "./voice-errors";
import type { VoicePipelineResponse, VoiceProviderResult } from "./voice-types";

const VOICE_PIPELINE_URL = import.meta.env.VITE_VOICE_PIPELINE_URL?.trim() || "";

export function hasVoicePipeline() {
  return Boolean(VOICE_PIPELINE_URL);
}

export async function runVoicePipeline(audioBlob: Blob): Promise<VoiceProviderResult> {
  if (!VOICE_PIPELINE_URL) {
    throw new VoiceError("Voice pipeline is not configured.", "VOICE_PIPELINE_MISSING");
  }

  const formData = new FormData();
  formData.append("audio", audioBlob, "voice-input.webm");

  let response: Response;
  try {
    response = await fetch(VOICE_PIPELINE_URL, { method: "POST", body: formData });
  } catch {
    throw new VoiceError("The voice service could not be reached.", "VOICE_PIPELINE_NETWORK");
  }

  const body = (await response.json().catch(() => null)) as VoicePipelineResponse | null;
  if (!response.ok) {
    const message = typeof body === "object" && body && typeof body.english_text === "string"
      ? body.english_text
      : "The voice service could not process this recording.";
    throw new VoiceError(message, `VOICE_PIPELINE_${response.status}`);
  }

  const englishText = body?.english_text?.trim();
  const originalTranscript = body?.original_transcript?.trim() || englishText || "";
  if (!englishText) {
    throw new VoiceError("The voice service returned an empty transcript.", "VOICE_PIPELINE_EMPTY");
  }

  return {
    originalTranscript,
    detectedLanguage: body?.detected_language || null,
    detectedLanguageConfidence: body?.detected_language_confidence ?? null,
    englishText,
    translationApplied: Boolean(body?.translation_applied),
    provider: body?.provider || "voice-pipeline",
    submitDirectly: Boolean(body?.submit_directly),
  };
}
