import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In Docker, Caddy serves this dev server on http://localhost and routes
// /api, /admin and /static to Django, so no proxy is configured here.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
  },
});
