import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    proxy: {
      "/api":
        loadEnv(mode, ".", "PORTAL_").PORTAL_API_URL || "http://127.0.0.1:8000",
    },
  },
  test: { include: ["src/**/*.test.ts"] },
}));
