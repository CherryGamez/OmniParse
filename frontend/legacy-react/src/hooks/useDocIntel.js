import { useCallback, useEffect, useRef, useState } from "react";

const BACKEND = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND}/api/v1`;

// Flip to false to silence console diagnostics.
const DEBUG = true;
const log = (...a) => DEBUG && console.log("[doc-intel]", ...a);

// Robustly read a JSON response. If the server returns HTML (wrong URL / backend
// down) or a non-2xx problem+json, throw a clear, actionable error instead of a
// cryptic "Unexpected token '<'" JSON parse failure.
async function readJson(res, label) {
  const contentType = res.headers.get("content-type") || "";
  const text = await res.text();
  if (!contentType.includes("json")) {
    throw new Error(
      `${label} failed: HTTP ${res.status} from ${res.url}. ` +
        `Expected JSON but received "${contentType || "no content-type"}". ` +
        `This usually means REACT_APP_BACKEND_URL is wrong or the backend isn't running. ` +
        `First bytes: ${text.slice(0, 60)}`
    );
  }
  const data = JSON.parse(text);
  if (!res.ok) {
    throw new Error(data.detail || data.title || `${label} failed: HTTP ${res.status}`);
  }
  return data;
}

// All extraction-console state + side effects live here so the UI components
// stay small and presentational. Each callback declares its real dependencies.
export default function useDocIntel() {
  const [token, setToken] = useState("");
  const [sourceTab, setSourceTab] = useState("upload"); // upload | s3
  const [file, setFile] = useState(null);
  const [s3Uri, setS3Uri] = useState("s3://demo-bucket/contracts/invoice-001.pdf");
  const [instructions, setInstructions] = useState(
    "Extract document type, all header fields, line items and totals."
  );
  const [mode, setMode] = useState("sync"); // sync | async
  const [callbackUrl, setCallbackUrl] = useState("");
  const [outputTab, setOutputTab] = useState("json"); // json | markdown
  const [result, setResult] = useState(null);
  const [job, setJob] = useState(null);
  const [correlationId, setCorrelationId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [health, setHealth] = useState("idle");
  const [ready, setReady] = useState("idle");
  const pollRef = useRef(null);

  const checkOps = useCallback(async () => {
    try {
      const h = await fetch(`${BACKEND}/api/health`);
      const d = await h.json(); // throws if the response is HTML (wrong URL / SPA fallback)
      setHealth(h.ok && d.status === "healthy" ? "ok" : "err");
    } catch (e) {
      log("health check failed (is REACT_APP_BACKEND_URL pointing at the backend?):", e.message);
      setHealth("err");
    }
    try {
      const r = await fetch(`${BACKEND}/api/ready`);
      const d = await r.json();
      setReady(d.status === "ready" ? "ok" : "err");
    } catch (e) {
      log("ready check failed:", e.message);
      setReady("err");
    }
  }, []);

  const mintToken = useCallback(async () => {
    try {
      log("minting token at", `${API}/auth/token`);
      const res = await fetch(`${API}/auth/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sub: "demo-user", roles: ["extractor", "admin"] }),
      });
      const d = await readJson(res, "Token mint");
      setToken(d.accessToken);
      log("token minted ok");
    } catch (e) {
      console.error("[doc-intel] mintToken:", e);
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    log("REACT_APP_BACKEND_URL =", BACKEND || "(UNDEFINED!)");
    if (!BACKEND) {
      setError(
        "REACT_APP_BACKEND_URL is not set. Create frontend/.env with " +
          "REACT_APP_BACKEND_URL=http://localhost:8001 and restart `yarn start`."
      );
      setHealth("err");
      setReady("err");
      return;
    }
    checkOps();
    mintToken();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [checkOps, mintToken]);

  const pollJob = useCallback(
    (jobId, headerCid) => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const res = await fetch(`${API}/jobs/${jobId}`, {
            headers: { Authorization: `Bearer ${token}`, "X-Correlation-Id": headerCid },
          });
          const d = await readJson(res, "Job status");
          setJob(d);
          if (d.status === "COMPLETED" || d.status === "FAILED") {
            clearInterval(pollRef.current);
            setLoading(false);
            if (d.result) setResult(d.result);
            if (d.error) setError(d.error);
            log("job", jobId, "->", d.status);
          }
        } catch (e) {
          console.error("[doc-intel] poll:", e);
          clearInterval(pollRef.current);
          setLoading(false);
          setError(e.message);
        }
      }, 1500);
    },
    [token]
  );

  // Build the fetch headers + body for the current source/mode selection.
  const buildRequest = useCallback(
    (cid) => {
      const headers = { Authorization: `Bearer ${token}`, "X-Correlation-Id": cid };
      if (sourceTab === "upload") {
        if (!file) return { error: "Please choose a file to upload first." };
        const fd = new FormData();
        fd.append("file", file);
        if (instructions) fd.append("instructions", instructions);
        if (mode === "async" && callbackUrl) fd.append("callbackUrl", callbackUrl);
        return { headers, body: fd };
      }
      headers["Content-Type"] = "application/json";
      const payload = { s3Uri, instructions };
      if (mode === "async" && callbackUrl) payload.callbackUrl = callbackUrl;
      return { headers, body: JSON.stringify(payload) };
    },
    [token, sourceTab, file, instructions, mode, callbackUrl, s3Uri]
  );

  const submit = useCallback(async () => {
    setError(null);
    setResult(null);
    setJob(null);

    if (!token) {
      setError("No auth token. Click 'Re-mint Demo Token' (and check the backend is reachable).");
      return;
    }

    const req = buildRequest(token && `ui-${Date.now().toString(36)}`);
    if (req.error) {
      setError(req.error);
      return;
    }

    const cid = `ui-${Date.now().toString(36)}`;
    setCorrelationId(cid);
    req.headers["X-Correlation-Id"] = cid;
    setLoading(true);

    const endpoint = mode === "sync" ? "extract/sync" : "extract/async";
    const url = `${API}/${endpoint}`;
    log("submit ->", { url, mode, source: sourceTab, file: file?.name, correlationId: cid });

    try {
      const res = await fetch(url, { method: "POST", headers: req.headers, body: req.body });
      log("response", res.status, res.headers.get("content-type"));
      const d = await readJson(res, "Extraction");
      if (mode === "sync") {
        setResult(d.result);
        setLoading(false);
        log("sync extraction ok");
      } else {
        setJob({ jobId: d.jobId, status: d.status, correlationId: d.correlationId });
        log("async job accepted:", d.jobId);
        pollJob(d.jobId, cid);
      }
    } catch (e) {
      console.error("[doc-intel] submit:", e);
      setError(e.message);
      setLoading(false);
    }
  }, [token, buildRequest, mode, sourceTab, file, pollJob]);

  return {
    token,
    setToken,
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
    outputTab,
    setOutputTab,
    result,
    job,
    correlationId,
    loading,
    error,
    health,
    ready,
    mintToken,
    submit,
  };
}
