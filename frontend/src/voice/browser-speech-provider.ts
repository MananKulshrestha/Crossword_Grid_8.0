import { VoiceError } from "./voice-errors";
import type { VoiceProviderResult } from "./voice-types";

type SpeechRecognitionCtor = new () => SpeechRecognition;

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  const speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  return speech || null;
}

export function browserSpeechSupported() {
  return Boolean(getRecognitionCtor());
}

export async function transcribeInBrowser(language: string): Promise<VoiceProviderResult> {
  const Recognition = getRecognitionCtor();
  if (!Recognition) {
    throw new VoiceError("Speech recognition is not available in this browser.", "SPEECH_UNSUPPORTED");
  }

  return await new Promise<VoiceProviderResult>((resolve, reject) => {
    const recognition = new Recognition();
    recognition.lang = language;
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event) => {
      const transcript = event.results[0]?.[0]?.transcript?.trim();
      if (!transcript) {
        reject(new VoiceError("No speech was detected. Please try again.", "SPEECH_EMPTY"));
        return;
      }

      resolve({
        originalTranscript: transcript,
        detectedLanguage: language,
        detectedLanguageConfidence: null,
        englishText: transcript,
        translationApplied: false,
        provider: "browser-speech-recognition",
        submitDirectly: true,
      });
    };

    recognition.onerror = (event) => {
      const message = event.error === "not-allowed"
        ? "Microphone access is blocked in this browser."
        : "Speech recognition failed. Please try again.";
      reject(new VoiceError(message, event.error));
    };

    recognition.onnomatch = () => reject(new VoiceError("I could not understand that recording.", "SPEECH_NOMATCH"));
    recognition.start();
  });
}
