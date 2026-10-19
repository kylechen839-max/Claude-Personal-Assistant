KaTeX 0.16.22 (MIT, see LICENSE), vendored so the phone app needs no CDN.
Files: dist/katex.min.js, dist/katex.min.css and the .woff2 fonts from the
npm package; the .woff/.ttf fallbacks were removed from the CSS (every
current browser loads .woff2). To update: `npm pack katex@<version>`, copy
the same files, and re-run the sed in the commit that added this folder.
