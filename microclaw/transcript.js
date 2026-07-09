/*
  Microclaw transcript renderer, shared by history_viewer.html (`microclaw
  view-history`) and serve.html (`microclaw serve`).

  Pure rendering: hand it an array of Anthropic messages and a container, it
  builds the transcript and returns the counts. Everything page-specific (drop
  zone, composer, data loading) lives in the pages.

  This file is inlined into a <style>-less script block by the CLI, so it must
  never contain a literal end-script tag. tests/test_history_viewer.py enforces
  that.
*/
(function (global) {
  "use strict";

  const esc = (s) => String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  // tiny inline markdown: **bold**, `code`, paragraphs
  function md(text) {
    return esc(text)
      .split(/\n{2,}/)
      .map(p => "<p>" + p
        .replace(/\n/g, "<br>")
        .replace(/`([^`]+)`/g, (_, c) => "<code>" + c + "</code>")
        .replace(/\*\*([^*]+)\*\*/g, (_, b) => "<strong>" + b + "</strong>")
        + "</p>")
      .join("");
  }

  function fmtJSON(val) {
    let obj = val;
    if (typeof val === "string") {
      try { obj = JSON.parse(val); } catch { return esc(val); }
    }
    return esc(JSON.stringify(obj, null, 2));
  }

  function preview(input) {
    const s = typeof input === "string" ? input : JSON.stringify(input);
    if (!s || s === "{}" || s === "null") return "";
    return s.length > 90 ? s.slice(0, 90) + "…" : s;
  }

  // The image block's fields land inside an HTML attribute, where esc() is not
  // enough — it leaves quotes alone, so a media_type of `x" onerror="...` would
  // break out of src= and inject attributes. Whitelist instead of escaping: a
  // history JSON can come from anywhere (the viewer takes a dropped file).
  const MEDIA_TYPES = /^image\/(png|jpeg|gif|webp)$/;
  const dataURL = (source) => "data:" +
    (MEDIA_TYPES.test(source.media_type) ? source.media_type : "image/png") +
    ";base64," + String(source.data).replace(/[^A-Za-z0-9+/=]/g, "");

  // A tool_result's content is either a plain string/object or, for the tools
  // that return a thumbnail (snap_and_analyze, run_autofocus), a list of
  // Anthropic content blocks: a text block plus a base64 PNG image block.
  function renderResult(content) {
    if (!Array.isArray(content)) {
      return '<pre class="json result">' + fmtJSON(content) + "</pre>";
    }
    const parts = content.map(b => {
      if (b && b.type === "image" && b.source && b.source.type === "base64") {
        return '<img class="thumb" alt="snap thumbnail" src="' + dataURL(b.source) + '">';
      }
      const val = b && b.type === "text" ? b.text : b;
      return '<pre class="json result">' + fmtJSON(val) + "</pre>";
    });
    return '<div class="result-blocks">' + parts.join("") + "</div>";
  }

  // build one collapsible tool card
  function toolCard(block, result) {
    const det = document.createElement("details");
    det.className = "tool";
    const isErr = result && result.is_error;
    det.innerHTML =
      "<summary>" +
        '<span class="chev">▶</span>' +
        '<span class="tool-badge' + (isErr ? " err-badge" : "") + '">' + (isErr ? "error" : "tool") + "</span>" +
        '<span class="tool-name">' + esc(block.name) + "</span>" +
        '<span class="tool-preview">' + esc(preview(block.input)) + "</span>" +
      "</summary>";
    const body = document.createElement("div");
    body.className = "tool-body";
    if (block.input && Object.keys(block.input).length) {
      body.innerHTML += '<div><div class="kv-label">Input</div><pre class="json">' + fmtJSON(block.input) + "</pre></div>";
    }
    if (result !== undefined) {
      body.innerHTML += '<div><div class="kv-label">Result</div>' + renderResult(result.content) + "</div>";
    } else {
      body.innerHTML += '<div><div class="kv-label">Result</div><pre class="json">(no result recorded)</pre></div>';
    }
    det.appendChild(body);
    return det;
  }

  function roleTag(cls, label) {
    const d = document.createElement("div");
    d.className = "role-tag role-" + cls;
    d.innerHTML = '<span class="dot"></span>' + label;
    return d;
  }

  /* Render `history` into `tx`, replacing its contents.
     Returns {userTurns, asstTurns, toolCalls}. */
  function render(history, tx) {
    if (!Array.isArray(history)) throw new Error("Top level is not an array of messages.");
    tx.innerHTML = "";

    // index tool_results by tool_use_id
    const results = {};
    for (const m of history) {
      if (Array.isArray(m.content)) {
        for (const b of m.content) {
          if (b && b.type === "tool_result") results[b.tool_use_id] = b;
        }
      }
    }

    let userTurns = 0, asstTurns = 0, toolCalls = 0, lastRole = null;

    for (const m of history) {
      const content = m.content;

      // user message
      if (m.role === "user") {
        // plain string user prompt, or array with a text block (not just tool_result echoes)
        let userText = null;
        if (typeof content === "string") userText = content;
        else if (Array.isArray(content)) {
          const texts = content.filter(b => b.type === "text").map(b => b.text);
          if (texts.length) userText = texts.join("\n\n");
        }
        if (userText != null && userText.trim() !== "") {
          userTurns++;
          if (lastRole !== "user") tx.appendChild(roleTag("user", "You"));
          lastRole = "user";
          const b = document.createElement("div");
          b.className = "turn";
          b.innerHTML = '<div class="bubble user">' + md(userText) + "</div>";
          tx.appendChild(b);
        }
        // tool_result-only user messages are folded into their tool card — skip
        continue;
      }

      // assistant message
      if (m.role === "assistant" && Array.isArray(content)) {
        const blocks = content.filter(b => b.type === "text" || b.type === "tool_use");
        if (!blocks.length) continue;
        asstTurns++;
        if (lastRole !== "assistant") tx.appendChild(roleTag("asst", "Microclaw"));
        lastRole = "assistant";
        const wrap = document.createElement("div");
        wrap.className = "turn";
        for (const b of content) {
          if (b.type === "text" && b.text && b.text.trim()) {
            const el = document.createElement("div");
            el.className = "bubble asst";
            el.innerHTML = md(b.text);
            wrap.appendChild(el);
          } else if (b.type === "tool_use") {
            toolCalls++;
            wrap.appendChild(toolCard(b, results[b.id]));
          }
        }
        tx.appendChild(wrap);
      }
    }

    return { userTurns, asstTurns, toolCalls };
  }

  function initTheme(btn) {
    const root = document.documentElement;
    btn.addEventListener("click", () => {
      const cur = root.getAttribute("data-theme")
        || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      root.setAttribute("data-theme", cur === "dark" ? "light" : "dark");
    });
  }

  const setOpen = (tx, open) => tx.querySelectorAll("details.tool").forEach(d => d.open = open);

  global.Transcript = {
    esc, md, fmtJSON, preview, renderResult, toolCard, render, initTheme,
    expandAll: (tx) => setOpen(tx, true),
    collapseAll: (tx) => setOpen(tx, false),
  };
})(window);
