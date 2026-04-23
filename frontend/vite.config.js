// frontend/vite.config.js

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],

  // Ensures PNG, JPG, SVG, and other assets build correctly on Render
  assetsInclude: ["**/*.png", "**/*.jpg", "**/*.jpeg", "**/*.svg"],

  server: {
    port: 5173,
  },

  build: {
    outDir: "dist",
    sourcemap: false,
  },
});