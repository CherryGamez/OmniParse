import React, { useCallback, useEffect, useState } from "react";
import { FileText, Loader2 } from "lucide-react";
import Markdown from "./Markdown";

const API = `${process.env.REACT_APP_BACKEND_URL}/api/v1`;

// Docs view: sidebar of project documents (PRD / TRD / App Flow) served by the
// backend from the repository's documents/ folder, rendered as Markdown.
export default function DocsPanel() {
  const [docs, setDocs] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [cache, setCache] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API}/documents`);
        const list = await res.json();
        if (!res.ok) throw new Error(list.detail || "Failed to load documents");
        setDocs(list);
        if (list.length) setActiveId(list[0].id);
      } catch (e) {
        setError(e.message);
      }
    })();
  }, []);

  const loadDoc = useCallback(
    async (id) => {
      if (cache[id]) return;
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API}/documents/${id}`);
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || "Failed to load document");
        setCache((c) => ({ ...c, [id]: d }));
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [cache]
  );

  useEffect(() => {
    if (activeId) loadDoc(activeId);
  }, [activeId, loadDoc]);

  const active = activeId ? cache[activeId] : null;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 min-h-[calc(100vh-4rem)]" data-testid="docs-panel">
      {/* Sidebar */}
      <aside className="lg:col-span-3 border-r border-line p-6">
        <span className="uppercase-label block mb-4 text-muted">Project Documents</span>
        <div className="space-y-1">
          {docs.map((d) => (
            <button
              key={d.id}
              data-testid={`doc-nav-${d.id}`}
              onClick={() => setActiveId(d.id)}
              className={`w-full flex items-center gap-2 px-3 py-2.5 text-left text-xs uppercase tracking-wider transition-colors duration-150 border ${
                activeId === d.id
                  ? "bg-ink text-white border-ink"
                  : "bg-white border-line hover:bg-gray-100"
              }`}
            >
              <FileText size={13} className="shrink-0" />
              <span>{d.title}</span>
            </button>
          ))}
        </div>
        <p className="mt-6 text-[11px] font-mono text-muted leading-relaxed">
          Served from the repository's <code>documents/</code> folder via{" "}
          <code>GET /api/v1/documents</code> — docs always match the shipped code.
        </p>
      </aside>

      {/* Reading pane */}
      <main className="lg:col-span-9 p-8 lg:p-12 overflow-y-auto">
        {error && (
          <div
            data-testid="docs-error"
            className="border border-red-200 bg-red-50 text-err p-4 text-xs font-mono mb-6"
          >
            {String(error)}
          </div>
        )}
        {loading && !active && (
          <div className="flex items-center gap-3 text-muted py-24 justify-center">
            <Loader2 size={20} className="animate-spin" />
            <span className="text-sm font-mono">Loading document…</span>
          </div>
        )}
        {active && (
          <article data-testid="doc-content" className="max-w-4xl">
            <Markdown source={active.content} />
          </article>
        )}
      </main>
    </div>
  );
}
