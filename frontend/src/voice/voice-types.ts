export type VoiceStage =
  | "idle"
  | "requesting-permission"
  | "listening"
  | "processing"
  | "review"
  | "error";

export interface VoiceCaptureResult {
  originalTranscript: string;
  detectedLanguage: string | null;
  detectedLanguageConfidence: number | null;
  englishText: string;
  translationApplied: boolean;
  provider: string;
}

export interface VoiceProviderResult extends VoiceCaptureResult {
  submitDirectly: boolean;
}

export interface VoicePipelineResponse {
  original_transcript?: string;
  detected_language?: string | null;
  detected_language_confidence?: number | null;
  english_text?: string;
  translation_applied?: boolean;
  provider?: string;
  submit_directly?: boolean;
}
