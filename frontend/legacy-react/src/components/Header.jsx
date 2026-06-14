import React from "react";
import { BookOpen, Terminal } from "lucide-react";
import Badge from "./Badge";

// Top app bar with title, Console/Docs navigation and ops health/ready badges.
export default function Header({ health, ready, view, setView }) {
  const navItems = [
    { k: "console", label: "Console", icon: Terminal },
    { k: "docs", label: "Docs", icon: BookOpen },
  ];
  return (
    <header className="h-16 px-6 flex items-center justify-between border-b border-line sticky top-0 bg-white z-20">
      <div className="flex items-center gap-3">
        <div className="h-8 w-8 bg-ink flex items-center justify-center">
          <Terminal size={18} className="text-white" />
        </div>
        <div>
          <h1 className="font-head text-base font-bold tracking-tight leading-none">
            DOCUMENT INTELLIGENCE
          </h1>
          <span className="uppercase-label text-muted">Extraction Console / v1</span>
        </div>
      </div>
      <nav className="flex border border-line" data-testid="main-nav">
        {navItems.map(({ k, label, icon: Icon }) => (
          <button
            key={k}
            data-testid={`nav-${k}`}
            onClick={() => setView(k)}
            className={`flex items-center gap-2 px-4 py-2 text-xs uppercase tracking-wider transition-colors duration-150 ${
              view === k ? "bg-ink text-white" : "bg-white hover:bg-gray-100"
            }`}
          >
            <Icon size={13} /> {label}
          </button>
        ))}
      </nav>
      <div className="flex items-center gap-2" data-testid="ops-badges">
        <Badge label="Health" tone={health} testId="health-badge" />
        <Badge label="Ready" tone={ready} testId="ready-badge" />
      </div>
    </header>
  );
}
