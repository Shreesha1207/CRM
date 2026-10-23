import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API is proxied, so the browser sees one origin and the
// auth cookie + CSRF cookie work without any cross-site configuration.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Tunnels (for testing the AI connectors from claude.ai or ChatGPT) arrive under their own
    // host names; these resolve to the tunnel providers, never to this machine. Add more with
    // VITE_ALLOWED_HOSTS=a.example.com,b.example.com.
    allowedHosts: [".trycloudflare.com", ".ngrok-free.app", ...(process.env.VITE_ALLOWED_HOSTS?.split(",").filter(Boolean) ?? [])],
    proxy: {
      "/api": { target: process.env.VITE_API_PROXY ?? "http://localhost:8000", changeOrigin: false },
      // Where connected AI assistants discover the OAuth server (see the README).
      "/.well-known/oauth-": { target: process.env.VITE_API_PROXY ?? "http://localhost:8000", changeOrigin: false },
    },
  },
});
