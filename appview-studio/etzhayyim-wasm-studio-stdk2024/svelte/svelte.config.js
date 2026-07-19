import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/kit').Config} */
export default {
  preprocess: vitePreprocess(),
  kit: {
    // CSR-only SPA. Worker serves the built bundle via ASSETS binding;
    // not_found_handling=single-page-application falls back to index.html.
    adapter: adapter({ fallback: "index.html" }),
    prerender: { entries: [] },
  },
};
