<script lang="ts">
  // AT URI deep-link catch-all: /at/{authority}/{collection}/{rkey} → render App.
  // App reads location.pathname to parse the AT URI and auto-loads the doc.
  import App from '../../../App.svelte';
</script>

<App />
