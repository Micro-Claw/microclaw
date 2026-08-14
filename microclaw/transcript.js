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

  // esc() is for text nodes: it leaves quotes alone, so a value landing inside
  // an HTML attribute needs them escaped too or it breaks out of the attribute.
  const escAttr = (s) => esc(s).replace(/"/g, "&quot;");

  // tiny inline markdown: fenced blocks, **bold**, `code`, paragraphs
  function md(text) {
    // Fenced blocks are held aside FIRST. The inline-code rule below needs at
    // least one non-backtick between its delimiters, so on a ``` fence it
    // matches from the third backtick to the first of the closing fence: the
    // block's newlines collapse into one <code> span and two backticks are left
    // sitting on the page. That is what the M5 gate saw on 2026-08-13 — every
    // verbatim tool result the model quoted came out as a run-on line wearing
    // stray backticks.
    // The placeholder is NUL-delimited. A transcript carries whatever a model
    // wrote or a dropped history file contains, so a printable marker such as
    // " 0 " or "[[0]]" would eventually appear in ordinary prose and be
    // swapped for somebody else's code block.
    const blocks = [];
    const hold = (_, code) => {
      blocks.push(String(code).replace(/\n+$/, ""));
      return "\u0000" + (blocks.length - 1) + "\u0000";
    };
    const held = String(text)
      .replace(/```[^\n]*\n?([\s\S]*?)```/g, hold)
      // A fence the model is still streaming has no closing delimiter yet.
      // `serve` renders every text delta, so without this the block flickers as
      // literal backticks until the turn ends.
      .replace(/```[^\n]*\n?([\s\S]*)$/, hold);
    return esc(held)
      .split(/\n{2,}/)
      .map(p => "<p>" + p
        .replace(/\n/g, "<br>")
        .replace(/`([^`]+)`/g, (_, c) => "<code>" + c + "</code>")
        .replace(/\*\*([^*]+)\*\*/g, (_, b) => "<strong>" + b + "</strong>")
        + "</p>")
      .join("")
      // Close the paragraph around each block: a <pre> nested in a <p> is
      // invalid and the browser closes the <p> itself, in the wrong place.
      .replace(/\u0000(\d+)\u0000/g, (_, i) =>
        '</p><pre class="code">' + esc(blocks[i]) + "</pre><p>")
      .replace(/<p><\/p>/g, "");
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

  /* Pull a tool's `artifact` declaration out of its result, if it made one.

     The tools say so structurally — {"artifact": {"kind": ..., "path": ...}} —
     rather than the renderer regexing paths out of prose, which would work for
     six months and then match a filename inside an error message. */
  function artifactOf(content) {
    let obj = content;
    if (Array.isArray(content)) {
      const text = content.find(b => b && b.type === "text");
      if (!text) return null;
      obj = text.text;
    }
    if (typeof obj === "string") {
      try { obj = JSON.parse(obj); } catch { return null; }
    }
    const a = obj && obj.artifact;
    return a && typeof a.path === "string" ? a : null;
  }

  const basename = (p) => String(p).split(/[\\/]/).pop();

  /* A download chip, but only where there is a server to download from.
     view-history is a file:// page with no /api/artifact behind it, so there the
     chip renders as inert text naming the file. */
  function artifactChip(artifact) {
    const name = esc(basename(artifact.path));
    const kind = esc(artifact.kind || "file");
    const title = escAttr(artifact.path);
    const served = typeof location !== "undefined" && /^https?:$/.test(location.protocol);
    if (!served) {
      return '<div class="artifact inert" title="' + title + '">' +
        '<span class="artifact-kind">' + kind + "</span>" + name + "</div>";
    }
    return '<a class="artifact" download href="/api/artifact?path=' +
      encodeURIComponent(artifact.path) + '" title="' + title + '">' +
      '<span class="artifact-kind">' + kind + "</span>" + name + "</a>";
  }

  /* Build one collapsible tool card.

     A missing result means two different things. In a saved history the tool
     result was never recorded; in a turn that is streaming right now (`live`)
     the tool is still running. Same absent value, opposite stories. */
  function toolCard(block, result, live) {
    const det = document.createElement("details");
    det.className = "tool";
    const isErr = result && result.is_error;
    const pending = result === undefined && live;
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
      const artifact = artifactOf(result.content);
      body.innerHTML += '<div><div class="kv-label">Result</div>' +
        renderResult(result.content) +
        (artifact ? artifactChip(artifact) : "") + "</div>";
    } else if (pending) {
      body.innerHTML += '<div><div class="kv-label">Result</div>' +
        '<div class="tool-pending"><span class="spin"></span>running…</div></div>';
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
     `opts.live` marks the transcript as a turn in flight, which only `serve`
     ever is: a tool_use with no tool_result is then drawn as still running
     rather than as a result that was never recorded.
     Returns {userTurns, asstTurns, toolCalls}. */
  function render(history, tx, opts) {
    const live = !!(opts && opts.live);
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

      // assistant message. A plain string is as valid a shape here as it is for
      // a user turn — microclaw seeds setup mode's opening message that way, and
      // rendering only arrays silently dropped it: the page a novice met on a
      // fresh install was empty apart from banners, so they had to type
      // something to find out what setup wanted (block 48e's clean-profile
      // acceptance run, 2026-08-14).
      if (m.role === "assistant" && typeof content === "string") {
        if (content.trim() === "") continue;
        asstTurns++;
        if (lastRole !== "assistant") tx.appendChild(roleTag("asst", "Microclaw"));
        lastRole = "assistant";
        const only = document.createElement("div");
        only.className = "turn";
        only.innerHTML = '<div class="bubble asst">' + md(content) + "</div>";
        tx.appendChild(only);
        continue;
      }
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
            wrap.appendChild(toolCard(b, results[b.id], live));
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

  /* Parse either legacy JSON-array history or append-only JSONL. A torn final
     JSONL record is recoverable and returned as a visible warning. */
  function parseHistoryText(raw) {
    const text = String(raw || "");
    const trimmed = text.trimStart();
    if (!trimmed) return { messages: [], warning: null };
    if (trimmed[0] === "[") return { messages: JSON.parse(text), warning: null };
    const lines = text.match(/[^\n]*\n|[^\n]+$/g) || [];
    const messages = [];
    let warning = null;
    for (let i = 0; i < lines.length; i++) {
      const physical = lines[i];
      const line = physical.replace(/[\r\n]+$/, "");
      if (!line.trim()) continue;
      try {
        const record = JSON.parse(line);
        if (!record || Array.isArray(record) || typeof record !== "object")
          throw new Error("record is not an object");
        messages.push(record);
      } catch (e) {
        const tornFinal = i === lines.length - 1 && !/[\r\n]$/.test(physical);
        if (!tornFinal) throw new Error("Malformed JSONL record " + (i + 1) + ": " + e.message);
        warning = "Incomplete final JSONL record ignored; complete messages were recovered.";
      }
    }
    return { messages, warning };
  }

  global.Transcript = {
    esc, escAttr, md, fmtJSON, preview, renderResult, toolCard, render, initTheme,
    artifactOf, artifactChip, parseHistoryText,
    expandAll: (tx) => setOpen(tx, true),
    collapseAll: (tx) => setOpen(tx, false),
  };
})(window);
