MathJax 3.2.2, `es5/tex-svg-full.js`, distributed under the Apache License 2.0
(see LICENSE in this directory).

Upstream: https://github.com/mathjax/MathJax/tree/3.2.2
Downloaded from: https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg-full.js
SHA-256: a4354ff94fd868aea0cc6eaaa79a57fda0588646fc46ee3700a349ee0a11cbe6

The SVG build embeds its normal math glyphs, so the review preview does not
need a CDN or remote font downloads for the supported built-in TeX packages.

`native-math.js` bundles the same MathJax 3.2.2 TeX/SVG implementation with its
lite DOM adaptor for Qt's in-process QJSEngine. It does not require Node on the
end user's computer. Source is in `native-entry.js`.
SHA-256: 127f9ff1abe23c04da6907398c5c2c09fba9f2290f5b06ae3dd2769f850d2eb2

Rebuild with Node, mathjax-full@3.2.2, and esbuild@0.28.2:

    esbuild native-entry.js --bundle --platform=browser --format=iife --global-name=ExamMath --target=es2015 --minify --outfile=native-math.js

Bundled mhchemparser is distributed under the license in mhchemparser-LICENSE.
