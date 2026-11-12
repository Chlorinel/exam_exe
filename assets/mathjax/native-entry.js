// Build only: bundle with esbuild using mathjax-full@3.2.2, target ES2015.
const {mathjax} = require('mathjax-full/js/mathjax.js');
const {TeX} = require('mathjax-full/js/input/tex.js');
const {SVG} = require('mathjax-full/js/output/svg.js');
const {liteAdaptor} = require('mathjax-full/js/adaptors/liteAdaptor.js');
const {RegisterHTMLHandler} = require('mathjax-full/js/handlers/html.js');
const {AllPackages} = require('mathjax-full/js/input/tex/AllPackages.js');

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const document = mathjax.document('', {
  InputJax: new TeX({packages: AllPackages}),
  OutputJax: new SVG({fontCache: 'none'})
});

exports.renderMath = function(tex, display) {
  try {
    document.inputJax[0].parseOptions.tags.reset(0);
    const node = document.convert(tex, {display: !!display, em: 20, ex: 10, containerWidth: 600});
    const svg = adaptor.firstChild(node);
    const width = parseFloat(adaptor.getAttribute(svg, 'width')) * 10;
    const height = parseFloat(adaptor.getAttribute(svg, 'height')) * 10;
    adaptor.setAttribute(svg, 'width', String(width));
    adaptor.setAttribute(svg, 'height', String(height));
    return JSON.stringify({svg: adaptor.outerHTML(svg), width, height});
  } catch (error) {
    return JSON.stringify({error: String(error)});
  }
};
