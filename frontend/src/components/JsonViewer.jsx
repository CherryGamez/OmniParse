import React from "react";

// Tokenize a JSON string into safe React nodes (keys=blue, strings=green,
// numbers=orange, booleans=electric, null=muted). Rendering tokens as React
// children means the browser escapes all text — NO dangerouslySetInnerHTML,
// so there is no XSS surface even for attacker-controlled content.
const TOKEN_RE =
  /("(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\btrue\b|\bfalse\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;

function classFor(token) {
  if (token[0] === '"') {
    return token.trimEnd().endsWith(":") ? "text-jsonKey font-medium" : "text-jsonStr";
  }
  if (token === "true" || token === "false") return "text-electric";
  if (token === "null") return "text-muted";
  return "text-jsonNum";
}

function tokenize(text) {
  const nodes = [];
  let lastIndex = 0;
  let key = 0;
  let match;
  TOKEN_RE.lastIndex = 0;
  while ((match = TOKEN_RE.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    nodes.push(
      <span key={key++} className={classFor(match[0])}>
        {match[0]}
      </span>
    );
    lastIndex = TOKEN_RE.lastIndex;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

export default function JsonViewer({ data, testId }) {
  if (data === null || data === undefined) {
    return (
      <div className="p-6 text-sm text-muted font-mono" data-testid={testId}>
        No data yet.
      </div>
    );
  }
  const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return (
    <pre
      data-testid={testId}
      className="p-6 text-xs leading-relaxed font-mono whitespace-pre-wrap break-words overflow-auto"
    >
      {tokenize(text)}
    </pre>
  );
}
