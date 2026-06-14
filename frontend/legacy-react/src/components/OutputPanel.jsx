import React from "react";
import { Copy, FileText, Hash, Loader2, Server } from "lucide-react";
import Badge from "./Badge";
import JsonViewer from "./JsonViewer";

const STATUS_TONE = {
  PENDING: "warn",
  PROCESSING: "info",
  COMPLETED: "ok",
  FAILED: "err",
};

function MetaStrip({ correlationId, job, result }) {
  const copy = (txt) => navigator.clipboard?.writeText(txt);
  return (
    <div className="px-6 py-3 border-b border-line flex flex-wrap items-center gap-x-6 gap-y-2 bg-gray-50/40">
      <div className="flex items-center gap-2">
        <Hash size={13} className="text-muted" />
        <span className="uppercase-label text-muted">Correlation ID</span>
        <code
          data-testid="correlation-id"
          className="text-xs font-mono bg-white border border-line px-2 py-0.5"
        >
          {correlationId || "—"}
        </code>
        {correlationId && (
          <button onClick={() => copy(correlationId)} className="text-muted hover:text-ink">
            <Copy size={13} />
          </button>
        )}
      </div>
      {job && (
        <div className="flex items-center gap-2">
          <Server size={13} className="text-muted" />
          <span className="uppercase-label text-muted">Job</span>
          <code className="text-xs font-mono">{job.jobId?.slice(0, 12)}…</code>
          <Badge
            label={job.status}
            tone={STATUS_TONE[job.status] || "idle"}
            testId="job-status-badge"
            pulse={job.status === "PROCESSING" || job.status === "PENDING"}
          />
        </div>
      )}
      {result && (
        <div className="flex items-center gap-2 ml-auto">
          {result.ocrUsed && (
            <Badge label={`OCR ${result.ocrEngine || ""}`} tone="info" testId="ocr-badge" />
          )}
          <Badge
            label={result.mock ? "Mock LLM" : result.model}
            tone={result.mock ? "warn" : "info"}
            testId="model-badge"
          />
          <span className="text-xs font-mono text-muted">{result.processingMs}ms</span>
        </div>
      )}
    </div>
  );
}

function OutputBody({ result, loading, outputTab, mode }) {
  if (!result && !loading) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-muted gap-3 py-24">
        <FileText size={40} strokeWidth={1} />
        <p className="text-sm font-mono">Run an extraction to see structured output here.</p>
      </div>
    );
  }
  if (loading && !result) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-muted gap-3 py-24">
        <Loader2 size={32} className="animate-spin" />
        <p className="text-sm font-mono">
          {mode === "async" ? "Polling async job…" : "Converting & extracting…"}
        </p>
      </div>
    );
  }
  if (outputTab === "markdown") {
    return <JsonViewer data={result.markdown} testId="markdown-output" />;
  }
  return <JsonViewer data={result.structured} testId="json-output" />;
}

// Right column: correlation/job meta, errors, output tabs and the viewer.
export default function OutputPanel({
  result,
  job,
  correlationId,
  loading,
  error,
  outputTab,
  setOutputTab,
  mode,
}) {
  const tabs = [
    { k: "json", label: "Structured JSON" },
    { k: "markdown", label: "Markdown (intermediate)" },
  ];
  return (
    <section className="lg:col-span-8 flex flex-col">
      <MetaStrip correlationId={correlationId} job={job} result={result} />

      {error && (
        <div
          data-testid="error-panel"
          className="mx-6 mt-4 border border-red-200 bg-red-50 text-err p-4 text-xs font-mono"
        >
          {String(error)}
        </div>
      )}

      <div className="px-6 pt-4 flex items-center gap-6 border-b border-line">
        {tabs.map((t) => (
          <button
            key={t.k}
            data-testid={`output-tab-${t.k}`}
            onClick={() => setOutputTab(t.k)}
            className={`pb-3 text-xs uppercase tracking-wider transition-colors duration-150 border-b-2 ${
              outputTab === t.k
                ? "border-ink text-ink"
                : "border-transparent text-muted hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 min-h-[420px] bg-white">
        <OutputBody result={result} loading={loading} outputTab={outputTab} mode={mode} />
      </div>
    </section>
  );
}
