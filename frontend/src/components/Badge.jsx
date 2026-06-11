import React from "react";

// Small uppercase status pill with a colored status dot.
const TONES = {
  ok: { dot: "bg-ok", text: "text-ok", border: "border-green-200", bg: "bg-green-50" },
  warn: { dot: "bg-warn", text: "text-yellow-700", border: "border-yellow-200", bg: "bg-yellow-50" },
  err: { dot: "bg-err", text: "text-err", border: "border-red-200", bg: "bg-red-50" },
  idle: { dot: "bg-gray-400", text: "text-gray-600", border: "border-line", bg: "bg-gray-50" },
  info: { dot: "bg-electric", text: "text-electric", border: "border-blue-200", bg: "bg-blue-50" },
};

export default function Badge({ label, tone = "idle", testId, pulse = false }) {
  const t = TONES[tone] || TONES.idle;
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-none border ${t.border} ${t.bg} ${t.text} uppercase-label`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${t.dot} ${pulse ? "animate-pulse" : ""}`} />
      {label}
    </span>
  );
}
