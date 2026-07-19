// CSR-only — adapter-static emits the index.html fallback; runtime is SPA.
// prerender=false because the SPA's onMount/fetch logic isn't crawlable.
export const prerender = false;
export const ssr = false;
export const trailingSlash = "always";
