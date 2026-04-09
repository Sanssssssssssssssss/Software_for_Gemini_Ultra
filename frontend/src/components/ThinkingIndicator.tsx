import { useThinkingIndicator } from "../lib/thinking-indicator";

type ThinkingIndicatorProps = {
  active: boolean;
  phaseLabel?: string | null;
};

export function ThinkingIndicator({ active, phaseLabel }: ThinkingIndicatorProps) {
  const indicator = useThinkingIndicator(active);
  const label = phaseLabel || indicator.label;

  return (
    <div
      aria-label={label}
      aria-live="polite"
      className={`thinking-indicator${active ? " active" : ""}`}
    >
      <span className="thinking-indicator__frame" aria-hidden="true">
        {indicator.frame}
      </span>
      <span className="thinking-indicator__label">{label}</span>
    </div>
  );
}
