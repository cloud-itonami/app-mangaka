import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    // `vite dev` for local UI iteration. Backend calls are proxied to a
    // locally-running `langgraph dev` (see ../../../lg/scripts/dev.sh).
    proxy: {
      "/api": {
        target: "http://127.0.0.1:2024",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
