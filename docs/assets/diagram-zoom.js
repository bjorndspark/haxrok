// Zoom (buttons, Ctrl/⌘ + wheel, trackpad pinch) and pan (drag) for Mermaid
// diagrams. Material renders each diagram into a *closed* shadow root on a
// div.mermaid, so the SVG itself is unreachable — we wrap that host in a
// clipping viewport and CSS-transform the host instead (still vector-crisp).
(() => {
  // Scales are relative to the diagram's natural size: 1 is Mermaid's own
  // font size. A 5000 px-wide ER diagram fits an 850 px column at ~0.17.
  const MIN_SCALE = 0.05;
  const MAX_SCALE = 8;
  const MAX_FIT_SCALE = 1; // on load, never blow a diagram up past natural size
  const BUTTON_STEP = 1.25;
  const MAX_HEIGHT_VH = 0.8;
  const PROBE_WIDTH = 100;

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  function button(label, title, onClick) {
    const el = document.createElement("button");
    el.type = "button";
    el.textContent = label;
    el.title = title;
    el.setAttribute("aria-label", title);
    el.addEventListener("click", onClick);
    return el;
  }

  /**
   * The diagram's natural size, measured through the host. Mermaid draws the
   * SVG at width 100%, capped at its natural width, and out of flow that 100%
   * falls back to the 300 px default -- so any diagram wider than 300 px was
   * measured at 300, and 8x zoom stopped short of legible (45% of natural on
   * a 5369 px diagram). So measure the host's height at two widths: a narrow
   * probe gives the aspect ratio, a huge one lets the cap bite and gives the
   * natural height. Font size and line height 0 keep the inline SVG's
   * baseline gap out of both.
   */
  function naturalSize(host) {
    const s = host.style;
    s.fontSize = "0";
    s.lineHeight = "0";
    s.width = `${PROBE_WIDTH}px`;
    const probe = host.getBoundingClientRect().height;
    s.width = "1000000px";
    const height = host.getBoundingClientRect().height;
    s.fontSize = "";
    s.lineHeight = "";
    s.width = "";
    return { width: probe ? (PROBE_WIDTH * height) / probe : 0, height };
  }

  /**
   * Box height: the diagram's aspect ratio at full content width, capped so
   * tall diagrams don't take over the page. A diagram shrunk to fit is one to
   * zoom into, so it gets the whole cap -- at its own ratio a wide diagram
   * gets a letterbox (290 px for a 3:1 one).
   */
  function boxHeight(viewport, width, height) {
    const cap = window.innerHeight * MAX_HEIGHT_VH;
    const fitWidthScale = viewport.clientWidth / width;
    if (fitWidthScale < 1) return cap;
    return Math.min(height * Math.min(fitWidthScale, MAX_FIT_SCALE), cap);
  }

  /** Wrap one rendered diagram host in a pan/zoom viewport. */
  function enhance(host) {
    if (host.parentElement?.classList.contains("diagram-zoom")) return;

    host.classList.add("diagram-zoom__content");
    const { width, height } = naturalSize(host);
    if (!width || !height) {
      host.classList.remove("diagram-zoom__content"); // hidden (e.g. closed tab); retry later
      return;
    }
    host.style.width = `${width}px`;

    const viewport = document.createElement("div");
    viewport.className = "diagram-zoom";
    viewport.title = "Drag to pan · Ctrl/⌘ + scroll to zoom";
    host.replaceWith(viewport);
    viewport.append(host);

    let scale = 1;
    let x = 0;
    let y = 0;
    const apply = () => {
      host.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    };

    /** Scale to fit the viewport and center; used on load and for reset. */
    const fit = () => {
      scale = clamp(
        Math.min(viewport.clientWidth / width, viewport.clientHeight / height),
        MIN_SCALE,
        MAX_FIT_SCALE,
      );
      x = (viewport.clientWidth - width * scale) / 2;
      y = (viewport.clientHeight - height * scale) / 2;
      apply();
    };

    /** Zoom by `factor`, keeping viewport point (px, py) fixed under the cursor. */
    const zoomAt = (factor, px, py) => {
      const next = clamp(scale * factor, MIN_SCALE, MAX_SCALE);
      x = px - ((px - x) * next) / scale;
      y = py - ((py - y) * next) / scale;
      scale = next;
      apply();
    };
    const zoomCenter = (factor) => {
      zoomAt(factor, viewport.clientWidth / 2, viewport.clientHeight / 2);
    };

    viewport.style.height = `${Math.round(boxHeight(viewport, width, height))}px`;
    fit();

    const controls = document.createElement("div");
    controls.className = "diagram-zoom__controls";
    controls.append(
      button("+", "Zoom in", () => {
        zoomCenter(BUTTON_STEP);
      }),
      button("−", "Zoom out", () => {
        zoomCenter(1 / BUTTON_STEP);
      }),
      button("⟲", "Reset view", fit),
    );
    viewport.append(controls);

    // Plain wheel keeps scrolling the page; Ctrl/⌘ + wheel (and trackpad
    // pinch, which browsers report as ctrl + wheel) zooms.
    viewport.addEventListener(
      "wheel",
      (e) => {
        if (!e.ctrlKey && !e.metaKey) return;
        e.preventDefault();
        const rect = viewport.getBoundingClientRect();
        zoomAt(Math.exp(-e.deltaY * 0.002), e.clientX - rect.left, e.clientY - rect.top);
      },
      { passive: false },
    );

    let drag = null;
    viewport.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || controls.contains(e.target)) return;
      drag = { id: e.pointerId, startX: e.clientX - x, startY: e.clientY - y };
      viewport.setPointerCapture(e.pointerId);
      viewport.classList.add("diagram-zoom--dragging");
    });
    viewport.addEventListener("pointermove", (e) => {
      if (drag?.id !== e.pointerId) return;
      x = e.clientX - drag.startX;
      y = e.clientY - drag.startY;
      apply();
    });
    const endDrag = (e) => {
      if (drag?.id !== e.pointerId) return;
      drag = null;
      viewport.classList.remove("diagram-zoom--dragging");
    };
    viewport.addEventListener("pointerup", endDrag);
    viewport.addEventListener("pointercancel", endDrag);
  }

  // Material renders diagrams asynchronously (after lazy-loading Mermaid), so
  // watch for the host divs to appear instead of running once on load.
  const scan = () => {
    document.querySelectorAll("div.mermaid").forEach(enhance);
  };
  new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });
  scan();
})();
