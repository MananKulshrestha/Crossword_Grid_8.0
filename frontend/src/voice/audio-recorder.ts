import { VoiceError } from "./voice-errors";

export async function recordAudio(durationMs = 7000): Promise<Blob> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new VoiceError("This browser does not support microphone recording.", "MIC_UNSUPPORTED");
  }

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  try {
    return await new Promise<Blob>((resolve, reject) => {
      const chunks: BlobPart[] = [];
      const recorder = new MediaRecorder(stream);

      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      });
      recorder.addEventListener("stop", () => resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" })));
      recorder.addEventListener("error", () => reject(new VoiceError("Microphone capture failed.", "MIC_RECORDING_FAILED")));

      recorder.start();
      window.setTimeout(() => {
        if (recorder.state !== "inactive") recorder.stop();
      }, durationMs);
    });
  } finally {
    stream.getTracks().forEach((track) => track.stop());
  }
}
