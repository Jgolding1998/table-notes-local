/* Table Notes front end.
 * Talks only to the local app that served this page (same origin).
 * All file-derived text is written with textContent / DOM APIs; never innerHTML.
 * Nothing is persisted in the browser.
 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var SVG_NS = "http://www.w3.org/2000/svg";

  var el = {
    example: $("example-btn"),
    fileInput: $("file-input"),
    drop: $("drop"),
    fileError: $("file-error"),
    status: $("status"),
    alert: $("alert"),
    reset: $("reset-btn"),
    results: $("results"),
    empty: $("empty"),
    resultName: $("result-name"),
    resultsTitle: $("results-title"),
    resultTag: $("result-tag"),
    figures: $("figures"),
    notes: $("notes"),
    metricField: $("metric-field"),
    metric: $("metric"),
    chartArea: $("chart-area"),
    profile: $("profile"),
    preview: $("preview"),
    previewHint: $("preview-hint"),
    includeName: $("include-name"),
    download: $("download-btn"),
    version: $("version"),
    limitSize: $("limit-size"),
    limitRows: $("limit-rows"),
    limitCols: $("limit-cols")
  };

  var state = {
    config: null,
    controller: null, // AbortController for the request in flight
    seq: 0,           // increments on every new request or reset; stale responses are dropped
    result: null
  };

  var intFmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  var numFmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

  /* ---------- small helpers ---------- */

  function make(tag, opts, children) {
    var node = document.createElement(tag);
    if (opts) {
      if (opts.text != null) node.textContent = String(opts.text);
      if (opts.cls) node.className = opts.cls;
      if (opts.attrs) {
        Object.keys(opts.attrs).forEach(function (k) { node.setAttribute(k, opts.attrs[k]); });
      }
    }
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }

  function svg(tag, attrs, text) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text != null) node.textContent = String(text);
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function isNum(v) { return typeof v === "number" && isFinite(v); }

  function fmtNum(v) { return isNum(v) ? numFmt.format(v) : "—"; }
  function fmtInt(v) { return isNum(v) ? intFmt.format(v) : "—"; }

  function fmtBytes(b) {
    if (!isNum(b)) return "";
    if (b >= 1048576) return numFmt.format(b / 1048576) + " MB";
    if (b >= 1024) return numFmt.format(b / 1024) + " KB";
    return intFmt.format(b) + " bytes";
  }

  function plural(n, one, many) { return fmtInt(n) + " " + (n === 1 ? one : many); }

  function cellText(v) {
    if (v === null || v === undefined || v === "") return null;
    if (typeof v === "number") return isNum(v) ? numFmt.format(v) : String(v);
    if (typeof v === "boolean") return v ? "true" : "false";
    return String(v);
  }

  function arr(v) { return Array.isArray(v) ? v : []; }

  /* ---------- status and errors ---------- */

  function setStatus(text, busy) {
    el.status.textContent = text;
    el.status.classList.toggle("is-busy", !!busy);
  }

  function showAlert(message, retry) {
    clear(el.alert);
    el.alert.appendChild(make("p", { text: message }));
    if (retry) {
      var b = make("button", { text: retry.label, cls: "btn", attrs: { type: "button" } });
      b.addEventListener("click", retry.run);
      el.alert.appendChild(b);
    }
    el.alert.hidden = false;
  }

  function hideAlert() {
    // If a keyboard user just pressed the alert's retry button, keep focus near the file controls
    // instead of dropping it to the top of the page.
    var hadFocus = el.alert.contains(document.activeElement);
    el.alert.hidden = true;
    clear(el.alert);
    if (hadFocus) (el.fileInput.disabled ? el.example : el.fileInput).focus();
  }

  function showFileError(message) {
    el.fileError.textContent = message;
    el.fileError.hidden = false;
  }
  function hideFileError() { el.fileError.hidden = true; el.fileError.textContent = ""; }

  function friendlyHttpError(status, detail) {
    if (typeof detail === "string" && detail.trim() && detail.length <= 300) return detail.trim();
    if (status === 413) return "The selected file is larger than the limit of " + fmtBytes(state.config && state.config.max_file_bytes) + ".";
    if (status === 415) return "The selected file must be a CSV or XLSX file.";
    if (status === 400 || status === 422) return "The selected file couldn't be read as a table.";
    if (status === 403) return "The local app didn't accept this request. Reload the page and try again.";
    if (status >= 500) return "The local app hit a problem while reading the file. Try again, or try a different file.";
    return "The request didn't work (status " + status + "). Try again.";
  }

  /* ---------- config ---------- */

  // keepAlert: refresh the token quietly without replacing a request error the user still needs to read.
  // A refresh only touches the status, alert and file chooser if no newer request, retry or Clear has
  // happened since it started (state.seq unchanged); a stale success still keeps the fresh token.
  function loadConfig(keepAlert) {
    var seq = state.seq;
    if (!keepAlert) setStatus("Connecting to the local app…", true);
    return fetch("/api/config", { headers: { Accept: "application/json" }, credentials: "omit", cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (cfg) {
        if (!cfg || typeof cfg.csrf_token !== "string") throw new Error("bad config");
        state.config = cfg;
        el.limitSize.textContent = fmtBytes(cfg.max_file_bytes);
        el.limitRows.textContent = fmtInt(cfg.max_rows);
        el.limitCols.textContent = fmtInt(cfg.max_columns);
        el.version.textContent = cfg.version ? "Version " + cfg.version : "";
        el.fileInput.disabled = false;
        if (!keepAlert && seq === state.seq) {
          hideAlert();
          if (!state.result) setStatus("Ready.", false);
        }
        return cfg;
      })
      .catch(function () {
        if (seq !== state.seq) return null;
        state.config = null;
        el.fileInput.disabled = true;
        setStatus("Not connected.", false);
        showAlert("Couldn't reach the local Table Notes app. Check that it is still running in your terminal, then try again.",
          { label: "Try again", run: function () { loadConfig(); } });
        return null;
      });
  }

  /* ---------- requests ---------- */

  function beginRequest(label) {
    if (state.controller) state.controller.abort();
    state.controller = new AbortController();
    state.seq += 1;
    hideAlert();
    hideFileError();
    clearResults(); // never leave an earlier result on screen under a new request's status or error
    setStatus(label, true);
    el.reset.disabled = false;
    return { seq: state.seq, signal: state.controller.signal };
  }

  function isCurrent(req) { return req.seq === state.seq; }

  function readJson(res) {
    return res.text().then(function (body) {
      var data = null;
      try { data = body ? JSON.parse(body) : null; } catch (e) { data = undefined; }
      return { res: res, data: data };
    });
  }

  // Clear the chooser after a failed upload: browsers don't fire "change" when the same file is
  // picked again, so a user who fixes the file or waits would otherwise have no way to resend it.
  function failed(sourceLabel) {
    el.fileInput.value = "";
    setStatus("Couldn't analyze " + sourceLabel + ".", false);
  }

  function handleResponse(req, sourceLabel, retry) {
    return function (out) {
      if (!isCurrent(req)) return;
      state.controller = null;
      if (!out.res.ok) {
        var status = out.res.status;
        failed(sourceLabel);
        if (status === 403) {
          // Usually the local app restarted and issued a new request token. Fetch the new token, then
          // explain and offer a retry. (If that fails, loadConfig shows the connection error itself.)
          setStatus("Reconnecting to the local app…", true);
          loadConfig(true).then(function (cfg) {
            if (!cfg || !isCurrent(req)) return;
            setStatus("Couldn't analyze " + sourceLabel + ".", false);
            showAlert("The local app didn't accept the request, probably because it restarted. This page has reconnected, so try again.",
              { label: "Try again", run: retry });
          });
          return;
        }
        var retryable = status === 408 || status === 429 || status >= 500;
        showAlert(friendlyHttpError(status, out.data && out.data.detail), retryable ? { label: "Try again", run: retry } : null);
        return;
      }
      if (!out.data || typeof out.data !== "object") {
        failed(sourceLabel);
        showAlert("The local app sent back a response Table Notes couldn't read. Try again.", { label: "Try again", run: retry });
        return;
      }
      render(out.data);
      setStatus("Showing " + sourceLabel + ".", false);
      el.resultsTitle.focus({ preventScroll: false });
    };
  }

  function handleFailure(req, sourceLabel, retry) {
    return function (err) {
      if (!isCurrent(req)) return; // superseded by a newer request or a reset
      state.controller = null;
      if (err && err.name === "AbortError") return;
      failed(sourceLabel);
      showAlert("Couldn't reach the local Table Notes app. Check that it is still running, then try again.",
        { label: "Try again", run: retry });
    };
  }

  function runExample() {
    el.fileInput.value = ""; // so choosing the same file again later still fires "change"
    var req = beginRequest("Opening the sample table…");
    fetch("/api/example", { headers: { Accept: "application/json" }, credentials: "omit", cache: "no-store", signal: req.signal })
      .then(readJson)
      .then(handleResponse(req, "the sample table", runExample))
      .catch(handleFailure(req, "the sample table", runExample));
  }

  // The retry button holds the File only while its alert is on screen; Clear or any new request
  // removes the alert and with it this reference. A fresh token is fetched first in case the app restarted.
  // Each press starts a new generation, so Clear, a newer request or a second press while the token is
  // loading cancels this resend instead of letting it bring back or replace something.
  function retryFile(file) {
    return function () {
      state.seq += 1;
      var seq = state.seq;
      setStatus("Reconnecting to the local app…", true);
      loadConfig(true).then(function (cfg) { if (cfg && seq === state.seq) runFile(file); });
    };
  }

  function checkFile(file) {
    var name = String(file.name || "").toLowerCase();
    if (!/\.(csv|xlsx)$/.test(name)) return "The selected file must be a .csv or .xlsx file.";
    if (file.size === 0) return "The selected file is empty.";
    var max = state.config && state.config.max_file_bytes;
    if (isNum(max) && file.size > max) return "The selected file must be smaller than " + fmtBytes(max) + ". This one is " + fmtBytes(file.size) + ".";
    return null;
  }

  function runFile(file) {
    if (!state.config) {
      showFileError("Not connected to the local app yet.");
      return;
    }
    var problem = checkFile(file);
    if (problem) {
      // A rejected choice should not leave an older result looking like it belongs to this file.
      resetAll("Choose a different file.");
      showFileError(problem);
      el.fileInput.focus();
      return;
    }
    var req = beginRequest("Reading the file in the local app…");
    var body = new FormData();
    body.append("file", file, file.name);
    fetch("/api/analyze", {
      method: "POST",
      body: body,
      credentials: "omit",
      cache: "no-store",
      headers: { Accept: "application/json", "X-Table-Notes-CSRF": state.config.csrf_token },
      signal: req.signal
    })
      .then(readJson)
      .then(handleResponse(req, "your file", retryFile(file)))
      .catch(handleFailure(req, "your file", retryFile(file)));
  }

  /* ---------- reset ---------- */

  function clearResults() {
    state.result = null;
    el.includeName.checked = false;
    clear(el.figures); clear(el.notes); clear(el.chartArea);
    clear(el.profile); clear(el.preview); clear(el.metric);
    el.metric.usable = [];
    el.resultName.textContent = "";
    el.previewHint.textContent = "";
    el.results.hidden = true;
    el.empty.hidden = false;
  }

  function resetAll(message) {
    if (state.controller) state.controller.abort();
    var hadRequest = !!state.controller;
    state.controller = null;
    state.seq += 1;
    el.fileInput.value = "";
    clearResults();
    el.reset.disabled = true;
    hideAlert();
    hideFileError();
    setStatus(message || (hadRequest
      ? "Cleared. This page stopped waiting for the earlier request."
      : "Cleared. Nothing is loaded."), false);
  }

  /* ---------- rendering ---------- */

  function render(data) {
    var shape = data.shape || {};
    var quality = data.quality || {};
    state.result = data;

    var name = cellText(data.filename);
    el.resultName.textContent = data.is_example ? "Sample: " + (name || "weekly orders") : (name || "Your file");
    el.resultTag.hidden = !data.is_example;
    el.includeName.checked = false;

    // Show the results first so the chart can measure its real width.
    el.empty.hidden = true;
    el.results.hidden = false;
    renderFigures(shape, quality);
    renderNotes(arr(data.narrative), arr(quality.warnings), arr(quality.empty_columns), shape);
    renderMetricSelect(arr(data.series));
    renderProfile(arr(data.columns));
    renderPreview(data.preview || {}, shape, arr(data.columns));
    el.reset.disabled = false;
  }

  function renderFigures(shape, quality) {
    clear(el.figures);
    var items = [
      ["Rows", fmtInt(shape.rows), false],
      ["Columns", fmtInt(shape.columns), false],
      ["Blank cells", fmtInt(quality.missing_cells), quality.missing_cells > 0],
      ["Duplicate rows", fmtInt(quality.duplicate_rows), quality.duplicate_rows > 0]
    ];
    items.forEach(function (it) {
      el.figures.appendChild(make("div", null, [
        make("dt", { text: it[0] }),
        make("dd", { text: it[1], cls: it[2] ? "is-flag" : "" })
      ]));
    });
  }

  function renderNotes(narrative, warnings, emptyCols, shape) {
    clear(el.notes);
    warnings.forEach(function (w) {
      var text = cellText(w);
      if (text) el.notes.appendChild(make("li", { cls: "is-warning" }, [make("strong", { text: "Check this" }), make("p", { text: text })]));
    });
    if (emptyCols.length) {
      el.notes.appendChild(make("li", { cls: "is-warning" }, [
        make("strong", { text: emptyCols.length === 1 ? "One column is completely blank" : emptyCols.length + " columns are completely blank" }),
        make("p", { text: emptyCols.map(cellText).filter(Boolean).join(", ") })
      ]));
    }
    narrative.forEach(function (n) {
      if (!n) return;
      var title = cellText(n.title), body = cellText(n.body);
      if (!title && !body) return;
      el.notes.appendChild(make("li", null, [title ? make("strong", { text: title }) : null, body ? make("p", { text: body }) : null]));
    });
    if (!el.notes.firstChild) {
      var msg = shape.rows === 0
        ? "The file was read, but it has no data rows."
        : "Nothing stood out. That doesn't prove the data is right, only that no gaps or duplicates were found.";
      el.notes.appendChild(make("li", null, [make("p", { text: msg })]));
    }
  }

  function renderProfile(columns) {
    clear(el.profile);
    if (!columns.length) {
      el.profile.appendChild(make("caption", { text: "No columns were found.", cls: "hint" }));
      return;
    }
    var head = ["Column", "Type", "Blank", "Distinct", "Min", "Median", "Max", "Mean", "Sum"];
    var thead = make("thead", null, [make("tr", null, head.map(function (h, i) {
      return make("th", { text: h, cls: i >= 2 ? "num" : "", attrs: { scope: "col" } });
    }))]);
    var tbody = make("tbody");
    columns.forEach(function (c) {
      var s = c.stats || {};
      var numeric = c.kind === "number";
      var name = cellText(c.name) || "(no name)";
      var row = make("tr", null, [
        make("th", { text: name, attrs: { scope: "row", title: name } }),
        make("td", null, [make("span", { text: c.kind || "unknown", cls: "kind" })]),
        make("td", { text: fmtInt(c.missing), cls: "num" + (c.missing > 0 ? " miss-flag" : "") }),
        make("td", { text: fmtInt(c.unique), cls: "num" })
      ]);
      ["min", "median", "max", "mean", "sum"].forEach(function (k) {
        var v = s[k];
        // Strings are shown verbatim and never parsed: dates send ISO min/max, and a numeric sum
        // beyond the safe integer limit arrives as exact decimal text.
        var text = isNum(v) ? fmtNum(v) : (typeof v === "string" && v ? v : "—");
        if (!numeric && (k === "sum" || k === "mean")) text = "—";
        row.appendChild(make("td", { text: text, cls: "num" }));
      });
      tbody.appendChild(row);
    });
    el.profile.appendChild(make("caption", { text: "One row per column in the file. Statistics use every accepted row.", cls: "visually-hidden" }));
    el.profile.appendChild(thead);
    el.profile.appendChild(tbody);
  }

  function renderPreview(preview, shape, columns) {
    var numberNames = {};
    columns.forEach(function (c) { if (c && c.kind === "number") numberNames[String(c.name)] = true; });
    clear(el.preview);
    var cols = arr(preview.columns), rows = arr(preview.rows);
    if (!cols.length || !rows.length) {
      el.previewHint.textContent = shape.rows === 0 ? "There are no data rows to preview." : "No preview rows were returned.";
      return;
    }
    el.previewHint.textContent = rows.length < shape.rows
      ? "First " + plural(rows.length, "row", "rows") + " of " + fmtInt(shape.rows) + "."
      : "All " + plural(rows.length, "row", "rows") + ".";
    var numericCol = cols.map(function (_, i) {
      return numberNames[String(cols[i])] === true;
    });
    var thead = make("thead", null, [make("tr", null, cols.map(function (c, i) {
      return make("th", { text: cellText(c) || "(no name)", cls: numericCol[i] ? "num" : "", attrs: { scope: "col" } });
    }))]);
    var tbody = make("tbody");
    rows.forEach(function (r) {
      var tr = make("tr");
      cols.forEach(function (_, i) {
        var v = arr(r)[i];
        var t = cellText(v);
        var td = make("td", { text: t == null ? "blank" : t, cls: t == null ? "blank" : (numericCol[i] ? "num" : "") });
        if (t != null && t.length > 28) td.title = t;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    el.preview.appendChild(thead);
    el.preview.appendChild(tbody);
  }

  /* ---------- chart ---------- */

  function renderMetricSelect(series) {
    clear(el.metric);
    var usable = series.filter(function (s) { return s && arr(s.points).some(function (p) { return p && isNum(p.value); }); });
    usable.forEach(function (s, i) {
      el.metric.appendChild(make("option", { text: cellText(s.column) || "Column " + (i + 1), attrs: { value: String(i) } }));
    });
    el.metric.usable = usable;
    el.metricField.hidden = usable.length < 2;
    drawChart();
  }

  function niceTicks(min, max, count) {
    if (min === max) { min -= 1; max += 1; }
    var span = max - min;
    var step = Math.pow(10, Math.floor(Math.log10(span / count)));
    var err = span / count / step;
    if (err >= 7.5) step *= 10; else if (err >= 3.5) step *= 5; else if (err >= 1.5) step *= 2;
    var lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
    var ticks = [];
    for (var v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v / step) * step);
    return ticks;
  }

  function shorten(text, n) { return text.length > n ? text.slice(0, n - 1) + "…" : text; }

  function directionWords(dir) {
    if (dir === "up") return "rising";
    if (dir === "down") return "falling";
    if (dir === "flat") return "roughly flat";
    if (dir === "insufficient") return "not enough values to judge";
    return null;
  }

  function drawChart() {
    clear(el.chartArea);
    var usable = el.metric.usable || [];
    if (!usable.length) {
      el.chartArea.appendChild(make("p", { cls: "hint", text: "No numeric column with enough values to chart. Text, date and true/false columns aren't charted." }));
      return;
    }
    var s = usable[Number(el.metric.value) || 0];
    var name = cellText(s.column) || "this column";
    var points = arr(s.points).filter(function (p) { return p && isNum(p.value); });
    var ext = arr(s.forecast).filter(function (p) { return p && isNum(p.value); });

    var width = Math.max(300, Math.floor(el.chartArea.clientWidth || 640) - 18);
    var height = width < 520 ? 220 : 300;
    var pad = { top: 16, right: 16, bottom: 34, left: 56 };
    var all = points.concat(ext);
    var values = all.map(function (p) { return p.value; });
    var ticks = niceTicks(Math.min.apply(null, values), Math.max.apply(null, values), width < 520 ? 3 : 5);
    var yMin = ticks[0], yMax = ticks[ticks.length - 1];
    var plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
    var n = all.length;
    var x = function (i) { return pad.left + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW); };
    var y = function (v) { return pad.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH; };

    var root = svg("svg", { viewBox: "0 0 " + width + " " + height, width: width, height: height, role: "img", "aria-labelledby": "chart-svg-title chart-svg-desc" });
    root.appendChild(svg("title", { id: "chart-svg-title" }, "Line chart of " + name));
    var dirWords = directionWords(s.direction);
    var total = isNum(s.observations) && s.observations > points.length ? s.observations : null;
    var countText = total
      ? "Showing " + fmtInt(points.length) + " evenly spaced of " + plural(total, "value", "values")
      : plural(points.length, "value", "values");
    root.appendChild(svg("desc", { id: "chart-svg-desc" },
      countText + (s.date_column ? " ordered by " + cellText(s.date_column) : " in file order") +
      (dirWords ? ", " + dirWords + " overall" : "") + ". A text table of the values follows the chart."));

    ticks.forEach(function (t) {
      var ty = y(t);
      root.appendChild(svg("line", { x1: pad.left, x2: width - pad.right, y1: ty, y2: ty, class: "grid" }));
      root.appendChild(svg("text", { x: pad.left - 8, y: ty + 4, "text-anchor": "end", class: "tick" }, fmtNum(t)));
    });
    root.appendChild(svg("line", { x1: pad.left, x2: width - pad.right, y1: pad.top + plotH, y2: pad.top + plotH, class: "axis" }));

    // X labels: first, last, and a middle one when there is room. Extension labels are skipped.
    var labelIdx = points.length > 2 && width >= 520 ? [0, Math.floor((points.length - 1) / 2), points.length - 1] : [0, points.length - 1];
    labelIdx.filter(function (v, i, a) { return a.indexOf(v) === i; }).forEach(function (i, k, list) {
      var anchor = list.length > 1 && k === 0 ? "start" : (k === list.length - 1 && list.length > 1 ? "end" : "middle");
      if (n > points.length && k === list.length - 1) anchor = "middle";
      root.appendChild(svg("text", { x: x(i), y: height - 10, "text-anchor": anchor, class: "tick" },
        shorten(cellText(points[i].label) || "#" + (i + 1), width < 520 ? 10 : 14)));
    });

    var line = points.map(function (p, i) { return x(i).toFixed(1) + "," + y(p.value).toFixed(1); }).join(" ");
    root.appendChild(svg("polyline", { points: line, class: "series" }));
    if (points.length <= 60) {
      points.forEach(function (p, i) {
        var dot = svg("circle", { cx: x(i), cy: y(p.value), r: 3, class: "dot" });
        dot.appendChild(svg("title", null, (cellText(p.label) || "#" + (i + 1)) + ": " + fmtNum(p.value)));
        root.appendChild(dot);
      });
    }
    if (ext.length && points.length) {
      var last = points.length - 1;
      var extLine = [x(last).toFixed(1) + "," + y(points[last].value).toFixed(1)].concat(
        ext.map(function (p, j) { return x(points.length + j).toFixed(1) + "," + y(p.value).toFixed(1); })).join(" ");
      root.appendChild(svg("polyline", { points: extLine, class: "ext" }));
      ext.forEach(function (p, j) {
        var c = svg("circle", { cx: x(points.length + j), cy: y(p.value), r: 3.5, class: "ext-dot" });
        c.appendChild(svg("title", null, (cellText(p.label) || "Extension") + ": " + fmtNum(p.value)));
        root.appendChild(c);
      });
    }

    var figure = make("figure", { cls: "chart" }, [root]);
    el.chartArea.appendChild(figure);

    var legend = make("p", { cls: "legend" }, [make("span", { text: name })]);
    if (ext.length) legend.appendChild(make("span", { cls: "ext", text: "Straight-line extension" }));
    el.chartArea.appendChild(legend);

    var caption = make("p", { cls: "chart-caption" });
    var parts = [];
    parts.push(countText +
      (s.date_column ? " ordered by " + cellText(s.date_column) : " in file order") + ".");
    if (dirWords) parts.push("Overall direction: " + dirWords + (isNum(s.slope) ? " (about " + fmtNum(s.slope) + " per step)." : "."));
    caption.appendChild(document.createTextNode(parts.join(" ")));
    el.chartArea.appendChild(caption);
    var note = cellText(s.note) || (ext.length ? "The dashed line extends the straight-line trend. It is not a reliable prediction." : null);
    if (note) el.chartArea.appendChild(make("p", { cls: "chart-caption", text: note }));

    // Text equivalent of the chart.
    var tbody = make("tbody");
    points.forEach(function (p, i) {
      tbody.appendChild(make("tr", null, [make("th", { text: cellText(p.label) || "#" + (i + 1), attrs: { scope: "row" } }), make("td", { text: fmtNum(p.value), cls: "num" }), make("td", { text: "Observed" })]));
    });
    ext.forEach(function (p) {
      tbody.appendChild(make("tr", null, [make("th", { text: cellText(p.label) || "Extension", attrs: { scope: "row" } }), make("td", { text: fmtNum(p.value), cls: "num" }), make("td", { text: "Straight-line extension" })]));
    });
    var table = make("table", { cls: "data" }, [
      make("thead", null, [make("tr", null, [
        make("th", { text: s.date_column ? cellText(s.date_column) : "Position", attrs: { scope: "col" } }),
        make("th", { text: name, cls: "num", attrs: { scope: "col" } }),
        make("th", { text: "Kind", attrs: { scope: "col" } })
      ])]),
      tbody
    ]);
    el.chartArea.appendChild(make("details", { cls: "points" }, [
      make("summary", { text: "Show the charted values as a table" }),
      make("div", { cls: "scroller", attrs: { tabindex: "0", role: "region", "aria-label": "Charted values for " + name } }, [table])
    ]));
  }

  /* ---------- summary download ---------- */

  function buildSummary(data, includeName) {
    var out = {
      generated_by: "Table Notes" + (state.config && state.config.version ? " " + state.config.version : ""),
      contents: "Column names, counts, statistics, notes and each numeric column's trend direction and slope. Minimum and maximum are actual values from the table. Preview rows, chart points and extension values are left out.",
      is_example: !!data.is_example,
      shape: data.shape || null,
      quality: data.quality || null,
      columns: arr(data.columns).map(function (c) {
        return { name: c.name, kind: c.kind, missing: c.missing, unique: c.unique, stats: c.stats || null };
      }),
      series: arr(data.series).map(function (s) {
        return {
          column: s.column, date_column: s.date_column || null,
          point_count: isNum(s.observations) ? s.observations : arr(s.points).length, direction: s.direction || null,
          slope: isNum(s.slope) ? s.slope : null, note: s.note || null
        };
      }),
      notes: arr(data.narrative).map(function (n) { return { title: n && n.title, body: n && n.body }; })
    };
    if (includeName && data.filename) out.filename = data.filename;
    return out;
  }

  function downloadSummary() {
    if (!state.result) return;
    var json = JSON.stringify(buildSummary(state.result, el.includeName.checked), null, 2);
    var url = URL.createObjectURL(new Blob([json], { type: "application/json" }));
    var a = make("a", { attrs: { href: url, download: "table-notes-summary.json" } });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    setStatus("Summary saved as table-notes-summary.json.", false);
  }

  /* ---------- wiring ---------- */

  el.example.addEventListener("click", runExample);
  el.reset.addEventListener("click", function () { resetAll(); el.example.focus(); });
  el.download.addEventListener("click", downloadSummary);
  el.metric.addEventListener("change", drawChart);

  el.fileInput.addEventListener("change", function () {
    var files = el.fileInput.files;
    if (files && files.length) runFile(files[0]);
  });

  ["dragenter", "dragover"].forEach(function (type) {
    el.drop.addEventListener(type, function (e) {
      if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") < 0) return;
      e.preventDefault();
      el.drop.classList.add("is-over");
    });
  });
  ["dragleave", "drop"].forEach(function (type) {
    el.drop.addEventListener(type, function () { el.drop.classList.remove("is-over"); });
  });
  el.drop.addEventListener("drop", function (e) {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    if (e.dataTransfer.files.length > 1) { showFileError("Drop one file at a time."); return; }
    el.fileInput.value = "";
    runFile(e.dataTransfer.files[0]);
  });
  // Dropping a file elsewhere on the page shouldn't navigate away from it.
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("drop", function (e) { e.preventDefault(); });

  var resizeTimer = null;
  var lastWidth = el.chartArea.clientWidth;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (state.result && el.chartArea.clientWidth !== lastWidth) {
        lastWidth = el.chartArea.clientWidth;
        var open = el.chartArea.querySelector("details[open]");
        drawChart();
        if (open) { var d = el.chartArea.querySelector("details"); if (d) d.open = true; }
      }
    }, 150);
  });

  loadConfig();
})();
