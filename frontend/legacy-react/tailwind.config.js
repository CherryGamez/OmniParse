/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}", "./public/index.html"],
  theme: {
    extend: {
      colors: {
        ink: "#111111",
        electric: "#0000FF",
        bg: "#F9FAFB",
        line: "#E5E7EB",
        muted: "#6B7280",
        ok: "#16A34A",
        warn: "#EAB308",
        err: "#DC2626",
        jsonKey: "#2563EB",
        jsonStr: "#16A34A",
        jsonNum: "#D97706",
      },
      fontFamily: {
        head: ["Chivo", "sans-serif"],
        body: ["'IBM Plex Sans'", "sans-serif"],
        mono: ["'IBM Plex Mono'", "monospace"],
      },
    },
  },
  plugins: [],
};
