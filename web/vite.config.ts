/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.STUDIO_API_URL ?? "http://127.0.0.1:8700";

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.PORT ?? 5195),
    proxy: { "/api": { target: api, changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
