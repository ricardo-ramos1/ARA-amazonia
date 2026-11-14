import { defineConfig } from 'vite';

// Relative base: the same build works at a domain root (https://example.org/) or in a sub-path
// (https://user.github.io/repo/). Data is fetched as data/... relative to the page.
export default defineConfig({
  base: './',
  build: {
    chunkSizeWarningLimit: 2500, // deck.gl + MapLibre are one ~2 MB bundle (≈570 kB gzip); expected
  },
});
