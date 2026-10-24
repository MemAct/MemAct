/* Event capture is injected after vendor/accessibility.js, in a private closure.
 * CSS path construction adapted from HDR UK, MIT. See THIRD_PARTY_NOTICES.md.
 * No password value, raw key stream, clipboard, network, or file content is sent.
 */
(() => {
  "use strict";
  // A popup's initial document can be replaced while retaining its Window.
  // Install on the actual document, but never duplicate listeners on one document.
  if (window.__memact?.document === document) return;
  const secret = new WeakSet();
  const pending = new Map();
  const inflight = new Set();
  const docId = crypto.randomUUID();
  let pointer = null;
  let nativeDrag = false;
  let suppressClickUntil = 0;
  let lastActivation = null;
  let enabled = true;

  function privateElement(el) {
    if (!(el instanceof Element)) return true;
    const sensitive = el.closest('[data-memact-private], [data-memact-ignore]');
    const isSecret = secret.has(el) || !!sensitive ||
      (el.tagName === "INPUT" && (el.type === "password" || /password|one-time-code/.test(el.autocomplete || "")));
    if (isSecret) {
      secret.add(el);
      el.setAttribute("data-memact-private", "");
    }
    return isSecret;
  }

  function markSecrets(root) {
    if (root instanceof Element && root.matches('input[type="password"], input[autocomplete*="password"], input[autocomplete="one-time-code"]')) privateElement(root);
    root.querySelectorAll?.('input[type="password"], input[autocomplete*="password"], input[autocomplete="one-time-code"]').forEach(privateElement);
  }
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === "attributes") {
        if (record.attributeName === "type" && record.oldValue === "password") {
          secret.add(record.target);
          record.target.setAttribute("data-memact-private", "");
          pending.delete(record.target);
        }
        privateElement(record.target);
      }
      for (const node of record.addedNodes) markSecrets(node);
    }
  }).observe(document, {subtree: true, childList: true, attributes: true,
    attributeOldValue: true, attributeFilter: ["type", "autocomplete"]});
  document.addEventListener("DOMContentLoaded", () => markSecrets(document), {once: true});

  function unique(root, selector, el) {
    try { const matches = root.querySelectorAll(selector); return matches.length === 1 && matches[0] === el; }
    catch { return false; }
  }

  function cssSelector(el) {
    if (!(el instanceof Element)) return null;
    const root = el.getRootNode();
    const candidates = [];
    if (el.id) candidates.push("#" + CSS.escape(el.id));
    for (const attr of ["data-testid", "data-cy", "data-test", "name"]) {
      const value = el.getAttribute(attr);
      if (value) candidates.push(`${el.localName}[${attr}="${CSS.escape(value)}"]`);
    }
    let selector = candidates.find(s => unique(root, s, el));
    if (!selector) {
      const parts = [];
      let node = el;
      while (node instanceof Element) {
        let part = node.localName;
        const parent = node.parentElement;
        if (parent) {
          const siblings = Array.from(parent.children).filter(c => c.localName === node.localName);
          if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
        parts.unshift(part);
        if (unique(root, parts.join(" > "), el)) break;
        node = parent;
      }
      selector = parts.join(" > ");
    }
    if (root instanceof ShadowRoot) return `${cssSelector(root.host)} ${selector}`;
    return selector;
  }

  const clean = (text, length = 300) => String(text || "").replace(/\s+/g, " ").trim().slice(0, length);

  function visibleText(el, limit = 300) {
    if (!el || privateElement(el)) return "";
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const parts = [];
    let count = 0;
    let node;
    while ((node = walker.nextNode()) && count++ < 100 && parts.join(" ").length < limit) {
      const parent = node.parentElement;
      if (!parent || parent.closest('script,style,noscript,input,textarea,select,[hidden],[aria-hidden="true"],[data-memact-private],[data-memact-ignore]')) continue;
      const style = getComputedStyle(parent);
      if (style.visibility === "hidden" || style.display === "none") continue;
      parts.push(node.nodeValue);
    }
    return clean(parts.join(" "), limit);
  }

  function describe(el) {
    if (!(el instanceof Element) || privateElement(el)) return null;
    let role = "", name = "";
    try {
      role = MemactAccessibility.getRole(el) || "";
      name = MemactAccessibility.computeAccessibleName(el, {computedStyleSupportsPseudoElements: false});
    } catch { /* A CSS selector remains available if accessible naming fails. */ }
    return {
      role, name: clean(name, 4096), text: visibleText(el, 160), selector: cssSelector(el),
      tag: el.localName, input_type: el.localName === "input" ? el.type : "",
      is_submit: (el.localName === "button" && el.type === "submit" && !!el.form) ||
        (el.localName === "input" && ["submit", "image"].includes(el.type)),
      href: el.localName === "a" ? el.href : ""
    };
  }

  function eventElement(event) {
    const first = event.composedPath?.().find(n => n instanceof Element) || event.target;
    return first instanceof Element ? first : document.body;
  }

  function actionable(el) {
    return el?.closest('button,a[href],input,textarea,select,[role="button"],[role="link"],[role="checkbox"],[role="switch"],[role="slider"],[contenteditable="true"]') || el;
  }

  function send(action, el = null, extra = {}) {
    if (!enabled) return Promise.resolve();
    if (el && privateElement(el)) return Promise.resolve();
    const target = el ? describe(el) : null;
    if (el && !target) return Promise.resolve();
    const context = el?.closest('form,section,article,tr,li,[role="dialog"]') || el?.parentElement;
    const raw = {action, target, nearby_text: context ? visibleText(context) : "",
      at: new Date().toISOString(), time: Date.now(), doc_id: docId,
      url: location.href, title: document.title, ...extra};
    let promise;
    try { promise = Promise.resolve(window.__memactEmit(raw)).catch(() => {}); }
    catch { return Promise.resolve(); }
    inflight.add(promise);
    promise.finally(() => inflight.delete(promise));
    return promise;
  }

  function editable(el) {
    return el?.isContentEditable || el?.localName === "textarea" ||
      (el?.localName === "input" && ["text", "email", "url", "tel", "search", "number", "date", "time", "datetime-local", "month", "week", "color"].includes(el.type));
  }

  function flush(except = null) {
    for (const [el, edit] of pending) {
      if (el === except) continue;
      pending.delete(el);
      if (!privateElement(el)) send({type: "fill", value: edit.value}, el);
    }
    return Promise.all(Array.from(inflight));
  }

  document.addEventListener("input", event => {
    if (!event.isTrusted || event.isComposing) return;
    const el = eventElement(event);
    if (privateElement(el) || !editable(el)) return;
    flush(el);
    pending.set(el, {value: el.isContentEditable ? el.innerText : el.value});
  }, true);

  document.addEventListener("compositionend", event => {
    const el = eventElement(event);
    if (!event.isTrusted || privateElement(el) || !editable(el)) return;
    pending.set(el, {value: el.isContentEditable ? el.innerText : el.value});
  }, true);

  document.addEventListener("focusout", event => {
    if (pending.has(event.target)) flush();
  }, true);

  document.addEventListener("change", event => {
    if (!event.isTrusted) return;
    const el = eventElement(event);
    if (privateElement(el)) return;
    flush();
    if (el.localName === "select") {
      send({type: "select", values: Array.from(el.selectedOptions, o => o.value)}, el);
    } else if (el.localName === "input" && ["checkbox", "radio"].includes(el.type)) {
      send({type: "check", checked: el.checked}, el);
    } else if (el.localName === "input" && el.type === "file") {
      send({type: "upload", files: Array.from(el.files, f => ({name: f.name, size: f.size, mime: f.type}))}, el);
    }
  }, true);

  document.addEventListener("click", event => {
    if (!event.isTrusted || Date.now() < suppressClickUntil) return;
    const raw = eventElement(event);
    const el = actionable(raw);
    if (privateElement(el)) return;
    const label = raw.closest("label");
    const control = label?.control;
    if (control && ["checkbox", "radio", "file"].includes(control.type)) return;
    if (el.localName === "select" || (el.localName === "input" && ["checkbox", "radio", "file"].includes(el.type))) return;
    // Enter in a textbox can synthesize a click on a different submit button.
    // Replaying the key already performs that click, so do not record both.
    if (!event.detail && lastActivation && Date.now() - lastActivation.time < 500) return;
    flush();
    send({type: "click", button: event.button === 1 ? "middle" : event.button === 2 ? "right" : "left",
      modifiers: [event.ctrlKey && "Control", event.metaKey && "Meta", event.altKey && "Alt", event.shiftKey && "Shift"].filter(Boolean)}, el);
  }, true);

  document.addEventListener("contextmenu", event => {
    if (!event.isTrusted) return;
    flush();
    send({type: "click", button: "right", modifiers: []}, actionable(eventElement(event)));
  }, true);

  document.addEventListener("keydown", event => {
    if (!event.isTrusted || event.isComposing || event.repeat) return;
    const el = eventElement(event);
    if (privateElement(el) || ["Control", "Alt", "Meta", "Shift", "AltGraph"].includes(event.key)) return;
    const combo = event.ctrlKey || event.metaKey || event.altKey;
    // Native selects emit their final selection. Recording both the arrow key
    // and that selection would apply the same user action twice on replay.
    if (el.localName === "select" && !combo && ["ArrowUp", "ArrowDown", "Home", "End", "Enter", " "].includes(event.key)) return;
    // Editing/pasting is represented by the resulting fill, not a clipboard read.
    if (editable(el) && combo && ["a", "v", "x", "z", "y"].includes(event.key.toLowerCase())) return;
    const special = ["Enter", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Backspace", "Delete", " "].includes(event.key) || /^F\d+$/.test(event.key);
    if (!combo && (!special || (editable(el) && !["Enter", "Tab", "Escape"].includes(event.key)))) return;
    if (!combo && event.key === " " && editable(el)) return;
    flush();
    const key = [event.ctrlKey && "Control", event.metaKey && "Meta", event.altKey && "Alt", event.shiftKey && "Shift",
      event.key === " " ? "Space" : event.key === "+" ? "Equal" : event.key].filter(Boolean).join("+");
    if (["Enter", " "].includes(event.key)) lastActivation = {el: actionable(el), time: Date.now()};
    send({type: "press", key}, el);
  }, true);

  function fraction(el, x, y) {
    const r = el.getBoundingClientRect();
    return {x: Math.max(0, Math.min(1, (x - r.left) / (r.width || 1))),
      y: Math.max(0, Math.min(1, (y - r.top) / (r.height || 1)))};
  }

  document.addEventListener("pointerdown", event => {
    if (!event.isTrusted || event.button !== 0) return;
    const el = actionable(eventElement(event));
    if (privateElement(el)) return;
    pointer = {el, x: event.clientX, y: event.clientY, start: fraction(el, event.clientX, event.clientY)};
    nativeDrag = false;
  }, true);

  document.addEventListener("pointerup", event => {
    if (!pointer || !event.isTrusted) return;
    const start = pointer;
    pointer = null;
    if (nativeDrag || Math.hypot(event.clientX - start.x, event.clientY - start.y) < 12) return;
    const destination = actionable(document.elementFromPoint(event.clientX, event.clientY) || eventElement(event));
    if (privateElement(destination) || editable(start.el)) return;
    flush();
    suppressClickUntil = Date.now() + 500;
    send({type: "drag", strategy: "pointer", destination: describe(destination),
      delta: {x: (event.clientX - start.x) / innerWidth, y: (event.clientY - start.y) / innerHeight},
      source_position: start.start, target_position: fraction(destination, event.clientX, event.clientY)}, start.el);
  }, true);

  document.addEventListener("dragstart", event => {
    if (!event.isTrusted) return;
    const el = actionable(eventElement(event));
    if (privateElement(el)) return;
    nativeDrag = true;
    flush();
    send({type: "drag_start", source_position: fraction(el, event.clientX, event.clientY)}, el);
  }, true);

  document.addEventListener("drop", event => {
    if (!event.isTrusted) return;
    const el = actionable(eventElement(event));
    if (privateElement(el)) return;
    suppressClickUntil = Date.now() + 500;
    const files = Array.from(event.dataTransfer?.files || [], f => ({name: f.name, size: f.size, mime: f.type}));
    if (files.length) send({type: "drop_files", files}, el);
    else send({type: "drag_end", target_position: fraction(el, event.clientX, event.clientY)}, el);
    pointer = null;
  }, true);

  document.addEventListener("dragend", () => {pointer = null; nativeDrag = false;}, true);
  window.addEventListener("pagehide", () => {flush();}, true);

  // The backend derives the source page/frame from Playwright's binding source.
  // This helper is used only to flush edits, identify iframe elements, and mask secrets.
  Object.defineProperty(window, "__memact", {value: {
    document,
    describe,
    flush,
    reset() {pending.clear(); pointer = null; lastActivation = null;},
    setEnabled(value) {enabled = !!value; pending.clear();},
    markSecrets: () => markSecrets(document)
  }, configurable: true});
  markSecrets(document);
})();
