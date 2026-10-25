import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./", // Electron loads dist/index.html via file:// — assets must be relative
  server: {
    port: 5174,
    strictPort: true,
  },
  build: {
    outDir: "dist",
  },
});
