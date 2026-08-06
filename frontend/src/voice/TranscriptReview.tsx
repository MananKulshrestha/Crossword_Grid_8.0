import type { VoiceCaptureResult } from "./voice-types";

export function TranscriptReview({
  voiceResult,
  value,
  onChange,
  onCancel,
  onSubmit,
  disabled,
}: {
  voiceResult: VoiceCaptureResult;
  value: string;
  onChange: (value: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
  disabled: boolean;
}) {
  return (
    <div className="voice-review">
      <div className="voice-review-copy">
        <strong>Voice transcript ready</strong>
        <span>{voiceResult.translationApplied ? "Review the English version before sending." : "Review or edit before sending."}</span>
      </div>
      <div className="voice-review-fields">
        {voiceResult.translationApplied && (
          <p className="voice-original">
            <b>Original:</b> {voiceResult.originalTranscript}
          </p>
        )}
        <textarea
          rows={2}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Voice transcript will appear here"
          disabled={disabled}
        />
      </div>
      <div className="voice-review-actions">
        <button type="button" className="secondary voice-action" onClick={onCancel} disabled={disabled}>Cancel</button>
        <button type="button" className="primary voice-action" onClick={onSubmit} disabled={!value.trim() || disabled}>Send transcript</button>
      </div>
    </div>
  );
}
