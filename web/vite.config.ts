import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development, the UI calls the API through Vite's proxy, so there's no CORS setup:
// http://localhost:5173/v1/... -> http://127.0.0.1:8000/v1/...
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
