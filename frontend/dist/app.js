/* ============================================================================
 *  Document Intelligence Console — vanilla JS
 *
 *  Single-origin: all backend calls use RELATIVE URLs (`/api/...`). Works in
 *  three deployment shapes:
 *    1. FastAPI serving both UI + API on one port (Kubernetes single-container).
 *    2. Static UI behind an ingress that proxies `/api/*` to the FastAPI pod.
 *    3. Local dev — static UI on :3000, FastAPI on :8001, an ingress in front.
 *
 *  No bundler, no transpiler — runs in any modern browser.
 *  XSS-safe: every dynamic value is set via textContent / DOM nodes.
 * ========================================================================== */
(() => {
  'use strict';

  // ---------- API base (same origin, relative) ----------
  const API = '/api/v1';
  const OPS = '/api';

  // ---------- App state ----------
  const state = {
    token: '',
    sourceTab: 'upload',    // 'upload' | 's3'
    file: null,
    s3Uri: 's3://demo-bucket/contracts/invoice-001.pdf',
    instructions: 'Extract document type, all header fields, line items and totals.',
    mode: 'sync',           // 'sync' | 'async'
    callbackUrl: '',
    outputTab: 'json',      // 'json' | 'markdown'
    result: null,
    job: null,
    correlationId: '',
    loading: false,
    error: null,
    view: 'console',        // 'console' | 'docs'
    pollTimer: null,
    // Docs view
    docs: [],
    activeDocId: null,
    docCache: {},
    docsError: null,
    docsLoading: false,
  };

  // ---------- DOM ----------
  const $ = (id) => document.getElementById(id);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const els = {
    // Header
    healthBadge: document.querySelector('[data-testid="health-badge"]'),
    readyBadge:  document.querySelector('[data-testid="ready-badge"]'),
    navConsole:  document.querySelector('[data-testid="nav-console"]'),
    navDocs:     document.querySelector('[data-testid="nav-docs"]'),
    viewConsole: $('view-console'),
    viewDocs:    $('view-docs'),
    // Input
    jwtInput:        $('jwt-input'),
    mintBtn:         $('mint-token-btn'),
    srcUploadTab:    document.querySelector('[data-testid="source-tab-upload"]'),
    srcS3Tab:        document.querySelector('[data-testid="source-tab-s3"]'),
    dropzone:        $('upload-dropzone'),
    uploadFilename:  $('upload-filename'),
    fileInput:       $('file-input'),
    s3Input:         $('s3-uri-input'),
    instructions:    $('instructions-input'),
    modeSyncTab:     document.querySelector('[data-testid="mode-sync"]'),
    modeAsyncTab:    document.querySelector('[data-testid="mode-async"]'),
    callbackInput:   $('callback-url-input'),
    extractBtn:      $('extract-btn'),
    extractIcon:     $('extract-icon'),
    extractLabel:    $('extract-label'),
    // Output
    correlationId:   $('correlation-id'),
    copyCidBtn:      $('copy-cid'),
    jobMeta:         $('job-meta'),
    jobIdEl:         $('job-id'),
    jobStatusBadge:  $('job-status-badge'),
    jobStatusLabel:  $('job-status-label'),
    resultMeta:      $('result-meta'),
    ocrBadge:        $('ocr-badge'),
    ocrBadgeLabel:   $('ocr-badge-label'),
    modelBadge:      $('model-badge'),
    modelBadgeLabel: $('model-badge-label'),
    chunksBadge:     $('chunks-badge'),
    chunksBadgeLabel: $('chunks-badge-label'),
    tokensBadge:     $('tokens-badge'),
    tokensBadgeLabel: $('tokens-badge-label'),
    procMs:          $('proc-ms'),
    errorPanel:      $('error-panel'),
    outputTabJson:   document.querySelector('[data-testid="output-tab-json"]'),
    outputTabMd:     document.querySelector('[data-testid="output-tab-markdown"]'),
    outputBody:      $('output-body'),
    // Docs
    docsNav:         $('docs-nav'),
    docsError:       $('docs-error'),
    docContent:      $('doc-content'),
  };

  /* ============================================================
   *  Tiny helpers
   * ============================================================ */
  const log = (...a) => console.log('[doc-intel]', ...a);

  function setBadge(el, tone, label) {
    if (!el) return;
    el.classList.remove('idle', 'ok', 'warn', 'err', 'info');
    el.classList.add(tone || 'idle');
    if (label !== undefined) {
      const span = el.querySelector('span:last-child');
      if (span) span.textContent = label;
    }
  }

  function setPulse(el, on) {
    if (!el) return;
    const dot = el.querySelector('.dot');
    if (dot) dot.classList.toggle('pulse', !!on);
  }

  function setError(msg) {
    state.error = msg;
    if (!msg) {
      els.errorPanel.hidden = true;
      els.errorPanel.textContent = '';
    } else {
      els.errorPanel.hidden = false;
      els.errorPanel.textContent = String(msg);
    }
  }

  function setLoading(loading) {
    state.loading = loading;
    els.extractBtn.disabled = loading;
    els.extractLabel.textContent = loading ? 'Processing' : 'Run Extraction';
    if (loading) {
      els.extractIcon.classList.add('spin');
      els.extractIcon.innerHTML = '<path d="M21 12a9 9 0 1 1-6.219-8.56" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"></path>';
    } else {
      els.extractIcon.classList.remove('spin');
      els.extractIcon.innerHTML = '<polyline points="9 18 15 12 9 6" stroke="currentColor" stroke-width="2" fill="none"></polyline>';
    }
  }

  async function readJson(res, label) {
    const contentType = res.headers.get('content-type') || '';
    const text = await res.text();
    if (!contentType.includes('json')) {
      throw new Error(
        `${label} failed: HTTP ${res.status} from ${res.url}. ` +
          `Expected JSON but received "${contentType || 'no content-type'}". ` +
          `First bytes: ${text.slice(0, 80)}`,
      );
    }
    const data = JSON.parse(text);
    if (!res.ok) {
      throw new Error(data.detail || data.title || `${label} failed: HTTP ${res.status}`);
    }
    return data;
  }

  /* ============================================================
   *  Health & token mint
   * ============================================================ */
  async function checkOps() {
    try {
      const r = await fetch(`${OPS}/health`);
      const d = await r.json();
      setBadge(els.healthBadge, r.ok && d.status === 'healthy' ? 'ok' : 'err', 'Health');
    } catch (e) {
      log('health failed:', e.message);
      setBadge(els.healthBadge, 'err', 'Health');
    }
    try {
      const r = await fetch(`${OPS}/ready`);
      const d = await r.json();
      setBadge(els.readyBadge, d.status === 'ready' ? 'ok' : 'err', 'Ready');
    } catch (e) {
      log('ready failed:', e.message);
      setBadge(els.readyBadge, 'err', 'Ready');
    }
  }

  async function mintToken() {
    try {
      const res = await fetch(`${API}/auth/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sub: 'demo-user', roles: ['extractor', 'admin'] }),
      });
      const d = await readJson(res, 'Token mint');
      state.token = d.accessToken;
      els.jwtInput.value = d.accessToken;
      log('token minted ok');
    } catch (e) {
      console.error('[doc-intel] mintToken:', e);
      setError(e.message);
    }
  }

  /* ============================================================
   *  Extraction
   * ============================================================ */
  function buildRequest(cid) {
    const headers = { Authorization: `Bearer ${state.token}`, 'X-Correlation-Id': cid };

    if (state.sourceTab === 'upload') {
      if (!state.file) return { error: 'Please choose a file to upload first.' };
      const fd = new FormData();
      fd.append('file', state.file);
      if (state.instructions) fd.append('instructions', state.instructions);
      if (state.mode === 'async' && state.callbackUrl) fd.append('callbackUrl', state.callbackUrl);
      return { headers, body: fd };
    }

    headers['Content-Type'] = 'application/json';
    const payload = { s3Uri: state.s3Uri, instructions: state.instructions };
    if (state.mode === 'async' && state.callbackUrl) payload.callbackUrl = state.callbackUrl;
    return { headers, body: JSON.stringify(payload) };
  }

  async function submit() {
    setError(null);
    state.result = null;
    state.job = null;
    renderJobMeta();
    renderResultMeta();
    renderOutput();

    if (!state.token) {
      setError("No auth token. Click 'Re-mint Demo Token' (and check the backend is reachable).");
      return;
    }

    const cid = `ui-${Date.now().toString(36)}`;
    state.correlationId = cid;
    els.correlationId.textContent = cid;
    els.copyCidBtn.hidden = false;

    const req = buildRequest(cid);
    if (req.error) { setError(req.error); return; }

    setLoading(true);
    const endpoint = state.mode === 'sync' ? 'extract/sync' : 'extract/async';
    log('submit ->', { endpoint, source: state.sourceTab, file: state.file?.name });

    try {
      const res = await fetch(`${API}/${endpoint}`, {
        method: 'POST', headers: req.headers, body: req.body,
      });
      const d = await readJson(res, 'Extraction');
      if (state.mode === 'sync') {
        state.result = d.result;
        setLoading(false);
        renderResultMeta();
        renderOutput();
      } else {
        state.job = { jobId: d.jobId, status: d.status, correlationId: d.correlationId };
        renderJobMeta();
        renderOutput();
        pollJob(d.jobId, cid);
      }
    } catch (e) {
      console.error('[doc-intel] submit:', e);
      setError(e.message);
      setLoading(false);
    }
  }

  function pollJob(jobId, cid) {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      try {
        const res = await fetch(`${API}/jobs/${jobId}`, {
          headers: { Authorization: `Bearer ${state.token}`, 'X-Correlation-Id': cid },
        });
        const d = await readJson(res, 'Job status');
        state.job = d;
        renderJobMeta();
        if (d.status === 'COMPLETED' || d.status === 'FAILED') {
          clearInterval(state.pollTimer);
          state.pollTimer = null;
          setLoading(false);
          if (d.result) state.result = d.result;
          if (d.error)  setError(d.error);
          renderResultMeta();
          renderOutput();
          log('job', jobId, '->', d.status);
        }
      } catch (e) {
        console.error('[doc-intel] poll:', e);
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        setLoading(false);
        setError(e.message);
      }
    }, 1500);
  }

  /* ============================================================
   *  Rendering
   * ============================================================ */
  function renderJobMeta() {
    if (!state.job) { els.jobMeta.hidden = true; return; }
    els.jobMeta.hidden = false;
    els.jobIdEl.textContent = (state.job.jobId || '').slice(0, 12) + '…';
    const tone = {
      PENDING: 'warn',
      PROCESSING: 'info',
      COMPLETED: 'ok',
      FAILED: 'err',
    }[state.job.status] || 'idle';
    setBadge(els.jobStatusBadge, tone, state.job.status);
    setPulse(els.jobStatusBadge, state.job.status === 'PENDING' || state.job.status === 'PROCESSING');
  }

  function renderResultMeta() {
    const r = state.result;
    if (!r) { els.resultMeta.hidden = true; return; }
    els.resultMeta.hidden = false;
    // OCR badge
    if (r.ocrUsed) {
      els.ocrBadge.classList.remove('hidden');
      els.ocrBadgeLabel.textContent = `OCR ${r.ocrEngine || ''}`.trim();
    } else {
      els.ocrBadge.classList.add('hidden');
    }
    // Model badge
    setBadge(els.modelBadge, r.mock ? 'warn' : 'info');
    els.modelBadgeLabel.textContent = r.mock ? 'Mock LLM' : (r.model || 'model');
    // Chunks badge (only when chunked)
    if (r.chunked && r.chunkCount > 1) {
      els.chunksBadge.classList.remove('hidden');
      els.chunksBadgeLabel.textContent = `CHUNKS ${r.chunkCount}`;
    } else {
      els.chunksBadge.classList.add('hidden');
    }
    // Tokens badge — always shown when we have an estimate
    if (typeof r.tokensEstimate === 'number') {
      els.tokensBadge.classList.remove('hidden');
      const used = r.tokensEstimate.toLocaleString();
      const saved = (r.tokensSavedVsRaw || 0).toLocaleString();
      els.tokensBadgeLabel.textContent = `TOK ${used} · SAVED ${saved}`;
    } else {
      els.tokensBadge.classList.add('hidden');
    }
    // Timing
    els.procMs.textContent = (typeof r.processingMs === 'number') ? `${r.processingMs}ms` : '';
  }

  function renderOutput() {
    const body = els.outputBody;
    body.innerHTML = '';
    if (!state.result && !state.loading) {
      body.appendChild(placeholder('Run an extraction to see structured output here.', 'file'));
      return;
    }
    if (state.loading && !state.result) {
      const msg = state.mode === 'async' ? 'Polling async job…' : 'Converting & extracting…';
      body.appendChild(placeholder(msg, 'spinner'));
      return;
    }
    if (state.outputTab === 'markdown') {
      const pre = jsonViewer(state.result.markdown);
      pre.dataset.testid = 'markdown-output';
      body.appendChild(pre);
    } else {
      const pre = jsonViewer(state.result.structured);
      pre.dataset.testid = 'json-output';
      body.appendChild(pre);
    }
  }

  function placeholder(msg, icon) {
    const div = document.createElement('div');
    div.className = 'placeholder';
    if (icon === 'spinner') {
      div.innerHTML = `
        <svg class="spin" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M21 12a9 9 0 1 1-6.219-8.56"></path>
        </svg>`;
    } else {
      div.innerHTML = `
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
        </svg>`;
    }
    const p = document.createElement('p');
    p.textContent = msg;
    div.appendChild(p);
    return div;
  }

  /* ------------------------------------------------------------
   *  XSS-safe JSON tokenizer (mirrors the original React viewer)
   * ------------------------------------------------------------ */
  const TOKEN_RE = /("(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\btrue\b|\bfalse\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  function tokenClass(token) {
    if (token[0] === '"') {
      return token.trimEnd().endsWith(':') ? 'k' : 's';
    }
    if (token === 'true' || token === 'false') return 'b';
    if (token === 'null') return 'nu';
    return 'n';
  }
  function jsonViewer(data) {
    const pre = document.createElement('pre');
    pre.className = 'json-viewer';
    if (data === null || data === undefined) {
      pre.textContent = 'No data yet.';
      return pre;
    }
    const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
    let last = 0; let m;
    TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(text)) !== null) {
      if (m.index > last) pre.appendChild(document.createTextNode(text.slice(last, m.index)));
      const span = document.createElement('span');
      span.className = tokenClass(m[0]);
      span.textContent = m[0];
      pre.appendChild(span);
      last = TOKEN_RE.lastIndex;
    }
    if (last < text.length) pre.appendChild(document.createTextNode(text.slice(last)));
    return pre;
  }

  /* ============================================================
   *  Markdown renderer for Docs view  (escaped DOM, no innerHTML)
   * ============================================================ */
  const INLINE_RE = /(\*\*[^*]+\*\*|`[^`]+`)/g;

  function appendInline(target, text) {
    let last = 0; let m;
    INLINE_RE.lastIndex = 0;
    while ((m = INLINE_RE.exec(text)) !== null) {
      if (m.index > last) target.appendChild(document.createTextNode(text.slice(last, m.index)));
      const tok = m[0];
      if (tok.startsWith('**')) {
        const s = document.createElement('strong');
        s.textContent = tok.slice(2, -2);
        target.appendChild(s);
      } else {
        const c = document.createElement('code');
        c.textContent = tok.slice(1, -1);
        target.appendChild(c);
      }
      last = INLINE_RE.lastIndex;
    }
    if (last < text.length) target.appendChild(document.createTextNode(text.slice(last)));
  }

  function renderMarkdown(source) {
    const root = document.createDocumentFragment();
    const lines = (source || '').split('\n');
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];

      // Fenced code block
      if (line.trim().startsWith('```')) {
        const buf = [];
        i += 1;
        while (i < lines.length && !lines[i].trim().startsWith('```')) { buf.push(lines[i]); i += 1; }
        i += 1;
        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.textContent = buf.join('\n');
        pre.appendChild(code);
        root.appendChild(pre);
        continue;
      }

      // Table
      if (line.trim().startsWith('|')) {
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(lines[i]); i += 1; }
        const cells = (r) => r.replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
        const table = document.createElement('table');
        const thead = document.createElement('thead');
        const trh = document.createElement('tr');
        cells(rows[0]).forEach((h) => {
          const th = document.createElement('th');
          appendInline(th, h);
          trh.appendChild(th);
        });
        thead.appendChild(trh);
        table.appendChild(thead);
        const tbody = document.createElement('tbody');
        rows.slice(1)
          .filter((r) => !/^\s*\|?[\s:|-]+\|?\s*$/.test(r))
          .forEach((r) => {
            const tr = document.createElement('tr');
            cells(r).forEach((c) => {
              const td = document.createElement('td');
              appendInline(td, c);
              tr.appendChild(td);
            });
            tbody.appendChild(tr);
          });
        table.appendChild(tbody);
        root.appendChild(table);
        continue;
      }

      // Headings
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) {
        const tag = document.createElement(`h${h[1].length}`);
        appendInline(tag, h[2]);
        root.appendChild(tag);
        i += 1; continue;
      }

      // Horizontal rule
      if (/^\s*---+\s*$/.test(line)) {
        root.appendChild(document.createElement('hr'));
        i += 1; continue;
      }

      // Lists
      if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
        const ordered = /^\s*\d+\.\s+/.test(line);
        const list = document.createElement(ordered ? 'ol' : 'ul');
        while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
          const li = document.createElement('li');
          appendInline(li, lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ''));
          list.appendChild(li);
          i += 1;
        }
        root.appendChild(list);
        continue;
      }

      // Blank
      if (!line.trim()) { i += 1; continue; }

      // Paragraph (merge continuation lines)
      const buf = [line.trim()];
      i += 1;
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^(#{1,4}\s|```|\||\s*([-*]|\d+\.)\s|---+\s*$)/.test(lines[i].trim())
      ) {
        buf.push(lines[i].trim());
        i += 1;
      }
      const p = document.createElement('p');
      appendInline(p, buf.join(' '));
      root.appendChild(p);
    }
    return root;
  }

  /* ============================================================
   *  Docs view
   * ============================================================ */
  async function loadDocsList() {
    try {
      const res = await fetch(`${API}/documents`);
      const list = await readJson(res, 'Load documents');
      state.docs = list;
      renderDocsNav();
      if (list.length) selectDoc(list[0].id);
    } catch (e) {
      showDocsError(e.message);
    }
  }

  function renderDocsNav() {
    els.docsNav.innerHTML = '';
    state.docs.forEach((d) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.dataset.testid = `doc-nav-${d.id}`;
      btn.className = state.activeDocId === d.id ? 'active' : '';
      btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>`;
      const span = document.createElement('span');
      span.textContent = d.title;
      btn.appendChild(span);
      btn.addEventListener('click', () => selectDoc(d.id));
      els.docsNav.appendChild(btn);
    });
  }

  async function selectDoc(id) {
    state.activeDocId = id;
    renderDocsNav();
    if (state.docCache[id]) { renderDoc(state.docCache[id]); return; }
    try {
      showDocsError(null);
      els.docContent.textContent = 'Loading document…';
      const res = await fetch(`${API}/documents/${id}`);
      const d = await readJson(res, 'Load document');
      state.docCache[id] = d;
      if (state.activeDocId === id) renderDoc(d);
    } catch (e) {
      showDocsError(e.message);
    }
  }

  function renderDoc(d) {
    els.docContent.innerHTML = '';
    els.docContent.appendChild(renderMarkdown(d.content));
  }

  function showDocsError(msg) {
    state.docsError = msg;
    if (!msg) { els.docsError.hidden = true; els.docsError.textContent = ''; return; }
    els.docsError.hidden = false;
    els.docsError.textContent = msg;
  }

  /* ============================================================
   *  Wiring (event listeners)
   * ============================================================ */
  function setActiveSegTab(tabs, attr, value) {
    tabs.forEach((t) => t.classList.toggle('active', t.dataset[attr] === value));
  }

  function wire() {
    // Header nav
    [els.navConsole, els.navDocs].forEach((btn) => {
      btn.addEventListener('click', () => {
        const v = btn.dataset.view;
        state.view = v;
        els.navConsole.classList.toggle('active', v === 'console');
        els.navDocs.classList.toggle('active', v === 'docs');
        els.navConsole.setAttribute('aria-selected', String(v === 'console'));
        els.navDocs.setAttribute('aria-selected', String(v === 'docs'));
        els.viewConsole.hidden = v !== 'console';
        els.viewDocs.hidden = v !== 'docs';
        if (v === 'docs' && state.docs.length === 0) loadDocsList();
      });
    });

    // Token field
    els.jwtInput.addEventListener('input', (e) => { state.token = e.target.value; });
    els.mintBtn.addEventListener('click', mintToken);

    // Source tabs
    const srcTabs = [els.srcUploadTab, els.srcS3Tab];
    srcTabs.forEach((b) => {
      b.addEventListener('click', () => {
        state.sourceTab = b.dataset.source;
        setActiveSegTab(srcTabs, 'source', state.sourceTab);
        const isUpload = state.sourceTab === 'upload';
        els.dropzone.classList.toggle('hidden', !isUpload);
        els.s3Input.classList.toggle('hidden', isUpload);
      });
    });

    // File input
    els.fileInput.addEventListener('change', (e) => {
      const f = e.target.files?.[0] || null;
      state.file = f;
      els.uploadFilename.textContent = f
        ? f.name
        : 'Drop PDF / image (JPG, PNG, HEIC, AVIF) / DOCX / scan or click';
    });

    // Drag and drop
    ['dragenter', 'dragover'].forEach((ev) =>
      els.dropzone.addEventListener(ev, (e) => {
        e.preventDefault(); e.stopPropagation();
        els.dropzone.classList.add('dragover');
      }),
    );
    ['dragleave', 'drop'].forEach((ev) =>
      els.dropzone.addEventListener(ev, (e) => {
        e.preventDefault(); e.stopPropagation();
        els.dropzone.classList.remove('dragover');
      }),
    );
    els.dropzone.addEventListener('drop', (e) => {
      const f = e.dataTransfer?.files?.[0] || null;
      if (f) {
        state.file = f;
        els.uploadFilename.textContent = f.name;
        // Reflect in <input> too so re-selection works
        try {
          const dt = new DataTransfer();
          dt.items.add(f);
          els.fileInput.files = dt.files;
        } catch (_) { /* not supported in some browsers; safe to ignore */ }
      }
    });

    // S3 / instructions / callback
    els.s3Input.addEventListener('input', (e) => { state.s3Uri = e.target.value; });
    els.instructions.addEventListener('input', (e) => { state.instructions = e.target.value; });
    els.callbackInput.addEventListener('input', (e) => { state.callbackUrl = e.target.value; });

    // Mode tabs
    const modeTabs = [els.modeSyncTab, els.modeAsyncTab];
    modeTabs.forEach((b) => {
      b.addEventListener('click', () => {
        state.mode = b.dataset.mode;
        setActiveSegTab(modeTabs, 'mode', state.mode);
        els.callbackInput.classList.toggle('hidden', state.mode !== 'async');
      });
    });

    // Output tabs
    const outTabs = [els.outputTabJson, els.outputTabMd];
    outTabs.forEach((b) => {
      b.addEventListener('click', () => {
        state.outputTab = b.dataset.output;
        setActiveSegTab(outTabs, 'output', state.outputTab);
        renderOutput();
      });
    });

    // Submit
    els.extractBtn.addEventListener('click', submit);

    // Copy correlation id
    els.copyCidBtn.addEventListener('click', () => {
      if (state.correlationId && navigator.clipboard) {
        navigator.clipboard.writeText(state.correlationId);
      }
    });
  }

  /* ============================================================
   *  Boot
   * ============================================================ */
  function boot() {
    wire();
    renderOutput();
    checkOps();
    mintToken();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
