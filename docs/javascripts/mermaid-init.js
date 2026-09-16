// Render the mermaid diagrams.
//
// Material for MkDocs has its own mermaid loader, but it requests
// unpkg.com/mermaid@11/dist/mermaid.min.js, which 404s: mermaid 11 does not publish that file on
// unpkg. So the fence writes class "mermaid-diagram" rather than "mermaid", Material leaves it
// alone, and this renders the diagrams with a pinned build from jsDelivr.
//
// Diagrams are re-rendered when the palette changes, because mermaid bakes its colors into the
// SVG at render time and Material switches themes without reloading the page.

(function () {
  "use strict";

  var SELECTOR = ".mermaid-diagram";

  function scheme() {
    return document.body.getAttribute("data-md-color-scheme") === "slate" ? "dark" : "default";
  }

  function render() {
    if (!window.mermaid) return;

    var nodes = document.querySelectorAll(SELECTOR);
    if (!nodes.length) return;

    nodes.forEach(function (el) {
      // Keep the diagram source so a theme change can re-render from it.
      if (!el.dataset.source) el.dataset.source = el.textContent;
      if (el.dataset.rendered === scheme()) return;
      el.removeAttribute("data-processed");
      el.innerHTML = el.dataset.source;
      el.dataset.rendered = scheme();
    });

    window.mermaid.initialize({
      startOnLoad: false,
      theme: scheme(),
      securityLevel: "loose",
      flowchart: { useMaxWidth: true, htmlLabels: true },
      sequence: { useMaxWidth: true }
    });

    try {
      window.mermaid.run({ querySelector: SELECTOR });
    } catch (e) {
      // A malformed diagram should not take the rest of the page down.
      if (window.console) console.error("mermaid render failed", e);
    }
  }

  function watchPalette() {
    var form = document.querySelector("form[data-md-component=palette]");
    if (!form) return;
    form.addEventListener("change", function () {
      // Material writes the new scheme on the next frame.
      setTimeout(render, 60);
    });
  }

  // Material's instant navigation swaps page content without a reload, and exposes document$.
  if (typeof document$ !== "undefined" && document$ && document$.subscribe) {
    document$.subscribe(function () {
      render();
      watchPalette();
    });
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      render();
      watchPalette();
    });
  } else {
    render();
    watchPalette();
  }
})();
