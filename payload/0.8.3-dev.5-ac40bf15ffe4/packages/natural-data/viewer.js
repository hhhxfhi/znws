(() => {
  // plugin/packages/natural-data/public-path.js
  function normalizeBasePath(value = "") {
    if (typeof value !== "string") throw new Error("publicBasePath \u5FC5\u987B\u662F\u8DEF\u5F84\u5B57\u7B26\u4E32\u3002");
    if (value.startsWith("//")) throw new Error("publicBasePath \u4E0D\u80FD\u4F7F\u7528\u8DE8\u57DF\u6216\u91CD\u590D\u659C\u6760\u8DEF\u5F84\u3002");
    const path = value.replace(/\/+$/, "");
    if (!path) return "";
    if (!path.startsWith("/") || path.startsWith("//") || /[\\?#\s\u0000-\u001f]/.test(path)) {
      throw new Error("publicBasePath \u5FC5\u987B\u662F\u540C\u57DF\u8DEF\u5F84\uFF0C\u4F8B\u5982 /sqlbot\uFF1B\u4E0D\u80FD\u586B\u5199\u57DF\u540D\u3001\u67E5\u8BE2\u53C2\u6570\u6216\u53CD\u659C\u6760\u3002");
    }
    for (const segment of path.slice(1).split("/")) {
      let decoded;
      try {
        decoded = decodeURIComponent(segment);
      } catch {
        throw new Error("publicBasePath \u7F16\u7801\u65E0\u6548\u3002");
      }
      if (!decoded || decoded === "." || decoded === ".." || /[\/\\?#\u0000-\u001f]/.test(decoded)) {
        throw new Error("publicBasePath \u5305\u542B\u65E0\u6548\u8DEF\u5F84\u6BB5\u3002");
      }
    }
    return new URL(path, "http://localhost").pathname;
  }
  function createPublicUrls(scope = globalThis) {
    const page = new URL(scope.location?.href || (scope.location?.origin || "http://localhost") + "/");
    const doc = scope.document;
    const sameOrigin = (value) => {
      try {
        const url = new URL(value, page);
        return url.origin === page.origin && !url.username && !url.password ? url : null;
      } catch {
        return null;
      }
    };
    function discover() {
      if (scope.__NATURAL_DATA_BASE_PATH__ !== void 0) return normalizeBasePath(scope.__NATURAL_DATA_BASE_PATH__);
      const meta = doc?.querySelector?.('meta[name="natural-data-base-path"]');
      if (meta) return normalizeBasePath(meta.content);
      const viewer = page.pathname.lastIndexOf("/natural-data/");
      if (viewer >= 0) return normalizeBasePath(page.pathname.slice(0, viewer));
      const scriptBase = (src) => {
        const url = sameOrigin(src);
        const marker = url?.pathname.match(/\/plugins(?:\/|$)/);
        return marker ? normalizeBasePath(url.pathname.slice(0, marker.index)) : null;
      };
      if (doc?.currentScript?.src) {
        const base = scriptBase(doc.currentScript.src);
        if (base !== null) return base;
      }
      const baseTag = doc?.querySelector?.("base[href]");
      if (baseTag) {
        const base = sameOrigin(baseTag.href);
        if (base) return normalizeBasePath(new URL(".", base).pathname);
      }
      for (const script of Array.from(doc?.scripts || []).reverse()) {
        if (!script.src) continue;
        const base = scriptBase(script.src);
        if (base !== null) return base;
      }
      const path = /\.[^/]+$/.test(page.pathname) ? new URL(".", page).pathname : page.pathname;
      return normalizeBasePath(path);
    }
    const basePath = discover();
    const route = (path) => {
      if (!/^\/natural-data(?:\/|\?|$)/.test(path)) throw new Error("\u65E0\u6548\u7684\u95EE\u6570\u8D44\u6E90\u8DEF\u5F84\u3002");
      return basePath + path;
    };
    const result2 = (value, kind = "view") => {
      if (typeof value !== "string" || !value) return null;
      const url = sameOrigin(value);
      const path = kind === "export" ? "/natural-data/api/export" : "/natural-data/view";
      if (!url || !url.pathname.endsWith(path)) return null;
      return route(path + url.search + url.hash);
    };
    return { basePath, route, result: result2 };
  }

  // plugin/packages/natural-data/viewer-entry.js
  var publicUrls = createPublicUrls(globalThis);
  var $ = (id) => document.getElementById(id);
  var params = new URLSearchParams(location.search);
  var project = params.get("project") || "";
  var current = params.get("result_ref") || "";
  var result;
  var el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text !== void 0) n.textContent = String(text);
    if (cls) n.className = cls;
    return n;
  };
  function error(e) {
    $("error").hidden = false;
    $("error").textContent = e.message || e;
  }
  async function api(op, args = {}, write = false) {
    const data = { project, ...args };
    const url = publicUrls.route("/natural-data/api/") + op;
    const r = await fetch(write ? url : url + "?" + new URLSearchParams(data), write ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(data) } : {});
    const v = await r.json();
    if (!r.ok) throw new Error(v.error || v.detail || "\u670D\u52A1\u4E0D\u53EF\u7528");
    return v;
  }
  function table(columns, rows) {
    const wrap = el("div", void 0, "table-wrap"), t = el("table"), head = el("tr");
    for (const c of columns) head.append(el("th", result?.column_labels?.[c] || c));
    t.append(head);
    for (const row of rows) {
      const tr = el("tr");
      for (const c of columns) {
        const td = el("td", row[c] ?? "");
        td.title = String(row[c] ?? "");
        tr.append(td);
      }
      t.append(tr);
    }
    wrap.append(t);
    return wrap;
  }
  function svgNode(tag, attrs = {}) {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
    return n;
  }
  function draw(rows, columns, kind) {
    const nums = columns.filter((c) => rows.some((r) => r[c] !== null && r[c] !== "" && Number.isFinite(Number(r[c]))) && rows.every((r) => r[c] === null || r[c] === "" || Number.isFinite(Number(r[c]))));
    const labels = columns.find((c) => !nums.includes(c)) || columns[0], metric = nums.find((c) => c !== labels) || nums[0];
    if (!metric) return el("p", "\u5F53\u524D\u7ED3\u679C\u6CA1\u6709\u53EF\u7ED8\u5236\u7684\u6570\u503C\u5B57\u6BB5\uFF0C\u8BF7\u67E5\u770B\u8868\u683C\u3002");
    const data = rows.slice(0, 40), svg = svgNode("svg", { viewBox: "0 0 760 320", role: "img", "aria-label": kind + " \u6570\u636E\u56FE\u8868" });
    const values = data.map((r) => Number(r[metric]) || 0), min = Math.min(0, ...values), max = Math.max(1, ...values), scale = (v) => 260 - (v - min) / (max - min) * 210;
    const title = svgNode("text", { x: 35, y: 20, fill: "#57655d", "font-size": 12 });
    title.textContent = (result.column_labels?.[metric] || metric) + (rows.length > 40 ? "\uFF08\u524D 40 \u9879\uFF09" : "");
    svg.append(title);
    if (kind === "pie" || kind === "donut") {
      if (values.some((v) => v < 0)) return el("p", "\u542B\u8D1F\u6570\u7684\u6570\u636E\u4E0D\u9002\u5408\u997C\u56FE\uFF0C\u8BF7\u5207\u6362\u67F1\u72B6\u56FE\u6216\u8868\u683C\u3002");
      const total = values.reduce((a, b) => a + b, 0);
      if (!total) return el("p", "\u6570\u636E\u5408\u8BA1\u4E3A 0\uFF0C\u65E0\u6CD5\u7ED8\u5236\u5360\u6BD4\u3002");
      let angle = -Math.PI / 2;
      data.forEach((r, i) => {
        const next = angle + values[i] / total * 2 * Math.PI, fill = ["#497b65", "#8caf97", "#c7d5b2", "#ccad77", "#93aabd", "#b797a0"][i % 6];
        const slice = values[i] === total ? svgNode("circle", { cx: 220, cy: 160, r: 110, fill }) : svgNode("path", { d: `M220,160 L${220 + 110 * Math.cos(angle)},${160 + 110 * Math.sin(angle)} A110,110 0 ${next - angle > Math.PI ? 1 : 0},1 ${220 + 110 * Math.cos(next)},${160 + 110 * Math.sin(next)} Z`, fill });
        svg.append(slice);
        angle = next;
        if (i < 10) {
          const text = svgNode("text", { x: 390, y: 45 + i * 24, fill, "font-size": 12 });
          text.textContent = String(r[labels]) + " \xB7 " + values[i];
          svg.append(text);
        }
      });
      if (kind === "donut") svg.append(svgNode("circle", { cx: 220, cy: 160, r: 60, fill: "white" }));
      return svg;
    }
    const points = [];
    data.forEach((r, i) => {
      const x = 55 + (i + 0.5) * 660 / data.length, y = scale(values[i]);
      points.push(`${x},${y}`);
      const shape = ["line", "area", "scatter"].includes(kind) ? svgNode("circle", { cx: x, cy: y, r: 4, fill: "#497b65" }) : svgNode("rect", { x: x - 220 / data.length, y: Math.min(y, scale(0)), width: 440 / data.length, height: Math.max(1, Math.abs(scale(0) - y)), rx: 2, fill: "#497b65" });
      const tip = svgNode("title");
      tip.textContent = String(r[labels]) + ": " + values[i];
      shape.append(tip);
      svg.append(shape);
      if (data.length <= 14 || i % Math.ceil(data.length / 12) === 0) {
        const text = svgNode("text", { x, y: 285, "text-anchor": "middle", "font-size": 10, fill: "#717b74" });
        text.textContent = String(r[labels]).slice(0, 12);
        svg.append(text);
      }
    });
    if (["line", "area"].includes(kind)) svg.append(svgNode("polyline", { points: points.join(" "), fill: "none", stroke: "#497b65", "stroke-width": 2 }));
    return svg;
  }
  function pivot(r) {
    const numeric = r.columns.filter((c) => r.rows.some((x) => typeof x[c] === "number"));
    const dims = r.columns.filter((c) => !numeric.includes(c));
    if (dims.length < 2 || !numeric.length) return table(r.columns, r.rows);
    const [a, b] = dims, v = numeric[0], cols = [...new Set(r.rows.map((x) => String(x[b])))], map = /* @__PURE__ */ new Map();
    for (const row of r.rows) {
      const key = String(row[a]);
      if (!map.has(key)) map.set(key, { [a]: key });
      const target = map.get(key);
      target[String(row[b])] = (target[String(row[b])] || 0) + Number(row[v] || 0);
    }
    return table([a, ...cols], Array.from(map.values()));
  }
  async function show(id) {
    current = id;
    result = await api("result", { result_ref: id });
    const a = $("result");
    a.replaceChildren();
    a.append(el("h2", result.title));
    a.append(el("span", result.status, "badge"));
    a.append(el("p", `${result.row_count || 0} \u884C${result.truncated ? " \xB7 \u7ED3\u679C\u5DF2\u622A\u65AD\uFF0C\u8BF7\u7F29\u5C0F\u8303\u56F4\u540E\u91CD\u67E5" : ""}`, "muted"));
    if (result.status !== "succeeded") {
      a.append(el("p", result.error || "\u67E5\u8BE2\u672A\u5B8C\u6210"));
      return;
    }
    const toolbar = el("div", void 0, "toolbar"), select = el("select");
    for (const [value, label] of Object.entries({ table: "\u8868\u683C", bar: "\u67F1\u72B6\u56FE", line: "\u6298\u7EBF\u56FE", pie: "\u997C\u56FE", donut: "\u73AF\u5F62\u56FE", scatter: "\u6563\u70B9\u56FE", "pivot-table": "\u900F\u89C6\u8868", map: "\u5730\u56FE" })) {
      const o = el("option", label);
      o.value = value;
      select.append(o);
    }
    select.value = result.view || "table";
    select.onchange = async () => {
      try {
        await api("visualize", { result_ref: id, kind: select.value }, true);
        await show(id);
      } catch (e) {
        error(e);
      }
    };
    toolbar.append(select);
    a.append(toolbar);
    if (select.value === "pivot-table") a.append(pivot(result));
    else if (select.value === "map") {
      a.append(el("p", "\u5730\u56FE\u9700\u8981\u8FD4\u56DE\u7ECF\u7EAC\u5EA6\u6216 GeoJSON \u5B57\u6BB5\uFF1B\u5B8C\u6574\u7A7A\u95F4\u6E32\u67D3\u7531\u5730\u56FE\u7EC4\u4EF6\u63D0\u4F9B\u3002"));
      a.append(table(result.columns, result.rows));
    } else {
      if (select.value !== "table") a.append(draw(result.rows, result.columns, select.value));
      a.append(table(result.columns, result.rows));
    }
    const d = el("details"), summary = el("summary", "\u67E5\u8BE2\u4F9D\u636E\u4E0E\u6267\u884C\u51ED\u8BC1");
    d.append(summary, el("pre", result.sql + "\n\n" + JSON.stringify(result.receipt, null, 2)));
    a.append(d);
  }
  async function refresh() {
    if (!project) return;
    const list = await api("results");
    $("history").replaceChildren();
    for (const r of list) {
      const b = el("button", r.title + (r.status === "succeeded" ? "" : " \xB7 " + r.status), current === r.result_ref ? "active" : "");
      b.onclick = () => show(r.result_ref).catch(error);
      $("history").append(b);
    }
    if (current) await show(current);
  }
  async function init() {
    if (current) document.body.classList.add("compact");
    if (params.has("settings")) {
      document.querySelector(".workspace").hidden = true;
      document.querySelector("nav").hidden = true;
      document.querySelector("header").hidden = true;
      $("configuration").hidden = false;
    }
    const cfg = await api("config");
    const projects = Object.keys(cfg.projects);
    if (project && !projects.includes(project)) projects.push(project);
    for (const p of projects) {
      const o = el("option", p.split("/").pop() || p);
      o.value = p;
      o.title = p;
      $("project").append(o);
    }
    project = project || projects[0] || "";
    $("project").value = project;
    $("project-path").value = project;
    for (const s of cfg.sources) {
      const label = el("label"), check = el("input");
      check.type = "checkbox";
      check.value = s.id;
      check.checked = (cfg.projects[project] || []).includes(s.id);
      label.append(check, document.createTextNode(s.name + " \xB7 " + s.dialect));
      $("source-options").append(label);
    }
    $("project").onchange = () => {
      project = $("project").value;
      current = "";
      refresh().catch(error);
    };
    $("refresh").onclick = () => refresh().catch(error);
    $("settings").onclick = () => {
      $("configuration").hidden = !$("configuration").hidden;
    };
    $("save-binding").onclick = async () => {
      try {
        project = $("project-path").value.trim();
        await api("bind", { source_ids: [...$("source-options").querySelectorAll("input:checked")].map((n) => n.value) }, true);
        $("save-message").textContent = " \u5DF2\u4FDD\u5B58\u3002\u5173\u95ED\u7A97\u53E3\u540E\u7EE7\u7EED\u5F53\u524D\u5BF9\u8BDD\u5373\u53EF\u3002";
      } catch (e) {
        error(e);
      }
    };
    await refresh();
  }
  init().catch(error);
})();
