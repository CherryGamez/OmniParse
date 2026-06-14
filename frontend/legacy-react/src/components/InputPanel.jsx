import React from "react";
import {
  Activity,
  ChevronRight,
  KeyRound,
  Loader2,
  UploadCloud,
  Zap,
} from "lucide-react";

const inputCls =
  "w-full bg-gray-50/50 border border-line p-3 text-xs font-mono rounded-none focus:outline-none focus:ring-1 focus:ring-ink focus:border-ink";

function Segmented({ value, onChange, options, idPrefix }) {
  return (
    <div className="flex border border-line">
      {options.map(({ k, label, icon: Icon }) => (
        <button
          key={k}
          data-testid={`${idPrefix}-${k}`}
          onClick={() => onChange(k)}
          className={`flex-1 flex items-center justify-center gap-2 py-2 text-xs uppercase tracking-wider transition-colors duration-150 ${
            value === k ? "bg-ink text-white" : "bg-white hover:bg-gray-100"
          }`}
        >
          {Icon ? <Icon size={13} /> : null} {label}
        </button>
      ))}
    </div>
  );
}

// Left column: auth token, document source, instructions, mode, submit.
export default function InputPanel({
  token,
  setToken,
  mintToken,
  sourceTab,
  setSourceTab,
  file,
  setFile,
  s3Uri,
  setS3Uri,
  instructions,
  setInstructions,
  mode,
  setMode,
  callbackUrl,
  setCallbackUrl,
  loading,
  submit,
}) {
  return (
    <section className="lg:col-span-4 border-r border-line">
      {/* Auth */}
      <div className="p-6 border-b border-line">
        <div className="flex items-center gap-2 mb-3">
          <KeyRound size={16} />
          <span className="uppercase-label">Mock OIDC Token</span>
        </div>
        <textarea
          data-testid="jwt-input"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          rows={3}
          className={`${inputCls} break-all`}
        />
        <button
          data-testid="mint-token-btn"
          onClick={mintToken}
          className="mt-2 w-full border border-line py-2 text-xs uppercase tracking-wider hover:bg-gray-100 transition-colors duration-150"
        >
          Re-mint Demo Token
        </button>
      </div>

      {/* Source */}
      <div className="p-6 border-b border-line">
        <span className="uppercase-label block mb-3">Document Source</span>
        <div className="mb-4">
          <Segmented
            value={sourceTab}
            onChange={setSourceTab}
            idPrefix="source-tab"
            options={[
              { k: "upload", label: "File Upload" },
              { k: "s3", label: "S3 URI" },
            ]}
          />
        </div>

        {sourceTab === "upload" ? (
          <label
            data-testid="upload-dropzone"
            className="flex flex-col items-center justify-center gap-2 border border-dashed border-gray-300 bg-gray-50/50 py-8 cursor-pointer hover:bg-gray-100 transition-colors duration-150"
          >
            <UploadCloud size={22} className="text-muted" />
            <span className="text-xs text-muted font-mono">
              {file ? file.name : "Drop PDF / image (JPG, PNG, HEIC, AVIF) / DOCX / scan or click"}
            </span>
            <input
              data-testid="file-input"
              type="file"
              accept=".pdf,.docx,.pptx,.xls,.xlsx,.msg,.txt,.md,.html,.csv,.json,.jpg,.jpeg,.png,.tif,.tiff,.bmp,.webp,.gif,.heic,.heif,.avif"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </label>
        ) : (
          <input
            data-testid="s3-uri-input"
            value={s3Uri}
            onChange={(e) => setS3Uri(e.target.value)}
            placeholder="s3://bucket/key.pdf"
            className={inputCls}
          />
        )}
      </div>

      {/* Instructions */}
      <div className="p-6 border-b border-line">
        <span className="uppercase-label block mb-3">Extraction Instructions</span>
        <textarea
          data-testid="instructions-input"
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          rows={3}
          className={inputCls}
        />
      </div>

      {/* Mode */}
      <div className="p-6 border-b border-line">
        <span className="uppercase-label block mb-3">Extraction Mode</span>
        <Segmented
          value={mode}
          onChange={setMode}
          idPrefix="mode"
          options={[
            { k: "sync", label: "Sync", icon: Zap },
            { k: "async", label: "Async (202)", icon: Activity },
          ]}
        />
        {mode === "async" && (
          <input
            data-testid="callback-url-input"
            value={callbackUrl}
            onChange={(e) => setCallbackUrl(e.target.value)}
            placeholder="callbackUrl (optional, e.g. Camunda)"
            className={`mt-3 ${inputCls}`}
          />
        )}
      </div>

      {/* Submit */}
      <div className="p-6">
        <button
          data-testid="extract-btn"
          onClick={submit}
          disabled={loading}
          className="w-full bg-ink text-white py-3 text-xs uppercase tracking-[0.2em] flex items-center justify-center gap-2 hover:bg-ink/90 transition-colors duration-150 disabled:opacity-50"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <ChevronRight size={15} />}
          {loading ? "Processing" : "Run Extraction"}
        </button>
      </div>
    </section>
  );
}
