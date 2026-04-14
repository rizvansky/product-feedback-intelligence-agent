import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        canvas: "#f4efe3",
        ink: "#1c2732",
        mist: "#dfe7df",
        lagoon: "#0f7c82",
        ember: "#b54d2a",
        pine: "#183b32",
        sand: "#efe4cf"
      },
      fontFamily: {
        display: ["Iowan Old Style", "Palatino Linotype", "Book Antiqua", "serif"],
        body: ["Avenir Next", "Segoe UI", "sans-serif"],
        mono: ["IBM Plex Mono", "SFMono-Regular", "monospace"]
      },
      boxShadow: {
        halo: "0 28px 70px rgba(15, 124, 130, 0.18)"
      }
    }
  },
  plugins: []
};

export default config;
