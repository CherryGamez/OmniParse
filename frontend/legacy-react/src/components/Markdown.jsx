import React from "react";

// Minimal, dependency-free Markdown renderer (headings, lists, tables, code
// blocks, bold, inline code, hr). Renders escaped React nodes — XSS-safe.

function inline(text, keyBase) {
  const parts = [];
  const regex = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m;
  let i = 0;
  while ((m = regex.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const token = m[0];
    if (token.startsWith("**")) {
      parts.push(
        <strong key={`${keyBase}-b${i}`} className="font-semibold">
          {token.slice(2, -2)}
        </strong>
      );
    } else {
      parts.push(
        <code
          key={`${keyBase}-c${i}`}
          className="font-mono text-[0.85em] bg-gray-100 border border-line px-1 py-0.5"
        >
          {token.slice(1, -1)}
        </code>
      );
    }
    last = m.index + token.length;
    i += 1;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function Table({ rows, keyBase }) {
  const cells = (line) =>
    line
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((c) => c.trim());
  const header = cells(rows[0]);
  const body = rows.slice(1).filter((r) => !/^\s*\|?[\s:|-]+\|?\s*$/.test(r));
  return (
    <div className="overflow-x-auto my-4">
      <table className="w-full text-xs border border-line">
        <thead>
          <tr className="bg-gray-50">
            {header.map((h, i) => (
              <th key={i} className="border border-line px-3 py-2 text-left font-semibold">
                {inline(h, `${keyBase}-h${i}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, ri) => (
            <tr key={ri}>
              {cells(row).map((c, ci) => (
                <td key={ci} className="border border-line px-3 py-2 align-top">
                  {inline(c, `${keyBase}-r${ri}c${ci}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Markdown({ source }) {
  const lines = (source || "").split("\n");
  const out = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    if (line.trim().startsWith("```")) {
      const buf = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1; // closing fence
      out.push(
        <pre
          key={key++}
          className="bg-gray-50 border border-line p-4 my-4 overflow-x-auto text-[11px] font-mono leading-relaxed"
        >
          {buf.join("\n")}
        </pre>
      );
      continue;
    }

    // Table block
    if (line.trim().startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(lines[i]);
        i += 1;
      }
      out.push(<Table key={key++} rows={rows} keyBase={`t${key}`} />);
      continue;
    }

    // Headings
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const level = h[1].length;
      const cls = {
        1: "font-head text-2xl font-bold mt-2 mb-4 tracking-tight",
        2: "font-head text-lg font-bold mt-8 mb-3 pb-2 border-b border-line",
        3: "font-head text-base font-semibold mt-6 mb-2",
        4: "font-head text-sm font-semibold mt-4 mb-2 uppercase tracking-wider",
      }[level];
      const Tag = `h${level}`;
      out.push(
        <Tag key={key++} className={cls}>
          {inline(h[2], `h${key}`)}
        </Tag>
      );
      i += 1;
      continue;
    }

    // Horizontal rule
    if (/^\s*---+\s*$/.test(line)) {
      out.push(<hr key={key++} className="border-line my-6" />);
      i += 1;
      continue;
    }

    // List block (unordered + ordered)
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
        i += 1;
      }
      out.push(
        <ul key={key++} className="my-3 space-y-1.5 pl-5 list-disc text-sm leading-relaxed">
          {items.map((it, li) => (
            <li key={li}>{inline(it, `l${key}-${li}`)}</li>
          ))}
        </ul>
      );
      continue;
    }

    // Blank line
    if (!line.trim()) {
      i += 1;
      continue;
    }

    // Paragraph — merge consecutive plain lines so inline formatting (e.g.
    // **bold** spanning a wrapped line) renders correctly.
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
    out.push(
      <p key={key++} className="text-sm leading-relaxed my-2">
        {inline(buf.join(" "), `p${key}`)}
      </p>
    );
  }

  return <div>{out}</div>;
}
