import React, { useState } from "react";
import useDocIntel from "./hooks/useDocIntel";
import Header from "./components/Header";
import InputPanel from "./components/InputPanel";
import OutputPanel from "./components/OutputPanel";
import DocsPanel from "./components/DocsPanel";

// Thin composition root: all logic lives in the useDocIntel hook, all markup
// in the focused Header / InputPanel / OutputPanel / DocsPanel components.
export default function App() {
  const state = useDocIntel();
  const [view, setView] = useState("console"); // console | docs
  return (
    <div className="min-h-screen bg-bg font-body text-ink">
      <div className="w-full max-w-[1600px] mx-auto min-h-screen border-l border-r border-line bg-white">
        <Header health={state.health} ready={state.ready} view={view} setView={setView} />
        {view === "console" ? (
          <div className="grid grid-cols-1 lg:grid-cols-12">
            <InputPanel {...state} />
            <OutputPanel {...state} />
          </div>
        ) : (
          <DocsPanel />
        )}
      </div>
    </div>
  );
}
