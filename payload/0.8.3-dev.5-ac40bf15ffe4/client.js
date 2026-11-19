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
    const result = (value, kind = "view") => {
      if (typeof value !== "string" || !value) return null;
      const url = sameOrigin(value);
      const path = kind === "export" ? "/natural-data/api/export" : "/natural-data/view";
      if (!url || !url.pathname.endsWith(path)) return null;
      return route(path + url.search + url.hash);
    };
    return { basePath, route, result };
  }

  // plugin/packages/natural-data/product-settings.js
  function createProductSettings({ React, h, Input, Button, Checkbox, SettingRow, Detail, SettingsMenu, IconChevronDownOutlineMedium, request }) {
    function Choice({ label, value, options, onChange, disabled }) {
      const [open, setOpen] = React.useState(false);
      return h("label", { className: "nw-field" }, h("span", null, label), h(SettingsMenu, { open, onClose: () => setOpen(false), selectedId: value, items: options, onSelect: (id) => {
        onChange(id);
        setOpen(false);
      }, anchor: h(Button, { variant: "outline", size: "sm", disabled, "aria-label": label, onClick: () => setOpen((v) => !v) }, options.find((x) => x.id === value)?.label || "\u8BF7\u9009\u62E9", h(IconChevronDownOutlineMedium, { size: 16 })) }));
    }
    function Field({ label, value, onChange, disabled, ...props }) {
      return h("label", { className: "nw-field" }, h("span", null, label), h(Input, { value, disabled, "aria-label": label, onChange: (e) => onChange(e.target.value), ...props }));
    }
    function OnboardingPanel({ cfg, current, busy, dirty, onNavigate }) {
      const status = current?.readiness, ready = status?.basic_query.state === "ready";
      return h(
        Detail,
        { title: "\u63A5\u5165\u68C0\u67E5\u4E0E\u4F7F\u7528\u5F15\u5BFC \xB7 " + (cfg.edition ? cfg.edition + " " : "") + (cfg.plugin_version || "0.5.1"), initialOpen: !ready },
        h(SettingRow, { title: ready ? "\u57FA\u7840\u95EE\u6570\u5DF2\u5C31\u7EEA" : "\u5B8C\u6210\u4EE5\u4E0B\u6B65\u9AA4\u5373\u53EF\u57FA\u7840\u95EE\u6570", description: ready ? "\u5F53\u524D\u5DF2\u53D1\u5E03\u7684\u6570\u636E\u8303\u56F4\u53EF\u4EE5\u76F4\u63A5\u63D0\u95EE\uFF0C\u65E0\u9700\u9884\u8BBE\u6307\u6807\u3002\u4E1A\u52A1\u5B9A\u4E49\u4E0D\u660E\u786E\u65F6\u4F1A\u5148\u6F84\u6E05\u3002" : "\u6570\u636E\u5E93\u8FDE\u63A5\u3001\u9879\u76EE\u6388\u6743\u548C\u53D1\u5E03\u5206\u522B\u68C0\u67E5\uFF1B\u4E1A\u52A1\u6307\u6807\u53EF\u4EE5\u7A0D\u540E\u6309\u9700\u914D\u7F6E\u3002" }),
        h(SettingRow, { title: "1. \u63A5\u5165\u6570\u636E\u5E93", description: cfg.sources.length ? "\u5DF2\u767B\u8BB0 " + cfg.sources.length + " \u4E2A\u53EF\u89C1\u6765\u6E90\u3002" : "\u5C1A\u672A\u914D\u7F6E\u53EF\u89C1\u7684\u6570\u636E\u6E90\u3002" }, cfg.system_admin && h(Button, { size: "sm", variant: "outline", disabled: busy || dirty, onClick: () => onNavigate("connections") }, "\u7BA1\u7406\u6570\u636E\u5E93")),
        h(SettingRow, { title: "2. \u6388\u6743\u5E76\u53D1\u5E03\u4E1A\u52A1\u9879\u76EE", description: current ? current.name + " \xB7 " + (current.published_version ? "\u751F\u6548\u7248\u672C v" + current.published_version : "\u5C1A\u672A\u53D1\u5E03") : "\u5148\u5728\u4E0B\u65B9\u521B\u5EFA\u4E1A\u52A1\u9879\u76EE\uFF0C\u518D\u9009\u62E9\u6570\u636E\u6E90\u548C\u6388\u6743\u8868\u3002" }, current && h(Button, { size: "sm", variant: "outline", disabled: busy || dirty, onClick: () => onNavigate("sources") }, "\u67E5\u770B\u6570\u636E\u8303\u56F4")),
        h(SettingRow, { title: "3. \u65B0\u5EFA\u4F1A\u8BDD\u5F00\u59CB\u63D0\u95EE", description: cfg.default_project ? "\u65B0\u4F1A\u8BDD\u4F7F\u7528\u5DF2\u5173\u8054\u6216\u9ED8\u8BA4\u9879\u76EE\uFF1B\u5DF2\u6267\u884C\u4F1A\u8BDD\u7EE7\u7EED\u4F7F\u7528\u539F\u9879\u76EE\u3002" : "\u53EA\u6709\u4E00\u4E2A\u5DF2\u6388\u6743\u53D1\u5E03\u9879\u76EE\u65F6\u81EA\u52A8\u7528\u4E8E\u65B0\u4F1A\u8BDD\uFF1B\u591A\u4E2A\u9879\u76EE\u8BF7\u5148\u8BBE\u7F6E\u9ED8\u8BA4\u9879\u76EE\u3002" }),
        (status?.issues || []).map((issue, i) => h(SettingRow, { key: i, title: issue.blocking ? "\u5F85\u5B8C\u6210" : "\u63D0\u793A", description: issue.message }, h(Button, { size: "sm", variant: "outline", disabled: busy || dirty || !current, onClick: () => onNavigate(issue.action) }, issue.action === "business" ? "\u914D\u7F6E\u4E1A\u52A1\u53E3\u5F84" : "\u67E5\u770B\u5E76\u5904\u7406"))),
        (status?.sources || []).flatMap((s) => s.unavailable_metrics.map((m, i) => h(SettingRow, { key: s.id + ":" + i, title: (m.name || m.id) + "\u6682\u4E0D\u53EF\u7528", description: m.reason }, h(Button, { size: "sm", variant: "outline", disabled: busy || dirty, onClick: () => onNavigate("business") }, "\u4FEE\u590D\u6307\u6807")))),
        ready && (status.sources || []).flatMap((s) => s.starter_questions.map((q, i) => h("p", { key: s.id + ":q" + i, className: "nw-setting-description" }, "\u53EF\u5148\u63D0\u95EE\uFF1A" + q.question))),
        cfg.environment && h(Detail, { title: "\u8FD0\u884C\u73AF\u5883\u68C0\u67E5" }, h("p", { className: "nw-setting-description" }, cfg.environment.model), cfg.environment.drivers.map((d) => h(SettingRow, { key: d.id, title: d.name, description: d.available ? "\u9A71\u52A8\u5DF2\u5C31\u7EEA" : "\u7F3A\u5C11\u9A71\u52A8\uFF0C\u8BF7\u7BA1\u7406\u5458\u5B89\u88C5\u63D2\u4EF6\u8FD0\u884C\u4F9D\u8D56\u3002" })))
      );
    }
    const emptyOperand = () => ({ id: "value", table: "", column: "", aggregation: "sum", time_column: "", entity_columns: [], filters: [] });
    function MetricBuilder({ project, draft, setDraft, sources, disabled, initial, onClose }) {
      const [form, setForm] = React.useState(() => {
        const row = initial, spec = row?.config;
        return { name: row?.name || "", definition: row?.definition || "", id: spec?.id || "", version: spec?.version || "1.0.0", unit: spec?.unit || "", source_id: spec?.source_id || draft.source_ids[0] || "", time_mode: spec?.time_mode || "period", coverage: (spec?.coverage || []).join(", "), formula: spec?.formula || "value", operands: spec?.operands?.map((o) => ({ ...o, column: o.column || "", time_column: o.time_column || "" })) || [emptyOperand()], dimensions: spec?.dimensions || [], relations: spec?.relations || [], additive: spec?.additive || false };
      });
      const [error, setError] = React.useState("");
      const [observed, setObserved] = React.useState(null), [checking, setChecking] = React.useState(false);
      const mounted = React.useRef(true);
      React.useEffect(() => () => {
        mounted.current = false;
      }, []);
      const source = sources.find((s) => s.id === form.source_id), tables = (source?.tables || []).filter((t) => (draft.tables[form.source_id] || []).includes(t.name));
      const change = (key, value) => setForm((f) => ({ ...f, [key]: value }));
      const operand = (i, key, value) => setForm((f) => ({ ...f, operands: f.operands.map((o, j) => j === i ? { ...o, [key]: value, ...key === "table" ? { column: "", time_column: "", entity_columns: [] } : {} } : o) }));
      async function inspectTime() {
        setChecking(true);
        setError("");
        setObserved(null);
        try {
          const value = await request("inspect_periods", { business_project: project, source_id: form.source_id, table: form.operands[0].table, column: form.operands[0].time_column });
          if (mounted.current) setObserved(value);
        } catch (e) {
          if (mounted.current) setError(e.message);
        } finally {
          if (mounted.current) setChecking(false);
        }
      }
      function save() {
        try {
          if (!form.name.trim() || !form.definition.trim() || !form.unit.trim() || !/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(form.id)) throw Error("\u8BF7\u586B\u5199\u540D\u79F0\u3001\u4E1A\u52A1\u5B9A\u4E49\u3001\u5355\u4F4D\u548C\u6709\u6548\u7684\u6307\u6807\u6807\u8BC6\uFF08\u5B57\u6BCD\u5F00\u5934\uFF0C\u53EF\u542B\u6570\u5B57\u548C\u4E0B\u5212\u7EBF\uFF09\u3002");
          if (!/^\d+\.\d+\.\d+$/.test(form.version)) throw Error("\u6307\u6807\u7248\u672C\u8BF7\u4F7F\u7528\u4E09\u4E2A\u6570\u5B57\uFF0C\u4F8B\u5982 1.0.0\u3002");
          const coverage = form.time_mode === "all" ? [] : form.coverage.split(/[,，\s]+/).filter(Boolean);
          if (form.time_mode === "period" && (!coverage.length || coverage.some((p) => !/^20\d{2}(-(?:0[1-9]|1[0-2]))?$/.test(p)))) throw Error("\u8BF7\u586B\u5199\u5DF2\u786E\u8BA4\u7684\u671F\u95F4\uFF0C\u4F8B\u5982 2026 \u6216 2026-10\uFF0C\u4EE5\u9017\u53F7\u5206\u9694\uFF1B\u5B8C\u6574\u5E74\u5EA6\u9700\u4E1A\u52A1\u786E\u8BA4\u3002");
          const operands = form.operands.map((o) => {
            const table2 = tables.find((t) => t.name === o.table);
            if (!table2 || !o.id || !o.column && o.aggregation !== "count" || form.time_mode === "period" && !o.time_column) throw Error("\u8BF7\u4E3A\u6BCF\u4E2A\u64CD\u4F5C\u6570\u9009\u62E9\u6388\u6743\u8868\u3001\u5B57\u6BB5\u548C\u6240\u9700\u65F6\u95F4\u5B57\u6BB5\u3002");
            return { ...o, column: o.column || null, time_column: form.time_mode === "all" ? null : o.time_column };
          });
          const spec = { kind: "semantic-metric", id: form.id, version: form.version, source_id: form.source_id, source_revision: source?.target_revision || 0, unit: form.unit.trim(), definition: form.definition.trim(), time_mode: form.time_mode, coverage, operands, formula: form.formula, dimensions: form.dimensions, relations: form.relations, additive: form.additive };
          const row = { id: initial?.id || crypto.randomUUID(), name: form.name.trim(), definition: form.definition.trim(), config: spec };
          setDraft((d) => ({ ...d, terms: initial ? d.terms.map((t) => t.id === initial.id ? row : t) : [...d.terms, row] }));
          onClose();
        } catch (e) {
          setError(e.message);
        }
      }
      return h(
        "div",
        { className: "nw-business-entry" },
        h("p", { className: "nw-setting-description" }, "\u914D\u7F6E\u5B8C\u6210\u540E\u6DFB\u52A0\u5230\u8349\u7A3F\uFF0C\u518D\u4FDD\u5B58\u5E76\u6821\u9A8C\u53D1\u5E03\u3002\u5B57\u6BB5\u5019\u9009\u6765\u81EA\u6388\u6743\u76EE\u5F55\uFF0C\u4E0D\u81EA\u52A8\u8BA4\u5B9A\u4E1A\u52A1\u53E3\u5F84\uFF1B\u7ED3\u6784\u53D8\u5316\u540E\u53EF\u91CD\u65B0\u9009\u62E9\u5B57\u6BB5\u4FEE\u590D\u3002"),
        h(Field, { label: "\u6307\u6807\u540D\u79F0", value: form.name, disabled, onChange: (v) => change("name", v) }),
        h(Field, { label: "\u4E1A\u52A1\u5B9A\u4E49\u4E0E\u7EDF\u8BA1\u8303\u56F4", value: form.definition, disabled, onChange: (v) => change("definition", v) }),
        h(Field, { label: "\u6307\u6807\u6807\u8BC6", value: form.id, disabled, onChange: (v) => change("id", v), placeholder: "\u4F8B\u5982 total_amount" }),
        h(Field, { label: "\u6307\u6807\u7248\u672C", value: form.version, disabled, onChange: (v) => change("version", v) }),
        h(Field, { label: "\u6307\u6807\u5355\u4F4D", value: form.unit, disabled, onChange: (v) => change("unit", v) }),
        h(Choice, { label: "\u6307\u6807\u6570\u636E\u6E90", value: form.source_id, disabled, options: sources.filter((s) => draft.source_ids.includes(s.id)).map((s) => ({ id: s.id, label: s.name })), onChange: (v) => setForm((f) => ({ ...f, source_id: v, operands: [emptyOperand()], dimensions: [], relations: [] })) }),
        h(Choice, { label: "\u7EDF\u8BA1\u65F6\u95F4\u65B9\u5F0F", value: form.time_mode, disabled, options: [{ id: "period", label: "\u6309\u671F\u95F4\u7EDF\u8BA1" }, { id: "all", label: "\u5168\u90E8\u6388\u6743\u6570\u636E\uFF08\u65E0\u671F\u95F4\uFF09" }], onChange: (v) => change("time_mode", v) }),
        form.time_mode === "period" && h(Field, { label: "\u5DF2\u786E\u8BA4\u53EF\u67E5\u8BE2\u7684\u671F\u95F4", value: form.coverage, disabled, onChange: (v) => change("coverage", v), placeholder: "\u4F8B\u5982 2025, 2026-09, 2026-10" }),
        form.operands.map((o, i) => h(
          Detail,
          { key: i, title: "\u64CD\u4F5C\u6570 " + (i + 1), initialOpen: true },
          h(Field, { label: "\u64CD\u4F5C\u6570\u6807\u8BC6 " + (i + 1), value: o.id, disabled, onChange: (v) => operand(i, "id", v) }),
          h(Choice, { label: "\u6388\u6743\u8868 " + (i + 1), value: o.table, disabled, options: tables.map((t) => ({ id: t.name, label: t.label ? t.label + " \xB7 " + t.name : t.name })), onChange: (v) => operand(i, "table", v) }),
          h(Choice, { label: "\u805A\u5408\u65B9\u5F0F " + (i + 1), value: o.aggregation, disabled, options: [["sum", "\u5408\u8BA1"], ["count", "\u8BA1\u6570"], ["distinct", "\u4E0D\u540C\u503C\u8BA1\u6570"], ["avg", "\u7B97\u672F\u5E73\u5747"], ["min", "\u6700\u5C0F\u503C"], ["max", "\u6700\u5927\u503C"]].map(([id, label]) => ({ id, label })), onChange: (v) => operand(i, "aggregation", v) }),
          h(Choice, { label: "\u7EDF\u8BA1\u5B57\u6BB5 " + (i + 1), value: o.column, disabled, options: [...o.aggregation === "count" ? [{ id: "", label: "\u5168\u90E8\u8BB0\u5F55" }] : [], ...(tables.find((t) => t.name === o.table)?.columns || []).map((c) => ({ id: c.name, label: c.label ? c.label + " \xB7 " + c.name : c.name }))], onChange: (v) => operand(i, "column", v) }),
          form.time_mode === "period" && h(Choice, { label: "\u65F6\u95F4\u5B57\u6BB5 " + (i + 1), value: o.time_column, disabled, options: (tables.find((t) => t.name === o.table)?.columns || []).map((c) => ({ id: c.name, label: c.name })), onChange: (v) => operand(i, "time_column", v) }),
          form.operands.length > 1 && h(Button, { variant: "ghost", size: "sm", disabled, onClick: () => change("operands", form.operands.filter((_, j) => j !== i)) }, "\u79FB\u9664\u6B64\u64CD\u4F5C\u6570")
        )),
        h(Button, { variant: "outline", size: "sm", disabled: disabled || form.operands.length >= 10, onClick: () => change("operands", [...form.operands, { ...emptyOperand(), id: "value" + (form.operands.length + 1) }]) }, "\u6DFB\u52A0\u64CD\u4F5C\u6570"),
        form.time_mode === "period" && h(Button, { variant: "outline", size: "sm", disabled: disabled || checking || !form.operands[0]?.table || !form.operands[0]?.time_column, onClick: inspectTime }, checking ? "\u6B63\u5728\u68C0\u67E5\u65F6\u95F4\u8303\u56F4\u2026" : "\u68C0\u67E5\u9996\u4E2A\u65F6\u95F4\u5B57\u6BB5\u7684\u5B9E\u9645\u8303\u56F4"),
        observed && h("p", { role: "status", className: "nw-setting-description" }, "\u6700\u65E9\u503C\uFF1A" + (observed.first_observed || "\u65E0\u8BB0\u5F55") + "\uFF1B\u6700\u665A\u503C\uFF1A" + (observed.latest_observed || "\u65E0\u8BB0\u5F55") + "\u3002" + observed.message),
        h(Field, { label: "\u8BA1\u7B97\u516C\u5F0F", value: form.formula, disabled, onChange: (v) => change("formula", v), placeholder: "value \u6216 spent / budget * 100" }),
        h("p", { className: "nw-setting-description" }, "\u516C\u5F0F\u5F15\u7528\u4E0A\u65B9\u64CD\u4F5C\u6570\u6807\u8BC6\uFF0C\u652F\u6301\u56DB\u5219\u8FD0\u7B97\u3002\u6BD4\u4F8B\u3001\u5E73\u5747\u6570\u53CA\u5E93\u5B58\u5E94\u6309\u5B9E\u9645\u4E1A\u52A1\u89C4\u5219\u914D\u7F6E\uFF0C\u96F6\u5206\u6BCD\u8FD4\u56DE\u672A\u77E5\u3002\u8DE8\u8868\u7EF4\u5EA6\u3001\u7B5B\u9009\u53CA\u552F\u4E00\u952E\u5173\u8054\u53EF\u5728\u6DFB\u52A0\u540E\u4F7F\u7528\u9AD8\u7EA7\u914D\u7F6E\u3002"),
        tables.length > 0 && h(Detail, { title: "\u53EF\u7528\u5206\u7EC4\u5B57\u6BB5" }, tables.filter((t) => t.name === form.operands[0]?.table).flatMap((t) => (t.columns || []).map((c) => h(Checkbox, { key: t.name + c.name, label: (c.label || c.name) + " \xB7 " + t.name, disabled, checked: form.dimensions.some((d) => d.table === t.name && d.column === c.name), onChange: (checked) => change("dimensions", checked ? [...form.dimensions, { id: "dim" + (form.dimensions.length + 1) + "_" + c.name.replace(/[^a-zA-Z0-9_]/g, "").slice(0, 25), table: t.name, column: c.name }] : form.dimensions.filter((d) => d.table !== t.name || d.column !== c.name)) })))),
        h(Checkbox, { label: "\u5DF2\u786E\u8BA4\u8BE5\u6307\u6807\u53EF\u4EE5\u6309\u6240\u9700\u8303\u56F4\u7D2F\u52A0", checked: form.additive, disabled, onChange: (v) => change("additive", v) }),
        error && h("p", { role: "alert", className: "nw-setting-status", "data-error": true }, error),
        h("div", { className: "nw-actions" }, h(Button, { variant: "outline", size: "sm", disabled, onClick: save }, initial ? "\u66F4\u65B0\u8349\u7A3F\u6307\u6807" : "\u6DFB\u52A0\u5230\u8349\u7A3F"), h(Button, { variant: "ghost", size: "sm", disabled, onClick: onClose }, "\u53D6\u6D88\u914D\u7F6E"))
      );
    }
    function TemplateTransfer({ project, sources, busy, dirty, onImport }) {
      const [raw, setRaw] = React.useState(""), [bindings, setBindings] = React.useState({}), [error, setError] = React.useState(""), [pending, setPending] = React.useState(false);
      let template;
      try {
        template = raw ? JSON.parse(raw) : null;
      } catch {
      }
      const active = React.useRef(true);
      React.useEffect(() => {
        active.current = true;
        return () => {
          active.current = false;
        };
      }, []);
      async function exportConfig() {
        setPending(true);
        setError("");
        try {
          const value = await request("business_template", { business_project: project.id });
          if (!active.current) return;
          const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
          const a = document.createElement("a");
          a.href = url;
          a.download = "ask-data-business-template.json";
          a.click();
          URL.revokeObjectURL(url);
        } catch (e) {
          if (active.current) setError(e.message);
        } finally {
          if (active.current) setPending(false);
        }
      }
      async function file(event) {
        const selected = event.target.files?.[0];
        if (!selected) return;
        if (selected.size > 3e5) {
          setError("\u6A21\u677F\u6587\u4EF6\u8D85\u8FC7 300 KB\u3002");
          return;
        }
        const text = await selected.text();
        if (active.current) {
          setRaw(text);
          setBindings({});
          setError("");
        }
      }
      return h(
        Detail,
        { title: "\u4E1A\u52A1\u6A21\u677F\u5BFC\u5165\u4E0E\u5BFC\u51FA" },
        h("p", { className: "nw-setting-description" }, "\u6A21\u677F\u53EA\u5305\u542B\u4E1A\u52A1\u89C4\u5219\u53CA\u67E5\u8BE2\u793A\u4F8B\uFF0C\u4E0D\u542B\u8FDE\u63A5\u51ED\u636E\u3001\u7528\u6237\u6388\u6743\u6216\u5386\u53F2\u6570\u636E\u3002\u8BF7\u81EA\u884C\u6838\u5BF9\u89C4\u5219\u6587\u5B57\u548C SQL \u4E2D\u7684\u4E1A\u52A1\u654F\u611F\u4FE1\u606F\u3002\u5BFC\u5165\u4EC5\u751F\u6210\u8349\u7A3F\uFF0C\u91CD\u65B0\u7ED1\u5B9A\u5F53\u524D\u5DF2\u6388\u6743\u6765\u6E90\u5E76\u6821\u9A8C\u53D1\u5E03\u540E\u751F\u6548\u3002\u8868\u5B57\u6BB5\u4E0D\u540C\u8BF7\u5148\u8C03\u6574\u6A21\u677F\uFF0C\u7ED3\u6784\u6821\u9A8C\u5931\u8D25\u4E0D\u4F1A\u4FDD\u5B58\u3002"),
        h(Button, { variant: "outline", size: "sm", disabled: busy || dirty || pending, onClick: exportConfig }, "\u5BFC\u51FA\u4E1A\u52A1\u6A21\u677F"),
        h("label", { className: "nw-field" }, h("span", null, "\u9009\u62E9\u4E1A\u52A1\u6A21\u677F\u6587\u4EF6"), h(Input, { type: "file", accept: ".json,application/json", disabled: busy || dirty, onChange: file, "aria-label": "\u9009\u62E9\u4E1A\u52A1\u6A21\u677F\u6587\u4EF6" })),
        template?.sources?.map((s) => h(Choice, { key: s.alias, label: "\u6A21\u677F\u6765\u6E90 " + s.alias, value: bindings[s.alias] || "", disabled: busy || dirty, options: sources.filter((x) => project.draft.source_ids.includes(x.id)).map((x) => ({ id: x.id, label: x.name })), onChange: (id) => setBindings((b) => ({ ...b, [s.alias]: id })) })),
        raw && !template && h("p", { role: "alert" }, "\u6A21\u677F JSON \u65E0\u6548\u3002"),
        template && h(Button, { variant: "outline", size: "sm", disabled: busy || dirty || !template.sources?.every((s) => bindings[s.alias]), onClick: () => onImport(template, bindings) }, "\u6821\u9A8C\u5E76\u5BFC\u5165\u8349\u7A3F"),
        error && h("p", { role: "alert" }, error)
      );
    }
    function PackImport({ project, busy, dirty, onImport }) {
      const [packs, setPacks] = React.useState(null), [choice, setChoice] = React.useState({}), [error, setError] = React.useState("");
      const key = project.id + ":" + (project.draft?.source_ids || []).join(",") + ":" + JSON.stringify(project.draft?.tables || {}).length;
      React.useEffect(() => {
        let live = true;
        request("business_packs", { business_project: project.id }).then((v) => {
          if (live) {
            setPacks(v.packs || []);
            setError("");
          }
        }).catch((e) => {
          if (live) setError(e.message);
        });
        return () => {
          live = false;
        };
      }, [key]);
      if (!packs?.length && !error) return null;
      return h(
        Detail,
        { title: "\u5185\u7F6E\u4E1A\u52A1\u53E3\u5F84\u5305" },
        h("p", { className: "nw-setting-description" }, "\u63D2\u4EF6\u5185\u7F6E\u7684\u4E1A\u52A1\u53E3\u5F84\u5305\uFF0C\u6309\u5F53\u524D\u9879\u76EE\u5DF2\u6388\u6743\u7684\u8868\u81EA\u52A8\u5339\u914D\uFF0C\u5BFC\u5165\u5B57\u6BB5\u4E2D\u6587\u540D\u3001\u53D6\u503C\u3001\u53E3\u5F84\u8BF4\u660E\u3001\u7ED3\u6784\u5316\u6307\u6807\u548C\u67E5\u8BE2\u793A\u4F8B\u3002\u5E93\u91CC\u7F3A\u5C11\u7684\u8868\u6216\u5B57\u6BB5\u4F1A\u8DF3\u8FC7\u5E76\u5217\u51FA\u3002\u5BFC\u5165\u53EA\u751F\u6210\u8349\u7A3F\uFF0C\u6838\u5BF9\u540E\u6821\u9A8C\u53D1\u5E03\uFF1B\u91CD\u590D\u5BFC\u5165\u53EA\u66FF\u6362\u5305\u5185\u6761\u76EE\uFF0C\u6539\u8FC7\u7684\u6761\u76EE\u4FDD\u7559\u3002"),
        (packs || []).map((p) => {
          const sid = choice[p.id] || p.matches[0]?.source_id || "";
          const m = p.matches.find((x) => x.source_id === sid);
          return h(
            SettingRow,
            { key: p.id, title: p.name + " \xB7 v" + p.version, description: p.description + " \u5171 " + p.tables + " \u5F20\u8868\u3001" + p.metrics + " \u4E2A\u7ED3\u6784\u5316\u6307\u6807\u3001" + p.terms + " \u6761\u53E3\u5F84\u8BF4\u660E\u3001" + p.examples + " \u4E2A\u67E5\u8BE2\u793A\u4F8B\u3002" + (m ? "\u5F53\u524D\u5339\u914D " + m.matched_tables + " \u5F20\u8868" + (m.missing_tables.length ? "\uFF0C\u7F3A\u5C11 " + m.missing_tables.length + " \u5F20" : "") + "\u3002" : "\u5F53\u524D\u9879\u76EE\u6CA1\u6709\u5339\u914D\u7684\u6388\u6743\u8868\u3002") + (p.imported_rows ? "\u5DF2\u5BFC\u5165\u3002" : "") },
            p.matches.length > 1 && h(Choice, { label: "\u6570\u636E\u6E90", value: sid, options: p.matches.map((x) => ({ id: x.source_id, label: x.name + "\uFF08" + x.matched_tables + " \u5F20\u8868\uFF09" })), disabled: busy || dirty, onChange: (id) => setChoice((c) => ({ ...c, [p.id]: id })) }),
            h(Button, { variant: "outline", size: "sm", disabled: busy || dirty || !sid, onClick: () => onImport(p.id, sid) }, p.imported_rows ? "\u91CD\u65B0\u5BFC\u5165\u8349\u7A3F" : "\u5BFC\u5165\u5230\u8349\u7A3F")
          );
        }),
        (packs || []).some((p) => p.pending?.length) && h(Detail, { title: "\u5F85\u4E1A\u52A1\u786E\u8BA4\u7684\u53E3\u5F84" }, (packs || []).flatMap((p) => p.pending.map((x, i) => h("p", { key: p.id + ":" + i, className: "nw-setting-description" }, i + 1 + ". " + x)))),
        error && h("p", { role: "alert" }, error)
      );
    }
    return { OnboardingPanel, MetricBuilder, TemplateTransfer, PackImport };
  }

  // plugin/packages/natural-data/administration.js
  function createAdministration({ React, h, Input, Button, SettingsMenu, SettingRow, Detail, DataSettings, request, IconChevronDownOutlineMedium }) {
    const displayLabel = (label) => label.replace(/\s+[a-f0-9-]{20,}(?:\s+\d+)?$/i, "");
    function Field({ label, value, onChange, disabled, placeholder }) {
      return h("label", { className: "nw-field" }, h("span", null, displayLabel(label)), h(Input, { "aria-label": displayLabel(label), value: value ?? "", disabled, placeholder, onChange: (e) => onChange(e.target.value) }));
    }
    function Choice({ label, value, options, onChange, disabled }) {
      const [open, setOpen] = React.useState(false);
      return h(SettingRow, { title: displayLabel(label) }, h(SettingsMenu, { open, onClose: () => setOpen(false), selectedId: value, items: options, onSelect: (id) => {
        onChange(id);
        setOpen(false);
      }, anchor: h(Button, { size: "sm", variant: "outline", disabled, onClick: () => setOpen((v) => !v), "aria-label": displayLabel(label) }, options.find((o) => o.id === value)?.label || "\u8BF7\u9009\u62E9", h(IconChevronDownOutlineMedium, { size: 16 })) }));
    }
    const kinds = [["table", "\u8868\u4E1A\u52A1\u540D"], ["field", "\u5B57\u6BB5\u4E1A\u52A1\u540D"], ["term", "\u672F\u8BED\u4E0E\u540C\u4E49\u8BCD"], ["enum", "\u679A\u4E3E\u6620\u5C04"], ["time", "\u65F6\u95F4\u8BED\u4E49"], ["relation", "\u8868\u5173\u7CFB"]].map(([id, label]) => ({ id, label }));
    function DictionaryEditor({ draft, setDraft, sources, editable, busy }) {
      const [kind, setKind] = React.useState("field");
      const rows = draft.mappings || [], active = sources.filter((s) => draft.source_ids.includes(s.id));
      const tables = (sid) => (active.find((s) => s.id === sid)?.tables || []).filter((t) => (draft.tables[sid] || []).includes(t.name));
      function edit(id, patch) {
        setDraft((d) => ({ ...d, mappings: (d.mappings || []).map((r) => r.id === id ? { ...r, ...patch } : r) }));
      }
      function remove(id) {
        setDraft((d) => ({ ...d, mappings: (d.mappings || []).filter((r) => r.id !== id) }));
      }
      function add() {
        const s = active[0], t = s && tables(s.id)[0], c = t?.columns?.[0];
        setDraft((d) => ({ ...d, mappings: [...d.mappings || [], { id: crypto.randomUUID(), kind, name: (kind === "table" ? t?.label || t?.name : c?.label || c?.name) || "", description: "", source_id: s?.id || "", table: t?.name || "", ...!["table", "relation"].includes(kind) ? { column: c?.name || "" } : {}, synonyms: [], ...kind === "enum" ? { values: [] } : {}, ...kind === "time" ? { grain: "date" } : {}, ...kind === "relation" ? { to_table: "", from_columns: [], to_columns: [], cardinality: "many_to_one" } : {} }] }));
      }
      const disabled = busy || !editable;
      return h(
        "section",
        { "aria-label": "\u4E1A\u52A1\u6620\u5C04\u7BA1\u7406" },
        h("p", { className: "nw-setting-description" }, "\u5C06\u5DF2\u6388\u6743\u8868\u5B57\u6BB5\u914D\u7F6E\u4E3A\u4E1A\u52A1\u8BED\u8A00\u3002\u8FD9\u91CC\u53EA\u7EF4\u62A4\u9879\u76EE\u8349\u7A3F\uFF0C\u4FDD\u5B58\u5E76\u6821\u9A8C\u53D1\u5E03\u540E\u5BF9\u8BDD\u624D\u4F7F\u7528\u3002\u4E0D\u4F1A\u4FEE\u6539\u4E1A\u52A1\u6570\u636E\u5E93\u3002"),
        editable && h(React.Fragment, null, h(Choice, { label: "\u65B0\u589E\u6620\u5C04\u7C7B\u578B", value: kind, options: kinds, onChange: setKind, disabled }), h(Button, { size: "sm", variant: "outline", disabled: disabled || !active.some((s) => tables(s.id).length), onClick: add }, "\u6DFB\u52A0\u4E1A\u52A1\u6620\u5C04")),
        !rows.length && h("p", { className: "nw-setting-description" }, "\u5C1A\u65E0\u4E1A\u52A1\u6620\u5C04\u3002\u57FA\u7840\u95EE\u6570\u4ECD\u53EF\u4F7F\u7528\u6388\u6743\u7269\u7406\u5B57\u6BB5\uFF0C\u4E1A\u52A1\u540D\u79F0\u548C\u89C4\u5219\u8BF7\u914D\u7F6E\u540E\u53D1\u5E03\u3002"),
        rows.map((row) => h(
          Detail,
          { key: row.id, title: (kinds.find((k) => k.id === row.kind)?.label || row.kind) + " \xB7 " + (row.name || "\u5F85\u586B\u5199"), initialOpen: true },
          h(Field, { label: "\u4E1A\u52A1\u540D\u79F0 " + row.id, value: row.name, onChange: (name) => edit(row.id, { name }), disabled, placeholder: "\u4E1A\u52A1\u4EBA\u5458\u4F7F\u7528\u7684\u540D\u79F0" }),
          h(Field, { label: "\u4E1A\u52A1\u8BF4\u660E " + row.id, value: row.description, onChange: (description) => edit(row.id, { description }), disabled }),
          h(Choice, { label: "\u6620\u5C04\u6570\u636E\u6E90 " + row.id, value: row.source_id, options: active.map((s) => ({ id: s.id, label: s.name })), disabled, onChange: (source_id) => edit(row.id, { source_id, table: "", column: "", to_table: "", from_columns: [], to_columns: [] }) }),
          h(Choice, { label: "\u7269\u7406\u8868 " + row.id, value: row.table, options: tables(row.source_id).map((t) => ({ id: t.name, label: (t.label ? t.label + " \xB7 " : "") + t.name })), disabled, onChange: (table2) => edit(row.id, { table: table2, column: "", from_columns: [] }) }),
          !["table", "relation"].includes(row.kind) && h(Choice, { label: "\u7269\u7406\u5B57\u6BB5 " + row.id, value: row.column, options: (tables(row.source_id).find((t) => t.name === row.table)?.columns || []).map((c) => ({ id: c.name, label: (c.label ? c.label + " \xB7 " : "") + c.name })), disabled, onChange: (column) => edit(row.id, { column }) }),
          ["table", "field", "term"].includes(row.kind) && h(Field, { label: "\u540C\u4E49\u8BCD " + row.id, value: (row.synonyms || []).join("\uFF0C"), onChange: (v) => edit(row.id, { synonyms: v.split(/[,，]/).map((x) => x.trim()).filter(Boolean) }), disabled, placeholder: "\u591A\u4E2A\u4E1A\u52A1\u53EB\u6CD5\u7528\u9017\u53F7\u5206\u9694\uFF1B\u6B67\u4E49\u9700\u4EBA\u5DE5\u786E\u8BA4" }),
          row.kind === "time" && h(Choice, { label: "\u65F6\u95F4\u7C92\u5EA6 " + row.id, value: row.grain, options: [{ id: "date", label: "\u65E5" }, { id: "month", label: "\u6708" }, { id: "year", label: "\u5E74" }, { id: "datetime", label: "\u65E5\u671F\u65F6\u95F4" }], disabled, onChange: (grain2) => edit(row.id, { grain: grain2 }) }),
          row.kind === "enum" && h("div", null, (row.values || []).map((v, i) => h("div", { key: i, className: "nw-admin-enum" }, h(Choice, { label: "\u7F16\u7801\u7C7B\u578B " + row.id + " " + i, value: v.type || "text", options: [{ id: "text", label: "\u6587\u672C" }, { id: "number", label: "\u6570\u5B57" }, { id: "boolean", label: "\u5E03\u5C14\u503C" }], disabled, onChange: (type) => edit(row.id, { values: row.values.map((x, j) => j === i ? { ...x, type, value: "" } : x) }) }), h(Field, { label: "\u539F\u59CB\u7F16\u7801 " + row.id + " " + i, value: String(v.value), disabled, onChange: (value) => edit(row.id, { values: row.values.map((x, j) => j === i ? { ...x, value } : x) }) }), h(Field, { label: "\u4E1A\u52A1\u503C " + row.id + " " + i, value: v.label, disabled, onChange: (label) => edit(row.id, { values: row.values.map((x, j) => j === i ? { ...x, label } : x) }) }), editable && h(Button, { size: "sm", disabled, onClick: () => edit(row.id, { values: row.values.filter((_, j) => j !== i) }) }, "\u79FB\u9664\u679A\u4E3E"))), editable && h(Button, { size: "sm", variant: "outline", disabled, onClick: () => edit(row.id, { values: [...row.values || [], { type: "text", value: "", label: "" }] }) }, "\u6DFB\u52A0\u679A\u4E3E\u503C")),
          row.kind === "relation" && h(React.Fragment, null, h(Choice, { label: "\u5173\u8054\u76EE\u6807\u8868 " + row.id, value: row.to_table, options: tables(row.source_id).map((t) => ({ id: t.name, label: t.name })), disabled, onChange: (to_table) => edit(row.id, { to_table, to_columns: [] }) }), h(Field, { label: "\u6765\u6E90\u5173\u8054\u952E " + row.id, value: (row.from_columns || []).join(","), disabled, placeholder: "\u7269\u7406\u5B57\u6BB5\u540D\uFF0C\u590D\u5408\u952E\u7528\u9017\u53F7\u5206\u9694", onChange: (v) => edit(row.id, { from_columns: v.split(/[,，]/).map((x) => x.trim()).filter(Boolean) }) }), h(Field, { label: "\u76EE\u6807\u5173\u8054\u952E " + row.id, value: (row.to_columns || []).join(","), disabled, onChange: (v) => edit(row.id, { to_columns: v.split(/[,，]/).map((x) => x.trim()).filter(Boolean) }) }), h(Choice, { label: "\u5173\u7CFB\u57FA\u6570 " + row.id, value: row.cardinality, options: [{ id: "many_to_one", label: "\u591A\u5BF9\u4E00\uFF08\u76EE\u6807\u552F\u4E00\uFF09" }, { id: "one_to_one", label: "\u4E00\u5BF9\u4E00\uFF08\u4E24\u7AEF\u552F\u4E00\uFF09" }], disabled, onChange: (cardinality) => edit(row.id, { cardinality }) }), h("p", { className: "nw-setting-description" }, "\u53D1\u5E03\u4F1A\u68C0\u67E5\u6570\u636E\u5E93\u552F\u4E00\u952E\uFF1B\u7528\u4E8E\u76EE\u5F55\u89E3\u91CA\u4E0E\u67E5\u8BE2\u53C2\u8003\uFF0C\u6307\u6807\u7684\u8BA1\u7B97\u5173\u7CFB\u4ECD\u968F\u6307\u6807\u5408\u7EA6\u72EC\u7ACB\u6821\u9A8C\u3002")),
          editable && h(Button, { size: "sm", variant: "outline", disabled, onClick: () => remove(row.id) }, "\u79FB\u9664\u6620\u5C04")
        ))
      );
    }
    return { DictionaryEditor };
  }
  var administrationCss = `
.nw-admin{height:100%;min-height:0;overflow:auto;padding:28px 32px;color:var(--dsw-alias-label-primary);background:inherit;box-sizing:border-box}.nw-admin-header{display:flex;align-items:center;justify-content:space-between;gap:24px;margin-bottom:25px}.nw-admin h1{font-size:23px;font-weight:500;line-height:1.4;margin:0}.nw-admin h2{font-size:18px;font-weight:500;margin:0 0 12px}.nw-admin-header p{margin:8px 0 0;font-size:13px;color:var(--dsw-alias-label-secondary)}.nw-admin-layout{display:grid;grid-template-columns:165px minmax(0,1fr);gap:30px}.nw-admin-layout>nav{display:flex;flex-direction:column;align-items:stretch;gap:8px}.nw-admin-content{min-width:0}.nw-admin .nw-settings{max-width:none}.nw-admin .nw-field{min-width:0}.nw-admin-form{display:grid;gap:15px;padding:18px 0;border-bottom:.5px solid var(--dsw-alias-border-l2)}.nw-admin-enum{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px 0;border-bottom:.5px solid var(--dsw-alias-border-l2)}@media(max-width:800px){.nw-admin{padding:20px 16px}.nw-admin-layout{grid-template-columns:1fr;gap:18px}.nw-admin-layout>nav{flex-direction:row;flex-wrap:wrap}.nw-admin-enum{grid-template-columns:1fr}}
`;

  // plugin/packages/natural-data/admin-console.js
  function createConsole(deps) {
    const { React, h, UI, request, getJson, openSession, SettingsMenu, MetricBuilder, PackImport, TemplateTransfer, CapabilitySettings, DatabaseConnections, OnboardingPanel, currentWorkspace = "", workspaces = [] } = deps;
    const { Button, Switch, Checkbox, Input, Modal, Tag } = UI;
    const icon = (name, size = 16) => UI[name] ? h(UI[name], { size }) : null;
    const GROUPS = ["terms", "examples", "skills", "layers", "mappings"];
    const isMetric = (row) => row?.config?.kind === "semantic-metric";
    const uid = () => crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
    function Field({ label, hint, children }) {
      return h("label", { className: "nwc-field" }, h("span", { className: "nwc-field-label" }, label), children, hint && h("span", { className: "nwc-field-hint" }, hint));
    }
    function Text({ label, value, onChange, placeholder, disabled, hint, type }) {
      return h(Field, { label, hint }, h(Input, { "aria-label": label, value: value ?? "", placeholder, disabled, type, onChange: (e) => onChange(e.target.value) }));
    }
    function Area({ label, value, onChange, placeholder, disabled, rows = 4, mono, hint }) {
      return h(Field, { label, hint }, h("textarea", { className: "nwc-textarea" + (mono ? " nwc-mono" : ""), "aria-label": label, value: value ?? "", placeholder, disabled, rows, onChange: (e) => onChange(e.target.value) }));
    }
    function optionLabel(text) {
      const t = String(text ?? ""), k = t.indexOf(" \xB7 ");
      return h("span", { className: "nwc-opt", title: t }, h("span", { className: "nwc-opt-main" }, k < 0 ? t : t.slice(0, k)), k >= 0 && h("span", { className: "nwc-opt-sub" }, t.slice(k + 3)));
    }
    function Pick({ label, value, items, onChange, disabled, placeholder = "\u8BF7\u9009\u62E9" }) {
      const [open, setOpen] = React.useState(false), text = items.find((i) => i.id === value)?.label || value || placeholder;
      return h(Field, { label }, h(UI.Menu, {
        open,
        portal: true,
        dense: true,
        className: "nwc-pick-anchor",
        listClassName: "nwc-pick-menu",
        onClose: () => setOpen(false),
        selectedId: value,
        items: items.map((i) => ({ ...i, label: optionLabel(i.label) })),
        onSelect: (id) => {
          onChange(id);
          setOpen(false);
        },
        anchor: h(Button, { variant: "outline", size: "sm", disabled, className: "nwc-pick", "aria-label": label, title: text, onClick: () => setOpen((v) => !v) }, h("span", { className: "nwc-ellipsis" }, text), icon("IconChevronDownOutlineMedium"))
      }));
    }
    function Search({ value, onChange, placeholder }) {
      return h("div", { className: "nwc-search" }, h(Input, { "aria-label": placeholder, placeholder, value, icon: icon("IconSearchOutlineMedium"), onChange: (e) => onChange(e.target.value) }));
    }
    function FilterMenu({ label, value, items, onChange }) {
      const [open, setOpen] = React.useState(false), cur = items.find((i) => i.id === value);
      return h(UI.Menu, { open, portal: true, dense: true, selectedId: value, listClassName: "nwc-row-menu", onClose: () => setOpen(false), items, onSelect: (id) => {
        onChange(id);
        setOpen(false);
      }, anchor: h(Button, { size: "md", variant: "outline", "aria-label": label, onClick: () => setOpen((v) => !v) }, cur?.label || label, icon("IconChevronDownOutlineMedium", 14)) });
    }
    function Toolbar({ children }) {
      const list = React.Children.toArray(children), isButton = (c) => c && c.type === Button, end = list.filter(isButton);
      return h("div", { className: "nwc-toolbar" }, list.filter((c) => !isButton(c)), end.length > 0 && h("div", { className: "nwc-toolbar-end" }, end.map((c) => React.cloneElement(c, { size: "md" }))));
    }
    function Card({ title, actions, children }) {
      return h("section", { className: "nwc-card" }, (title || actions) && h("div", { className: "nwc-card-head" }, title && h("h3", null, title), actions && h("div", { className: "nwc-actions" }, actions)), children);
    }
    function NameLine({ text, children }) {
      return h("div", { className: "nwc-strong nwc-name-line", title: text }, h("span", { className: "nwc-name" }, text), children);
    }
    function Empty({ children }) {
      return h("p", { className: "nwc-empty" }, children);
    }
    const FLEX_MIN = 140, OPS_COMPACT = 140, CELL_PAD = 24;
    const showFull = (e) => {
      const el = e.currentTarget, cut = el.scrollWidth > el.clientWidth || [...el.querySelectorAll("*")].some((x) => x.scrollWidth > x.clientWidth);
      if (cut && !el.title) el.title = el.innerText.replace(/\n+/g, "\n");
    };
    const Compact = React.createContext ? React.createContext(false) : null;
    function Table({ columns, rows, rowKey, empty = "\u6682\u65E0\u6570\u636E" }) {
      const ref = React.useRef(null), [width, setWidth] = React.useState(0);
      React.useLayoutEffect(() => {
        const el = ref.current;
        if (!el) return;
        setWidth(el.clientWidth);
        const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
        ro.observe(el);
        return () => ro.disconnect();
      }, [rows.length > 0]);
      const need = (cols2, compact2, flex2 = FLEX_MIN) => cols2.reduce((n, c) => n + CELL_PAD + (c.key === "ops" && compact2 ? Math.min(OPS_COMPACT, parseInt(c.width) || OPS_COMPACT) : parseInt(c.width) || flex2), 0);
      let cols = columns, compact = false, flex = FLEX_MIN;
      if (width) {
        const optional = columns.filter((c) => c.optional).reverse();
        for (const c of optional) {
          if (need(cols, false) <= width) break;
          cols = cols.filter((x) => x !== c);
        }
        if (need(cols, false) > width) compact = true;
        const flexCount = cols.filter((c) => !c.width).length;
        if (flexCount && need(cols, compact) > width) flex = Math.max(80, Math.floor(FLEX_MIN - (need(cols, compact) - width) / flexCount));
      }
      if (!rows.length) return h(Empty, null, empty);
      const cls = (c) => [c.className, c.key === "ops" && "nwc-ops"].filter(Boolean).join(" ") || void 0;
      const wrap = (child) => Compact ? h(Compact.Provider, { value: compact }, child) : child;
      return wrap(h("div", { ref, className: "nwc-table-wrap" }, h(
        "table",
        { className: "nwc-table", style: { minWidth: need(cols, compact, flex) + "px" } },
        h("thead", null, h("tr", null, cols.map((c) => h("th", { key: c.key, className: cls(c), style: c.width ? { width: c.key === "ops" && compact ? Math.min(OPS_COMPACT, parseInt(c.width)) + "px" : c.width } : void 0 }, c.title)))),
        h("tbody", null, rows.map((row, i) => h("tr", { key: rowKey(row, i) }, cols.map((c) => {
          const v = c.render ? c.render(row) : row[c.key];
          return h("td", { key: c.key, className: cls(c), title: typeof v === "string" ? v : void 0 }, c.wrap ? v : h("div", { className: "nwc-cell", onMouseEnter: showFull }, v));
        }))))
      )));
    }
    function RowActions({ items }) {
      const [open, setOpen] = React.useState(false), compact = Compact ? React.useContext(Compact) : false;
      const list = items.filter(Boolean), safe = list.filter((i) => !i.danger), shown = safe.slice(0, compact ? 1 : 2), rest = [...safe.slice(shown.length), ...list.filter((i) => i.danger)];
      const button = (i) => h(Button, { key: i.label, size: "sm", variant: "ghost", disabled: i.disabled, onClick: i.onClick }, i.label);
      if (rest.length === 1 && shown.length < 2) return h("div", { className: "nwc-row-actions" }, shown.map(button), h(Button, { size: "sm", variant: "ghost", className: rest[0].danger ? "nwc-danger-text" : void 0, disabled: rest[0].disabled, onClick: rest[0].onClick }, rest[0].label));
      return h("div", { className: "nwc-row-actions" }, shown.map(button), rest.length > 0 && h(UI.Menu, { open, portal: true, align: "end", dense: true, listClassName: "nwc-row-menu", onClose: () => setOpen(false), items: rest.map((i) => ({ id: i.label, label: i.label, disabled: i.disabled, danger: i.danger })), onSelect: (id) => {
        setOpen(false);
        rest.find((i) => i.label === id)?.onClick();
      }, anchor: h(Button, { size: "sm", variant: "ghost", "aria-label": "\u66F4\u591A\u64CD\u4F5C", onClick: () => setOpen((v) => !v) }, "\u66F4\u591A", icon("IconChevronDownOutlineMedium", 14)) }));
    }
    function Dialog({ open, title, description, onClose, children, footer, wide }) {
      return h(Modal, { open, onClose, title, description, closeLabel: "\u5173\u95ED", footer, className: "nwc-dialog" + (wide ? " nwc-dialog-wide" : ""), contentClassName: "nwc-dialog-content" }, h("div", { className: "nwc-dialog-body" }, children));
    }
    function EditDialog({ title, initial, onSave, onClose, render, wide, saveLabel = "\u4FDD\u5B58", validate, aside }) {
      const [value, setValue] = React.useState(() => structuredClone(initial)), [busy, setBusy] = React.useState(false), [error, setError] = React.useState("");
      const set = (patch) => setValue((v) => ({ ...v, ...typeof patch === "function" ? patch(v) : patch }));
      async function save() {
        const problem = validate?.(value);
        if (problem) {
          setError(problem);
          return;
        }
        setBusy(true);
        setError("");
        try {
          await onSave(value);
          onClose();
        } catch (e) {
          setError(e.message);
        } finally {
          setBusy(false);
        }
      }
      return h(
        Dialog,
        { open: true, title, onClose, wide, footer: h("div", { className: "nwc-dialog-footer" }, h("div", { className: "nwc-actions nwc-footer-status" }, aside?.(value, setError), error && h("span", { role: "alert", className: "nwc-error-text nwc-ellipsis", title: error }, error)), h("div", { className: "nwc-actions" }, h(Button, { variant: "ghost", size: "sm", disabled: busy, onClick: onClose }, "\u53D6\u6D88"), h(Button, { variant: "primary", size: "sm", disabled: busy, onClick: save }, busy ? "\u6B63\u5728\u4FDD\u5B58\u2026" : saveLabel))) },
        render(value, set, busy)
      );
    }
    function Section({ title, extra, children }) {
      return h("section", { className: "nwc-form-section" }, h("div", { className: "nwc-form-section-head" }, h("h4", null, title), extra), children);
    }
    function Confirm({ title, description, confirmLabel = "\u786E\u8BA4", danger = true, onConfirm, onClose, children, blocked = false }) {
      const [busy, setBusy] = React.useState(false), [error, setError] = React.useState("");
      async function go() {
        setBusy(true);
        setError("");
        try {
          await onConfirm();
          onClose();
        } catch (e) {
          setError(e.message);
        } finally {
          setBusy(false);
        }
      }
      return h(
        Dialog,
        { open: true, title, onClose, footer: h("div", { className: "nwc-actions" }, h(Button, { variant: "ghost", size: "sm", disabled: busy, onClick: onClose }, "\u53D6\u6D88"), h(Button, { variant: danger ? "outline" : "primary", size: "sm", className: danger ? "nwc-danger-button" : void 0, disabled: busy || blocked, onClick: go }, busy ? "\u6B63\u5728\u5904\u7406\u2026" : confirmLabel)) },
        description && h("p", { className: "nwc-text" }, description),
        children,
        error && h("p", { role: "alert", className: "nwc-error" }, error)
      );
    }
    const clip = (text, n = 80) => {
      const s = String(text ?? "");
      return s.length > n ? s.slice(0, n) + "\u2026" : s;
    };
    const time = (at) => at ? new Date(at * 1e3).toLocaleString() : "";
    const pad = (n) => String(n).padStart(2, "0");
    const shortTime = (at) => {
      if (!at) return "\u2014";
      const d = new Date(at * 1e3), now = /* @__PURE__ */ new Date();
      return h("span", { title: time(at) }, d.getFullYear() === now.getFullYear() ? pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) : d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()));
    };
    function liveEntry(project) {
      return project?.versions?.find((v) => v.version === project.published_version) || null;
    }
    function published(project) {
      return liveEntry(project)?.config || null;
    }
    function draftOf(project) {
      return project ? project.draft || published(project) : null;
    }
    const nameOf = (group, row) => row?.name || row?.question || row?.id;
    const groupNames = { terms: "\u672F\u8BED\u4E0E\u6307\u6807", examples: "\u67E5\u8BE2\u793A\u4F8B", skills: "\u5206\u6790\u65B9\u6CD5\u8BF4\u660E", layers: "\u7A7A\u95F4\u56FE\u5C42\u8BF4\u660E", mappings: "\u5B57\u6BB5\u6620\u5C04" };
    function changes(project) {
      const draft = draftOf(project), base = published(project) || { source_ids: [], tables: {}, terms: [], examples: [], skills: [], layers: [], mappings: [] };
      if (!draft) return [];
      const out = [];
      const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
      for (const sid of /* @__PURE__ */ new Set([...draft.source_ids || [], ...base.source_ids || []])) {
        const was = (base.source_ids || []).includes(sid), is = (draft.source_ids || []).includes(sid);
        if (was !== is) out.push({ area: "\u6570\u636E\u8303\u56F4", kind: is ? "\u65B0\u589E" : "\u79FB\u9664", name: "\u6570\u636E\u6E90 " + sid });
        else if (is && !same([...draft.tables?.[sid] || []].sort(), [...base.tables?.[sid] || []].sort())) out.push({ area: "\u6570\u636E\u8303\u56F4", kind: "\u4FEE\u6539", name: "\u6388\u6743\u8868\uFF08" + sid + "\uFF09" });
      }
      for (const group of GROUPS) {
        const before = new Map((base[group] || []).map((r) => [r.id, r])), after = new Map((draft[group] || []).map((r) => [r.id, r]));
        for (const [id, row] of after) {
          if (!before.has(id)) out.push({ area: groupNames[group], kind: "\u65B0\u589E", name: nameOf(group, row) });
          else if (!same(row, before.get(id))) out.push({ area: groupNames[group], kind: "\u4FEE\u6539", name: nameOf(group, row) });
        }
        for (const [id, row] of before) if (!after.has(id)) out.push({ area: groupNames[group], kind: "\u5220\u9664", name: nameOf(group, row) });
      }
      return out;
    }
    function ConfigurationDiagnostics({ project, enabled }) {
      const [open, setOpen] = React.useState(false), [value, setValue] = React.useState(null), [error, setError] = React.useState(""), [question, setQuestion] = React.useState(""), [busy, setBusy] = React.useState(false), [relation, setRelation] = React.useState(null);
      const run = async (action = "diagnose", relation_id) => {
        setBusy(true);
        setError("");
        try {
          const r = await request("admin_validation", { business_project: project.id, action, question, relation_id });
          action === "relation" ? setRelation(r) : setValue(r);
        } catch (e) {
          setError(e.message);
        } finally {
          setBusy(false);
        }
      };
      if (!enabled) return null;
      const issues = Object.entries(value?.diagnostics || {}).flatMap(([source, rows]) => rows.map((r) => ({ ...r, source })));
      return h(
        React.Fragment,
        null,
        h("div", { className: "nwc-actions" }, h(Button, { variant: "outline", onClick: () => {
          setOpen(true);
          run();
        } }, "\u914D\u7F6E\u8BCA\u65AD")),
        open && h(
          Dialog,
          { open: true, title: "\u914D\u7F6E\u8BCA\u65AD\u4E0E\u590D\u6D4B", onClose: () => setOpen(false) },
          h("p", null, "\u8BCA\u65AD\u5F53\u524D\u5DF2\u751F\u6548\u914D\u7F6E\uFF0C\u4E0D\u81EA\u52A8\u4FEE\u6539\u6388\u6743\u3002\u5173\u8054\u8BCA\u65AD\u53EA\u8BFB\uFF0C\u5355\u6B21\u6700\u591A 30 \u79D2\u3001\u8FD4\u56DE 100 \u884C\uFF0C\u5E76\u53D1 1 \u6B21\u3002"),
          error && h("p", { role: "alert" }, error),
          h(Text, { label: "\u4EE3\u8868\u95EE\u9898\uFF1A\u67E5\u770B\u914D\u7F6E\u5982\u4F55\u547D\u4E2D\u68C0\u7D22", value: question, onChange: setQuestion }),
          h(Button, { disabled: busy, onClick: () => run() }, busy ? "\u68C0\u67E5\u4E2D\u2026" : "\u68C0\u67E5\u914D\u7F6E\u4E0E\u68C0\u7D22"),
          value && h(
            React.Fragment,
            null,
            h("p", null, issues.length ? "\u53D1\u73B0 " + issues.length + " \u6761\u5F85\u6838\u5BF9\u63D0\u793A\uFF08\u663E\u793A\u524D 100 \u6761\uFF09\u3002" : "\u5F53\u524D\u9759\u6001\u68C0\u67E5\u672A\u53D1\u73B0\u95EE\u9898\uFF0C\u4ECD\u9700\u5B9E\u9645\u67E5\u8BE2\u4E0E\u72EC\u7ACB\u771F\u503C\u6838\u5BF9\u3002"),
            issues.length > 0 && h("ul", { className: "nwc-diagnostic-list" }, issues.slice(0, 100).map((r, i) => h("li", { key: i }, [r.name || r.table || r.column || "", r.message].filter(Boolean).join("\uFF1A")))),
            ...Object.values(value.retrieval || {}).flat().map((r) => h("p", { key: r.entry_ref }, r.summary + " \xB7 " + ({ explicit_name: "\u7269\u7406\u540D\u79F0\u547D\u4E2D", business_name_or_alias: "\u4E1A\u52A1\u540D\u79F0\u6216\u522B\u540D\u547D\u4E2D", bm25: "\u8BCD\u6CD5\u76F8\u5173" }[r.retrieval_basis] || r.retrieval_basis))),
            ...(value.relations || []).map((r) => h("div", { key: r.id, className: "nwc-actions" }, h("span", null, r.name + " \xB7 " + r.keys.map((k) => k.join(" \u2192 ")).join("\u3001")), h(Button, { disabled: busy, onClick: () => run("relation", r.id) }, "\u53EA\u8BFB\u5173\u8054\u8BCA\u65AD"))),
            relation && h("div", null, h(Tag, null, relation.state), h("p", null, relation.note), h("pre", null, JSON.stringify(relation.checks || relation.result, null, 2))),
            h("p", null, "\u5DF2\u6709 " + (value.records || []).length + " \u6761\u9A8C\u8BC1\u8BB0\u5F55\uFF0C\u5176\u4E2D " + (value.records || []).filter((r) => r.freshness === "expired").length + " \u6761\u5DF2\u8FC7\u671F\u3002\u53EF\u5728\u67E5\u8BE2\u793A\u4F8B\u4E2D\u8865\u72EC\u7ACB\u9884\u671F\u3001\u8BD5\u8FD0\u884C\u6216\u53D1\u8D77\u539F\u751F\u8BD5\u95EE\u3002")
          )
        )
      );
    }
    const NAV = [
      { group: "\u6570\u636E\u63A5\u5165", items: [["connections", "\u6570\u636E\u6E90\u8FDE\u63A5", "IconDatabaseOutlineMedium", { system: true }]] },
      { group: "\u4E1A\u52A1\u9879\u76EE", items: [["projects", "\u9879\u76EE\u5217\u8868", "IconFolderOpenOutlineMedium"], ["scope", "\u6570\u636E\u8303\u56F4", null, { project: true }], ["members", "\u6210\u5458\u4E0E\u89D2\u8272", "IconUsersOutlineMedium", { project: true, admin: true }], ["versions", "\u53D8\u66F4\u5386\u53F2", null, { project: true }]] },
      { group: "\u4E1A\u52A1\u53E3\u5F84", items: [["mappings", "\u672F\u8BED\u4E0E\u5B57\u6BB5\u6620\u5C04", "IconListPenOutlineMedium", { project: true }], ["metrics", "\u6307\u6807\u53E3\u5F84", null, { project: true }], ["examples", "\u67E5\u8BE2\u793A\u4F8B", null, { project: true }], ["skills", "\u5206\u6790\u6280\u80FD", null, { project: true }], ["imports", "\u53E3\u5F84\u5BFC\u5165", null, { project: true, editor: true }], ["excel", "Excel \u6279\u91CF\u5BFC\u5165", null, { project: true, editor: true }]] },
      { group: "\u95EE\u6570\u914D\u7F6E", items: [["capabilities", "\u529F\u80FD\u5F00\u5173", "IconSlidersTwoOutlineMedium", { project: true }]] }
    ];
    const flatIds = NAV.flatMap((g) => g.items.map(([id]) => id));
    function Console(props = {}) {
      const activeWorkspace = props?.currentWorkspace || currentWorkspace || "";
      const hostWorkspaces = Array.isArray(props?.workspaces) ? props.workspaces : Array.isArray(workspaces) ? workspaces : [];
      const [cfg, setCfg] = React.useState(null), [error, setError] = React.useState(""), [chosen, setRoute] = React.useState(""), [projectId, setProjectId] = React.useState(""), [chosenWorkspace, setChosenWorkspace] = React.useState(""), [notice, setNotice] = React.useState(null), [menu, setMenu] = React.useState(false), [pendingOpen, setPendingOpen] = React.useState(false);
      const [switchingProject, setSwitchingProject] = React.useState(false);
      const revision = React.useRef(0), readEpoch = React.useRef(0), selectedProject = React.useRef(""), selection = React.useRef({ id: "" });
      const held = React.useRef({ inventory: null, inventoryKey: "", details: /* @__PURE__ */ new Map() });
      const CACHE_FIELDS = ["draft", "versions", "review_candidates", "review_reasons"];
      function rememberDetail(id, row) {
        const details = held.current.details, fields = {};
        for (const key of CACHE_FIELDS) if (key in row) fields[key] = structuredClone(row[key]);
        details.delete(id);
        details.set(id, { key: row.detail_revision || "", fields });
        while (details.size > 4) details.delete(details.keys().next().value);
      }
      function reuseDetail(row) {
        const details = held.current.details, entry = details.get(row.id);
        if (!entry || !entry.key || entry.key !== row.detail_revision) return false;
        Object.assign(row, structuredClone(entry.fields));
        details.delete(row.id);
        details.set(row.id, entry);
        return true;
      }
      async function reload(select, force) {
        if (select !== void 0) {
          selection.current = { id: select };
          setSwitchingProject(select !== selectedProject.current);
        }
        const target = selection.current.id, epoch = ++readEpoch.current;
        const cached = held.current, detail = target ? cached.details.get(target) : void 0;
        const args = {
          view: "manager",
          business_project: target,
          project: activeWorkspace || "__operator__",
          ...!force && cached.inventoryKey ? { cached_inventory: cached.inventoryKey } : {},
          ...!force && detail?.key ? { cached_detail: detail.key } : {}
        };
        let value;
        try {
          value = await request("config", args);
        } catch (e) {
          if (epoch !== readEpoch.current) return false;
          selection.current.id = selectedProject.current;
          setSwitchingProject(false);
          throw e;
        }
        if (epoch !== readEpoch.current) return false;
        const row = (value.projects || []).find((p) => p.id === value.selected_project);
        if (value.inventory_elided) {
          if (!cached.inventory) {
            if (force) throw Error("\u914D\u7F6E\u7F13\u5B58\u5DF2\u5931\u6548\uFF0C\u8BF7\u91CD\u65B0\u6253\u5F00\u914D\u7F6E\u7BA1\u7406\u3002");
            return reload(select, true);
          }
          value = { ...value, sources: cached.inventory };
        } else if (Array.isArray(value.sources)) {
          cached.inventory = value.sources;
          cached.inventoryKey = value.inventory_revision || "";
        }
        if (row?.detail_reused) {
          if (!reuseDetail(row)) {
            if (force) throw Error("\u914D\u7F6E\u7F13\u5B58\u5DF2\u5931\u6548\uFF0C\u8BF7\u91CD\u65B0\u6253\u5F00\u914D\u7F6E\u7BA1\u7406\u3002");
            return reload(select, true);
          }
        } else if (row) rememberDetail(row.id, row);
        for (const id2 of [...cached.details.keys()]) if (!(value.projects || []).some((p) => p.id === id2)) cached.details.delete(id2);
        revision.current = value.revision;
        const want = value.selected_project || target || activeWorkspace && value.workspace_defaults?.[activeWorkspace] || value.default_project || value.projects[0]?.id || "";
        const id = value.projects.some((p) => p.id === want) ? want : value.projects[0]?.id || "";
        selection.current.id = id;
        selectedProject.current = id;
        setCfg(value);
        setProjectId(id);
        setSwitchingProject(false);
        return true;
      }
      React.useEffect(() => {
        reload().catch((e) => setError(e.message));
      }, []);
      const project = cfg?.projects.find((p) => p.id === projectId) || null;
      const pending = React.useMemo(() => project ? changes(project) : [], [project]);
      if (!cfg) return h("div", { className: "nwc" }, h("div", { className: "nwc-loading" }, h("p", { role: error ? "alert" : "status" }, error || "\u6B63\u5728\u8BFB\u53D6\u914D\u7F6E\u2026"), error && h(Button, { size: "sm", variant: "outline", onClick: () => {
        setError("");
        reload().catch((e) => setError(e.message));
      } }, "\u91CD\u65B0\u8BFB\u53D6")));
      const route = flatIds.includes(chosen) ? chosen : cfg.system_admin ? "connections" : "projects";
      const role = project?.role, admin = role === "admin", editor = ["admin", "editor"].includes(role);
      const can = (o) => !(o?.system && !cfg.system_admin) && !(o?.admin && !admin) && !(o?.editor && !editor);
      const flat = NAV.flatMap((g) => g.items.map(([id, label, ic, o]) => ({ id, label, icon: ic, group: g.group, ...o || {} })));
      const current = flat.find((x) => x.id === route) || flat[0];
      const say = (text, failed = false) => setNotice(text ? { text, failed } : null);
      async function chooseProject(id) {
        setPendingOpen(false);
        say("");
        return reload(id);
      }
      const viewSelection = selection.current;
      const isCurrentProject = () => selection.current === viewSelection && projectId === viewSelection.id;
      async function act(action, args = {}, message) {
        if (!isCurrentProject()) throw Error("\u4E1A\u52A1\u9879\u76EE\u5DF2\u5207\u6362\uFF0C\u8BF7\u91CD\u65B0\u6253\u5F00\u8BE5\u64CD\u4F5C\u3002");
        try {
          const v = await request("business_update", { action, business_project: projectId, expected_revision: revision.current, ...args });
          await reload();
          if (message && isCurrentProject()) say(typeof message === "function" ? message(v) : message);
          return v;
        } catch (e) {
          if (e.status === 409) {
            await reload();
            throw Error("\u914D\u7F6E\u5DF2\u88AB\u5176\u4ED6\u4EBA\u66F4\u65B0\uFF0C\u5DF2\u91CD\u65B0\u8BFB\u53D6\uFF0C\u8BF7\u518D\u8BD5\u4E00\u6B21\u3002");
          }
          throw e;
        }
      }
      async function saveDraft(mutate, message = "\u5DF2\u4FDD\u5B58\u5E76\u751F\u6548\u3002") {
        const next = mutate(structuredClone(draftOf(project)));
        const value = await act("save", { draft: next, expected_revision: cfg.revision });
        if (!isCurrentProject()) return;
        const saved = value.projects?.find((p) => p.id === projectId);
        say(saved && changes(saved).length ? "\u5DF2\u4FDD\u5B58\u5230\u672A\u751F\u6548\u7684\u6539\u52A8\uFF0C\u8BF7\u6838\u5BF9\u540E\u7EDF\u4E00\u5E94\u7528\u3002" : message);
      }
      const ctx = { cfg, project, admin, editor, act, saveDraft, reload: (select, force = false) => reload(selection.current === viewSelection ? select : void 0, force), say, go: setRoute, rev: () => revision.current };
      const page = !project && current.project && route !== "projects" ? h(Empty, null, cfg.projects.length ? "\u8BF7\u9009\u62E9\u4E1A\u52A1\u9879\u76EE\u3002" : "\u8FD8\u6CA1\u6709\u4E1A\u52A1\u9879\u76EE\u3002" + (cfg.system_admin ? "\u5728\u300C\u9879\u76EE\u5217\u8868\u300D\u4E2D\u65B0\u5EFA\u3002" : "")) : route === "connections" ? h(Connections, ctx) : route === "projects" ? h(Projects, { ...ctx, chooseProject, pendingCount: pending.length, activeWorkspace, hostWorkspaces, chosenWorkspace, setChosenWorkspace }) : route === "scope" ? h(Scope, ctx) : route === "members" ? h(Members, ctx) : route === "mappings" ? h(Mappings, ctx) : route === "metrics" ? h(MetricPage, { ...ctx, key: projectId, hasChanges: pending.length > 0 }) : route === "examples" ? h(Examples, ctx) : route === "skills" ? h(Skills, ctx) : route === "imports" ? h(Imports, { ...ctx, key: projectId }) : route === "excel" ? h(ExcelImport, { ...ctx, key: projectId }) : route === "capabilities" ? h(Capabilities, { ...ctx, key: projectId }) : route === "versions" ? switchingProject ? h(Empty, null, "\u6B63\u5728\u8BFB\u53D6\u53D8\u66F4\u5386\u53F2\u2026") : h(Versions, { ...ctx, key: projectId }) : null;
      return h(
        "div",
        { className: "nwc" },
        h(
          "nav",
          { className: "nwc-nav", "aria-label": "\u914D\u7F6E\u7BA1\u7406\u5BFC\u822A" },
          h("div", { className: "nwc-brand" }, h("span", null, "\u914D\u7F6E\u7BA1\u7406"), cfg.plugin_version && h("span", { className: "nwc-brand-version" }, (cfg.edition ? cfg.edition + " " : "") + cfg.plugin_version)),
          NAV.map((g) => {
            const items = g.items.filter(([, , , o]) => can(o));
            if (!items.length) return null;
            return h(
              "div",
              { key: g.group, className: "nwc-nav-group" },
              h("div", { className: "nwc-nav-title" }, g.group),
              items.map(([id, label, ic]) => h("button", { key: id, type: "button", className: "nwc-nav-item" + (id === route ? " is-active" : ""), "aria-current": id === route ? "page" : void 0, onClick: () => {
                setRoute(id);
                say("");
              } }, h("span", { className: "nwc-nav-icon" }, ic ? icon(ic) : null), h("span", null, label)))
            );
          })
        ),
        h(
          "div",
          { className: "nwc-main" },
          h(
            "header",
            { className: "nwc-top" },
            h("div", { className: "nwc-crumb" }, h("span", null, current.group), h("span", null, "/"), h("h1", null, current.label)),
            h(
              "div",
              { className: "nwc-actions" },
              current.project && cfg.projects.length > 0 && h(SettingsMenu, { open: menu, onClose: () => setMenu(false), selectedId: projectId, items: cfg.projects.map((p) => ({ id: p.id, label: p.name })), onSelect: (id) => {
                setMenu(false);
                chooseProject(id).catch((e) => say(e.message, true));
              }, anchor: h(Button, { variant: "outline", size: "sm", "aria-label": "\u5207\u6362\u4E1A\u52A1\u9879\u76EE", onClick: () => setMenu((v) => !v) }, icon("IconFolderOpenOutlineMedium"), h("span", { className: "nwc-ellipsis" }, project?.name || "\u9009\u62E9\u9879\u76EE"), icon("IconChevronDownOutlineMedium")) }),
              current.project && project && !switchingProject && (pending.length ? h(Button, { size: "sm", variant: "outline", onClick: () => setPendingOpen(true) }, h(Tag, { tone: "warning" }, pending.length + " \u5904"), "\u672A\u751F\u6548\u7684\u6539\u52A8") : null)
            )
          ),
          notice && h("div", { role: notice.failed ? "alert" : "status", className: "nwc-notice" + (notice.failed ? " is-error" : "") }, h("span", null, notice.text), h(Button, { size: "sm", variant: "ghost", onClick: () => say("") }, "\u5173\u95ED")),
          h("main", { className: "nwc-content" }, page),
          pendingOpen && project && !switchingProject && h(Changes, { ...ctx, key: projectId, pending, onClose: () => {
            if (isCurrentProject()) setPendingOpen(false);
          } })
        )
      );
    }
    function Stat({ label, value, action }) {
      return h("button", { type: "button", className: "nwc-stat", disabled: !action, onClick: action || void 0 }, h("span", { className: "nwc-stat-value" }, value), h("span", { className: "nwc-stat-label" }, label));
    }
    const CAP_TEXT = { work: "\u8BB0\u5F55\u6BCF\u6B21\u63D0\u95EE\u7684\u4EFB\u52A1\u3001\u8BA1\u5212\u548C\u7ED3\u679C\uFF1B\u95EE\u6570\u4F9D\u8D56\u5B83\u3002", data: "\u5728\u5DF2\u6388\u6743\u7684\u6570\u636E\u8303\u56F4\u5185\u67E5\u8BE2\u6570\u636E\u5E76\u4F5C\u7B54\u3002", analysis: "\u5728\u67E5\u8BE2\u7ED3\u679C\u4E0A\u505A\u5206\u5E03\u3001\u5BF9\u6BD4\u3001\u5F52\u56E0\u7B49\u5206\u6790\u3002", knowledge: "\u68C0\u7D22\u77E5\u8BC6\u6587\u6863\u3002\u5F53\u524D\u7248\u672C\u8FD8\u6CA1\u6709\u8FD9\u9879\u80FD\u529B\uFF0C\u4FDD\u6301\u5173\u95ED\u3002" };
    const CAP_STATE = { enabled: ["\u542F\u7528", "success"], history: ["\u4EC5\u67E5\u770B\u5386\u53F2", "warning"], disabled: ["\u5173\u95ED", "quiet"] };
    function StateMenu({ value, disabled, label, onChange }) {
      const [open, setOpen] = React.useState(false);
      return h(UI.Menu, {
        open,
        portal: true,
        align: "end",
        dense: true,
        listClassName: "nwc-row-menu",
        selectedId: value,
        onClose: () => setOpen(false),
        items: Object.entries(CAP_STATE).map(([id, [l]]) => ({ id, label: l })),
        onSelect: (id) => {
          setOpen(false);
          if (id !== value) onChange(id);
        },
        anchor: h(Button, { size: "sm", variant: "outline", disabled, "aria-label": label, onClick: () => setOpen((v) => !v) }, CAP_STATE[value]?.[0] || value, icon("IconChevronDownOutlineMedium", 14))
      });
    }
    function Capabilities({ cfg, project, admin, say }) {
      const [scope, setScope] = React.useState("project"), [policy, setPolicy] = React.useState(null), [error, setError] = React.useState(""), [busy, setBusy] = React.useState(false);
      const load = () => getJson("policy", { project: project.id }).then((v) => {
        setPolicy(v);
        setError("");
      }).catch((e) => setError(e.message));
      React.useEffect(() => {
        load();
      }, []);
      if (!policy) return error ? h(Card, null, h("p", { role: "alert", className: "nwc-error nwc-pad" }, error), h("div", { className: "nwc-pad" }, h(Button, { size: "sm", variant: "outline", onClick: load }, "\u91CD\u65B0\u8BFB\u53D6"))) : h(Empty, null, "\u6B63\u5728\u8BFB\u53D6\u2026");
      const editable = scope === "global" ? cfg.system_admin : admin;
      const titleOf = (id) => policy.capabilities.find((c) => c.id === id)?.title || id;
      async function change(id, state) {
        setBusy(true);
        try {
          setPolicy(await request("policy_update", { project: project.id, business_project: project.id, scope, changes: { [id]: state }, expected_revision: policy.revision }));
          say("\u5DF2\u4FDD\u5B58\uFF0C" + (scope === "global" ? "\u6240\u6709\u9879\u76EE" : "\u8FD9\u4E2A\u9879\u76EE") + "\u7684\u4F1A\u8BDD\u7ACB\u5373\u6309\u65B0\u8BBE\u7F6E\u6267\u884C\u3002");
        } catch (e) {
          say(e.message, true);
          load();
        } finally {
          setBusy(false);
        }
      }
      return h(
        React.Fragment,
        null,
        h(
          Toolbar,
          null,
          h("div", { className: "nwc-pills" }, h(UI.Pill, { active: scope === "project", onClick: () => setScope("project") }, "\u5F53\u524D\u9879\u76EE"), h(UI.Pill, { active: scope === "global", disabled: !cfg.system_admin, onClick: () => cfg.system_admin && setScope("global") }, "\u5168\u90E8\u9879\u76EE")),
          h("span", { className: "nwc-sub" }, scope === "global" ? "\u5BF9\u6240\u6709\u9879\u76EE\u751F\u6548\uFF0C\u4F18\u5148\u4E8E\u5355\u4E2A\u9879\u76EE\u7684\u8BBE\u7F6E\u3002" : "\u53EA\u5BF9\u300C" + project.name + "\u300D\u751F\u6548\uFF1B\u5168\u90E8\u9879\u76EE\u91CC\u5173\u95ED\u7684\u529F\u80FD\u8FD9\u91CC\u4E0D\u80FD\u6253\u5F00\u3002")
        ),
        h(Card, null, h(Table, { rowKey: (c) => c.id, rows: policy.capabilities, columns: [
          { key: "title", title: "\u529F\u80FD", render: (c) => h("div", null, h("div", { className: "nwc-strong" }, c.title), h("div", { className: "nwc-sub", title: CAP_TEXT[c.id] || "" }, CAP_TEXT[c.id] || "")) },
          { key: "requires", title: "\u4F9D\u8D56", optional: true, width: "90px", render: (c) => c.requires.length ? c.requires.map(titleOf).join("\u3001") : "\u2014" },
          { key: "state", title: "\u5F53\u524D\u751F\u6548", width: "100px", render: (c) => {
            const [l, t] = CAP_STATE[c.state] || [c.state, "neutral"];
            return h(Tag, { tone: t }, l);
          } },
          { key: "ops", title: scope === "global" ? "\u5168\u90E8\u9879\u76EE\u8BBE\u7F6E" : "\u672C\u9879\u76EE\u8BBE\u7F6E", width: "150px", wrap: true, render: (c) => h(StateMenu, { value: (scope === "global" ? policy.global : policy.project)[c.id] || c.default || "enabled", disabled: busy || !editable, label: c.title + "\u8BBE\u7F6E", onChange: (state) => change(c.id, state) }) }
        ] }))
      );
    }
    const SSL = [["verify-full", "\u6821\u9A8C\u8BC1\u4E66\u548C\u4E3B\u673A\u540D\uFF08\u63A8\u8350\uFF09"], ["verify-ca", "\u4EC5\u6821\u9A8C CA"], ["require", "\u4EC5\u52A0\u5BC6\uFF0C\u4E0D\u6821\u9A8C\u8BC1\u4E66"], ["prefer", "\u5141\u8BB8\u56DE\u9000\u660E\u6587"], ["disable", "\u4E0D\u4F7F\u7528 SSL"]].map(([id, label]) => ({ id, label }));
    const PG_LIKE = ["postgresql", "postgis", "tbase", "kingbase"];
    function Connections({ cfg, project, reload, say }) {
      const [cat, setCat] = React.useState(null), [error, setError] = React.useState(""), [q, setQ] = React.useState(""), [dialog, setDialog] = React.useState(null), [busy, setBusy] = React.useState(""), [tests, setTests] = React.useState({});
      async function load() {
        setCat(await request("connections"));
      }
      React.useEffect(() => {
        load().catch((e) => setError(e.message));
      }, []);
      if (!cat) return error ? h(Card, null, h("p", { role: "alert", className: "nwc-error nwc-pad" }, error), h("div", { className: "nwc-pad" }, h(Button, { size: "sm", variant: "outline", onClick: () => {
        setError("");
        load().catch((e) => setError(e.message));
      } }, "\u91CD\u65B0\u8BFB\u53D6"))) : h(Empty, null, "\u6B63\u5728\u8BFB\u53D6\u6570\u636E\u5E93\u8FDE\u63A5\u2026");
      const typeName = (id) => cat.types.find((t) => t.id === id)?.label || id;
      const projectName = (id) => cfg.projects.find((p) => p.id === id)?.name || id;
      async function update(args, message) {
        try {
          const v = await request("connection_update", { ...args, expected_revision: cat.revision });
          setCat(v);
          await reload();
          say(v?.message || message);
          return v;
        } catch (e) {
          if (e.status === 409) {
            await load();
            throw Error("\u8FDE\u63A5\u914D\u7F6E\u5DF2\u88AB\u5176\u4ED6\u4EBA\u66F4\u65B0\uFF0C\u5DF2\u91CD\u65B0\u8BFB\u53D6\uFF0C\u8BF7\u518D\u8BD5\u4E00\u6B21\u3002");
          }
          throw e;
        }
      }
      async function test(s) {
        setBusy(s.id);
        setTests((t) => ({ ...t, [s.id]: null }));
        const d = /* @__PURE__ */ new Date(), at = pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
        try {
          await request("connection_test", { source_id: s.id });
          setTests((t) => ({ ...t, [s.id]: { ok: true, at } }));
          say("\u300C" + s.name + "\u300D\u8FDE\u63A5\u6210\u529F\uFF08" + at + "\uFF09\u3002");
        } catch (e) {
          setTests((t) => ({ ...t, [s.id]: { ok: false, at, text: e.message } }));
          say("\u300C" + s.name + "\u300D\u8FDE\u63A5\u5931\u8D25\uFF1A" + e.message, true);
        } finally {
          setBusy("");
        }
      }
      const rows = cat.connections.filter((s) => !q || (s.name + " " + (s.description || "") + " " + s.dialect).toLowerCase().includes(q.toLowerCase()));
      const address = (s) => s.dialect === "sqlite" ? s.connection.path : (s.connection.host || "\u672A\u914D\u7F6E") + (s.connection.port ? ":" + s.connection.port : "") + (s.connection.database ? " / " + s.connection.database : "");
      const changed = (s) => s.schema_changes && Object.values(s.schema_changes).some((v) => v.length);
      return h(
        React.Fragment,
        null,
        h(Toolbar, null, h(Search, { value: q, onChange: setQ, placeholder: "\u641C\u7D22\u8FDE\u63A5\u540D\u79F0\u6216\u7C7B\u578B" }), h(Button, { size: "sm", variant: "primary", onClick: () => setDialog({ kind: "edit", isNew: true }) }, icon("IconPlusOutlineMedium"), "\u65B0\u589E\u8FDE\u63A5")),
        h(Card, null, h(Table, { rowKey: (s) => s.id, rows, empty: cat.connections.length ? "\u6CA1\u6709\u5339\u914D\u7684\u8FDE\u63A5" : "\u8FD8\u6CA1\u6709\u6570\u636E\u5E93\u8FDE\u63A5", columns: [
          { key: "name", title: "\u8FDE\u63A5", render: (s) => h("div", null, h(NameLine, { text: s.name }, s.synthetic && h(Tag, { tone: "neutral" }, "\u6A21\u62DF\u6570\u636E"), changed(s) && h(Tag, { tone: "warning" }, "\u7ED3\u6784\u6709\u53D8\u5316")), h("div", { className: "nwc-sub", title: typeName(s.dialect) + " \xB7 " + address(s) }, typeName(s.dialect) + " \xB7 " + address(s))) },
          { key: "state", title: "\u72B6\u6001", width: "110px", render: (s) => {
            const t = tests[s.id];
            return h("div", null, s.enabled ? h(Tag, { tone: "success" }, "\u5DF2\u542F\u7528") : h(Tag, { tone: "quiet" }, "\u5DF2\u505C\u7528"), busy === s.id ? h("div", { className: "nwc-sub" }, "\u6B63\u5728\u6D4B\u8BD5\u2026") : t && h("div", { className: (t.ok ? "nwc-ok-text" : "nwc-error-text") + " nwc-sub", title: t.ok ? "\u8FDE\u63A5\u6210\u529F " + t.at : t.text }, (t.ok ? "\u2713 \u8FDE\u63A5\u6B63\u5E38 " : "\u2717 \u8FDE\u63A5\u5931\u8D25 ") + t.at.slice(0, 5)));
          } },
          { key: "tables", title: "\u8868", optional: true, width: "70px", render: (s) => String(s.table_count) },
          { key: "used", title: "\u5173\u8054\u9879\u76EE", optional: true, width: "100px", render: (s) => s.used_by.length ? h("span", { title: s.used_by.map(projectName).join("\u3001") }, s.used_by.length + " \u4E2A") : "\u2014" },
          { key: "checked", title: "\u6700\u8FD1\u68C0\u67E5", optional: true, width: "110px", render: (s) => shortTime(s.last_checked) },
          { key: "ops", title: "\u64CD\u4F5C", width: "200px", wrap: true, render: (s) => h(RowActions, { items: [
            { label: "\u7F16\u8F91", onClick: () => setDialog({ kind: "edit", source: s }) },
            { label: busy === s.id ? "\u6B63\u5728\u6D4B\u8BD5\u2026" : "\u6D4B\u8BD5\u8FDE\u63A5", disabled: Boolean(busy), onClick: () => test(s) },
            { label: "\u67E5\u770B\u8868\u7ED3\u6784", onClick: () => setDialog({ kind: "tables", source: s }) },
            { label: "\u5237\u65B0\u8868\u7ED3\u6784", onClick: () => update({ action: "refresh", source_id: s.id }, "\u8868\u7ED3\u6784\u5DF2\u66F4\u65B0\uFF0C\u8BF7\u68C0\u67E5\u76F8\u5173\u9879\u76EE\u7684\u6570\u636E\u8303\u56F4\u3002").catch((e) => say(e.message, true)) },
            s.enabled ? { label: "\u505C\u7528", onClick: () => setDialog({ kind: "disable", source: s }) } : { label: "\u542F\u7528", onClick: () => update({ action: "state", source_id: s.id, enabled: true }, "\u8FDE\u63A5\u5DF2\u542F\u7528\u3002").catch((e) => say(e.message, true)) },
            { label: "\u5220\u9664", danger: true, onClick: () => setDialog({ kind: "delete", source: s }) }
          ] }) }
        ] })),
        dialog?.kind === "edit" && h(ConnectionDialog, { cat, source: dialog.source, isNew: dialog.isNew, update, say, onClose: () => setDialog(null) }),
        dialog?.kind === "tables" && h(SchemaDialog, { source: dialog.source, project, onClose: () => setDialog(null), onRefresh: () => update({ action: "refresh", source_id: dialog.source.id }, "\u8868\u7ED3\u6784\u5DF2\u66F4\u65B0\uFF0C\u8BF7\u68C0\u67E5\u76F8\u5173\u9879\u76EE\u7684\u6570\u636E\u8303\u56F4\u3002").then((v) => setDialog({ kind: "tables", source: v.connections.find((x) => x.id === dialog.source.id) || dialog.source })) }),
        dialog?.kind === "disable" && h(Confirm, { title: "\u505C\u7528\u8FDE\u63A5\u300C" + dialog.source.name + "\u300D", description: "\u505C\u7528\u540E\u4E0D\u518D\u53D1\u8D77\u65B0\u67E5\u8BE2\uFF0C\u6B63\u5728\u6267\u884C\u7684\u67E5\u8BE2\u4F1A\u88AB\u53D6\u6D88\uFF1B\u9879\u76EE\u914D\u7F6E\u548C\u5386\u53F2\u7ED3\u679C\u4FDD\u7559\uFF0C\u53EF\u968F\u65F6\u91CD\u65B0\u542F\u7528\u3002", confirmLabel: "\u505C\u7528\u8FDE\u63A5", onClose: () => setDialog(null), onConfirm: () => update({ action: "state", source_id: dialog.source.id, enabled: false }, "\u8FDE\u63A5\u5DF2\u505C\u7528\u3002") }),
        dialog?.kind === "delete" && h(DeleteConnection, { source: dialog.source, projectName, onClose: () => setDialog(null), onConfirm: (detach) => update({ action: "delete", source_id: dialog.source.id, confirm_delete: true, confirm_detach: detach, expected_business_revision: cat.business_revision }, "\u8FDE\u63A5\u5DF2\u5220\u9664\uFF0C\u6E90\u6570\u636E\u5E93\u6CA1\u6709\u6539\u52A8\u3002") })
      );
    }
    function DeleteConnection({ source, projectName, onClose, onConfirm }) {
      const [detach, setDetach] = React.useState(false), used = source.used_by.length > 0;
      return h(
        Confirm,
        { title: "\u5220\u9664\u8FDE\u63A5\u300C" + source.name + "\u300D", description: "\u5220\u9664\u8FDE\u63A5\u914D\u7F6E\u548C\u4FDD\u5B58\u7684\u51ED\u636E\uFF0C\u4E0D\u5220\u9664\u6E90\u6570\u636E\u5E93\u4E2D\u7684\u4EFB\u4F55\u8868\u6216\u6570\u636E\u3002", confirmLabel: "\u5220\u9664\u8FDE\u63A5", blocked: used && !detach, onClose, onConfirm: () => onConfirm(detach) },
        used && h(Checkbox, { label: "\u540C\u65F6\u4ECE " + source.used_by.length + " \u4E2A\u9879\u76EE\u4E2D\u79FB\u9664\u8FD9\u4E2A\u6570\u636E\u6E90\uFF08" + source.used_by.map(projectName).join("\u3001") + "\uFF09\uFF0C\u7ACB\u5373\u64A4\u9500\u67E5\u8BE2\u6388\u6743", checked: detach, onChange: setDetach })
      );
    }
    function ConnectionDialog({ cat, source, isNew, update, say, onClose }) {
      const [testing, setTesting] = React.useState(false), [tested, setTested] = React.useState(null);
      const initial = isNew ? { name: "", description: "", dialect: "postgresql", host: "", port: 5432, database: "", schema: "public", username: "", password: "", sslmode: "verify-full", ssl_ca: "", path: "", synthetic: false, clear_password: false, confirm_target_change: false } : { name: source.name, description: source.description || "", dialect: source.dialect, ...source.connection, password: "", clear_password: false, synthetic: Boolean(source.synthetic), confirm_target_change: false };
      const targetKeys = (v) => ["dialect", ...v.dialect === "sqlite" ? ["path"] : ["host", "port", "database", "schema", "username", "sslmode", "ssl_ca"]];
      const targetChanged = (v) => !isNew && targetKeys(v).some((k) => String(v[k] ?? "") !== String((k === "dialect" ? source.dialect : source.connection[k]) ?? ""));
      const onlyText = (v) => !isNew && !targetChanged(v) && !v.password && !v.clear_password && Boolean(v.synthetic) === Boolean(source.synthetic);
      function payload(v) {
        const { password_set, tls_warning, clear_password, confirm_target_change, ...values } = v;
        values.port = Number(values.port);
        values.password_action = !isNew && source.connection.password_set && !values.password && !clear_password ? "keep" : "replace";
        if (clear_password) values.password = "";
        return { source_id: isNew ? void 0 : source.id, connection: values, confirm_target_change };
      }
      const dbLabel = (d) => d === "oracle" ? "Oracle \u670D\u52A1\u540D" : d === "dameng" ? "\u8FBE\u68A6\u5B9E\u4F8B" : "\u6570\u636E\u5E93\u540D\u79F0";
      return h(EditDialog, {
        title: isNew ? "\u65B0\u589E\u6570\u636E\u5E93\u8FDE\u63A5" : "\u7F16\u8F91\u8FDE\u63A5\u300C" + source.name + "\u300D",
        wide: true,
        initial,
        saveLabel: isNew ? "\u4FDD\u5B58\u5E76\u8BFB\u53D6\u8868\u7ED3\u6784" : "\u4FDD\u5B58",
        onClose,
        validate: (v) => !v.name.trim() ? "\u8BF7\u586B\u5199\u8FDE\u63A5\u540D\u79F0\u3002" : v.dialect === "sqlite" ? !v.path.trim() && "\u8BF7\u586B\u5199\u6570\u636E\u5E93\u6587\u4EF6\u8DEF\u5F84\u3002" : !v.host.trim() || !v.port ? "\u8BF7\u586B\u5199\u670D\u52A1\u5668\u5730\u5740\u548C\u7AEF\u53E3\u3002" : targetChanged(v) && source.used_by.length && !v.confirm_target_change ? "\u8FD9\u4E2A\u8FDE\u63A5\u5DF2\u88AB\u9879\u76EE\u4F7F\u7528\uFF0C\u4FEE\u6539\u8FDE\u63A5\u76EE\u6807\u524D\u8BF7\u52FE\u9009\u786E\u8BA4\u3002" : null,
        onSave: (v) => onlyText(v) ? update({ action: "metadata", source_id: source.id, name: v.name.trim(), description: v.description }, "\u540D\u79F0\u548C\u8BF4\u660E\u5DF2\u4FDD\u5B58\u3002") : update({ action: "save", ...payload(v) }, isNew ? "\u8FDE\u63A5\u5DF2\u4FDD\u5B58\uFF0C\u8868\u7ED3\u6784\u5DF2\u8BFB\u53D6\u3002\u8BF7\u5230\u300C\u6570\u636E\u8303\u56F4\u300D\u4E3A\u9879\u76EE\u6388\u6743\u8868\u3002" : "\u8FDE\u63A5\u5DF2\u4FDD\u5B58\uFF0C\u8868\u7ED3\u6784\u5DF2\u91CD\u65B0\u8BFB\u53D6\u3002"),
        aside: (v, setError) => h(
          React.Fragment,
          null,
          h(Button, { size: "sm", variant: "outline", disabled: testing, onClick: async () => {
            setTesting(true);
            setTested(null);
            setError("");
            try {
              const r = await request("connection_test", payload(v));
              setTested({ ok: true, text: "\u8FDE\u63A5\u6210\u529F" });
            } catch (e) {
              setTested({ ok: false, text: e.message || "\u8FDE\u63A5\u5931\u8D25" });
            } finally {
              setTesting(false);
            }
          } }, testing ? "\u6B63\u5728\u6D4B\u8BD5\u2026" : "\u6D4B\u8BD5\u8FDE\u63A5"),
          tested && h("span", { role: tested.ok ? "status" : "alert", className: (tested.ok ? "nwc-ok-text" : "nwc-error-text") + " nwc-ellipsis", title: tested.text }, tested.ok ? "\u2713 " + tested.text : "\u8FDE\u63A5\u5931\u8D25\uFF1A" + tested.text)
        ),
        render: (v, set) => h(
          React.Fragment,
          null,
          h(
            Section,
            { title: "\u57FA\u672C\u4FE1\u606F" },
            h(
              "div",
              { className: "nwc-form-grid" },
              h(Text, { label: "\u8FDE\u63A5\u540D\u79F0", value: v.name, onChange: (name) => set({ name }) }),
              h(Pick, { label: "\u6570\u636E\u5E93\u7C7B\u578B", value: v.dialect, items: cat.types.map((t) => ({ id: t.id, label: t.label + (t.available ? "" : "\uFF08\u9A71\u52A8\u672A\u5B89\u88C5\uFF09"), disabled: !t.available })), onChange: (id) => set((x) => ({ dialect: id, port: cat.types.find((t) => t.id === id)?.port || 5432, schema: id === "mysql" ? x.database : "public", password: "" })) })
            ),
            h(Text, { label: "\u8BF4\u660E", value: v.description, placeholder: "\u53EF\u9009\uFF0C\u4F8B\u5982\u7528\u9014\u3001\u8D1F\u8D23\u4EBA", onChange: (description) => set({ description }) })
          ),
          h(Section, { title: "\u8FDE\u63A5\u53C2\u6570" }, v.dialect === "sqlite" ? h(Text, { label: "\u6570\u636E\u5E93\u6587\u4EF6\u8DEF\u5F84", value: v.path, placeholder: "\u5DF2\u6709\u6570\u636E\u5E93\u6587\u4EF6\u7684\u7EDD\u5BF9\u8DEF\u5F84", onChange: (path) => set({ path }) }) : h(
            React.Fragment,
            null,
            h(
              "div",
              { className: "nwc-form-grid nwc-form-grid-host" },
              h(Text, { label: "\u670D\u52A1\u5668\u5730\u5740", value: v.host, onChange: (host) => set({ host }) }),
              h(Text, { label: "\u7AEF\u53E3", value: String(v.port ?? ""), type: "number", onChange: (port) => set({ port }) })
            ),
            h(
              "div",
              { className: "nwc-form-grid" },
              h(Text, { label: dbLabel(v.dialect), value: v.database, onChange: (database) => set({ database }) }),
              h(Text, { label: "Schema", value: v.schema, placeholder: v.dialect === "mysql" ? "\u4E0E\u6570\u636E\u5E93\u540D\u79F0\u4E00\u81F4" : "public", onChange: (schema) => set({ schema }) }),
              h(Text, { label: "\u7528\u6237\u540D", value: v.username, onChange: (username) => set({ username }) }),
              h(Field, { label: "\u5BC6\u7801" }, h(Input, { "aria-label": "\u5BC6\u7801", type: "password", autoComplete: "new-password", value: v.password, disabled: v.clear_password, placeholder: !isNew && source.connection.password_set ? "\u7559\u7A7A\u5219\u4FDD\u7559\u5DF2\u4FDD\u5B58\u7684\u5BC6\u7801" : "", onChange: (e) => set({ password: e.target.value }) }))
            ),
            !isNew && source.connection.password_set && h(Checkbox, { label: "\u6E05\u7A7A\u5DF2\u4FDD\u5B58\u7684\u5BC6\u7801", checked: v.clear_password, onChange: (clear_password) => set({ clear_password }) }),
            [...PG_LIKE, "mysql"].includes(v.dialect) ? h(React.Fragment, null, h("div", { className: "nwc-form-grid" }, h(Pick, { label: "SSL \u6A21\u5F0F", value: v.sslmode, items: SSL.filter((x) => v.dialect !== "mysql" || x.id !== "prefer"), onChange: (sslmode) => set({ sslmode }) }), h(Text, { label: "SSL CA \u8BC1\u4E66\u8DEF\u5F84\uFF08\u53EF\u9009\uFF09", value: v.ssl_ca, placeholder: "MySQL \u7559\u7A7A\u4F7F\u7528\u7CFB\u7EDF CA\uFF1BPostgreSQL \u4F7F\u7528\u9A71\u52A8\u9ED8\u8BA4 CA", onChange: (ssl_ca) => set({ ssl_ca }) })), v.sslmode !== "verify-full" && h("p", { role: "alert", className: "nwc-sub" }, "\u5F53\u524D\u6A21\u5F0F\u672A\u540C\u65F6\u5F3A\u5236\u52A0\u5BC6\u3001\u6821\u9A8C\u8BC1\u4E66\u548C\u4E3B\u673A\u540D\uFF1B\u4EC5\u5728\u5DF2\u6838\u5B9E\u7684\u53D7\u63A7\u73AF\u5883\u4E2D\u4F7F\u7528\u3002")) : h("p", { className: "nwc-sub" }, "\u8FD9\u7C7B\u6570\u636E\u5E93\u7684\u52A0\u5BC6\u8FDE\u63A5\u9700\u6309\u90E8\u7F72\u8BF4\u660E\u5355\u72EC\u914D\u7F6E\u3002")
          )),
          h(
            Section,
            { title: "\u5176\u4ED6" },
            h(Checkbox, { label: "\u8FD9\u662F\u6D4B\u8BD5\u6216\u6A21\u62DF\u6570\u636E", checked: v.synthetic, onChange: (synthetic) => set({ synthetic }) }),
            targetChanged(v) && source.used_by.length > 0 && h(Checkbox, { label: "\u786E\u8BA4\u628A\u4F7F\u7528\u8FD9\u4E2A\u8FDE\u63A5\u7684 " + source.used_by.length + " \u4E2A\u9879\u76EE\u5207\u6362\u5230\u65B0\u7684\u6570\u636E\u5E93\u76EE\u6807\uFF08\u4E4B\u540E\u9700\u91CD\u65B0\u6838\u5BF9\u6388\u6743\u8868\u548C\u53E3\u5F84\uFF09", checked: v.confirm_target_change, onChange: (confirm_target_change) => set({ confirm_target_change }) }),
            h("p", { className: "nwc-sub" }, "\u4FDD\u5B58\u65F6\u4F1A\u6D4B\u8BD5\u8FDE\u63A5\u5E76\u8BFB\u53D6\u8868\u7ED3\u6784\uFF0C\u4E0D\u4F1A\u5199\u5165\u6E90\u6570\u636E\u5E93\u3002\u53EA\u6539\u540D\u79F0\u548C\u8BF4\u660E\u65F6\u4E0D\u91CD\u65B0\u8FDE\u63A5\u3002")
          )
        )
      });
    }
    function SchemaDialog({ source, project, onClose, onRefresh }) {
      const config = project && (published(project) || draftOf(project));
      const mapped = new Map((config?.mappings || []).filter((m) => m.kind === "table" && m.source_id === source.id && m.name).map((m) => [m.table, m.name]));
      const label = (t) => mapped.get(t.name) || t.label || "";
      const [q, setQ] = React.useState(""), [busy, setBusy] = React.useState(false), [error, setError] = React.useState("");
      const rows = source.tables.filter((t) => !q || (t.name + " " + label(t) + " " + t.columns.map((c) => c.name).join(" ")).toLowerCase().includes(q.toLowerCase()));
      const ch = source.schema_changes || {};
      const schemas = new Set(source.tables.map((t) => t.name.includes(".") ? t.name.slice(0, t.name.indexOf(".") + 1) : ""));
      const prefix = schemas.size === 1 ? [...schemas][0] : "", short = (n) => prefix && n.startsWith(prefix) ? n.slice(prefix.length) : n;
      return h(
        Dialog,
        { open: true, wide: true, title: "\u8868\u7ED3\u6784 \xB7 " + source.name, onClose, footer: h("div", { className: "nwc-dialog-footer" }, h("span", { className: "nwc-sub" }, "\u5171 " + source.table_count + " \u5F20\u8868" + (source.last_checked ? " \xB7 \u6700\u8FD1\u68C0\u67E5 " + time(source.last_checked) : "") + (mapped.size ? " \xB7 \u4E1A\u52A1\u540D\u53D6\u81EA\u9879\u76EE\u300C" + project.name + "\u300D\u7684\u8868\u4E1A\u52A1\u540D" : "")), h("div", { className: "nwc-actions" }, h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: async () => {
          setBusy(true);
          setError("");
          try {
            await onRefresh();
          } catch (e) {
            setError(e.message);
          } finally {
            setBusy(false);
          }
        } }, busy ? "\u6B63\u5728\u5237\u65B0\u2026" : "\u5237\u65B0\u8868\u7ED3\u6784"), h(Button, { size: "sm", variant: "primary", onClick: onClose }, "\u5173\u95ED"))) },
        Object.values(ch).some((v) => v?.length) && h("div", { className: "nwc-notice is-warning" }, "\u6700\u8FD1\u4E00\u6B21\u5237\u65B0\uFF1A\u65B0\u589E " + (ch.added_tables || []).length + " \u5F20\u8868\uFF0C\u79FB\u9664 " + (ch.removed_tables || []).length + " \u5F20\u8868\uFF0C" + (ch.changed_tables || []).length + " \u5F20\u8868\u5B57\u6BB5\u6709\u53D8\u5316\u3002\u8BF7\u6838\u5BF9\u9879\u76EE\u7684\u6388\u6743\u8868\u548C\u6307\u6807\u53E3\u5F84\u3002"),
        h(Search, { value: q, onChange: setQ, placeholder: "\u641C\u7D22\u8868\u540D\u3001\u4E1A\u52A1\u540D\u6216\u5B57\u6BB5" }),
        h(Table, { rowKey: (t) => t.name, rows, empty: "\u6CA1\u6709\u5339\u914D\u7684\u8868", columns: [
          { key: "name", title: prefix ? "\u8868\u540D\uFF08" + prefix.slice(0, -1) + "\uFF09" : "\u8868\u540D", render: (t) => h("span", { className: "nwc-mono", title: t.name }, short(t.name)) },
          { key: "label", title: "\u4E1A\u52A1\u540D", render: (t) => h("span", { title: label(t) }, label(t) || "\u2014") },
          { key: "count", title: "\u5B57\u6BB5\u6570", width: "70px", render: (t) => String(t.columns.length) },
          { key: "columns", title: "\u5B57\u6BB5", render: (t) => {
            const text = t.columns.map((c) => c.name).join("\u3001");
            return h("span", { className: "nwc-mono", title: t.columns.map((c) => c.name + " " + c.type).join("\n") }, text);
          } }
        ] }),
        error && h("p", { role: "alert", className: "nwc-error" }, error)
      );
    }
    function Projects({ cfg, project, chooseProject, pendingCount, say, go, reload, activeWorkspace, hostWorkspaces = [], chosenWorkspace = "", setChosenWorkspace }) {
      const [dialog, setDialog] = React.useState(null), [q, setQ] = React.useState(""), [wsMenu, setWsMenu] = React.useState(false);
      const rows = cfg.projects.filter((p) => !q || p.name.includes(q) || (p.description || "").includes(q));
      const roles = { admin: "\u9879\u76EE\u7BA1\u7406\u5458", editor: "\u7F16\u8F91\u8005", viewer: "\u4F7F\u7528\u8005" };
      const targetWorkspace = chosenWorkspace || activeWorkspace;
      const isHostWs = Boolean(targetWorkspace && targetWorkspace === activeWorkspace);
      const wsOptions = [];
      const seenWs = /* @__PURE__ */ new Set();
      const addWs = (path, label) => {
        const p = String(path || "").trim();
        if (!p || seenWs.has(p)) return;
        seenWs.add(p);
        wsOptions.push({ id: p, label });
      };
      const labelForWs = (path, suffix = "") => {
        const hw = hostWorkspaces.find((w) => w.path === path);
        return (hw?.title || path.split("/").pop() || path) + suffix + " \xB7 " + path;
      };
      if (activeWorkspace) addWs(activeWorkspace, labelForWs(activeWorkspace, "\uFF08\u5F53\u524D\u4F1A\u8BDD\uFF09"));
      for (const w of hostWorkspaces) addWs(w.path, labelForWs(w.path));
      for (const p of Object.keys(cfg.workspace_defaults || {})) addWs(p, labelForWs(p, "\uFF08\u5DF2\u914D\u7F6E\uFF09"));
      const targetLabel = wsOptions.find((o) => o.id === targetWorkspace)?.label.split(" \xB7 ")[0] || (targetWorkspace ? targetWorkspace.split("/").pop() || targetWorkspace : "\u9009\u62E9\u5DE5\u4F5C\u533A\u2026");
      async function rowAct(p, action, args, message) {
        try {
          const v = await request("business_update", { action, business_project: p.id, expected_revision: cfg.revision, ...args });
          await reload(v?.selected_project || p.id);
          if (message) say(message);
        } catch (e) {
          if (e.status === 409) await reload();
          throw e;
        }
      }
      return h(
        React.Fragment,
        null,
        h(
          Toolbar,
          null,
          h(Search, { value: q, onChange: setQ, placeholder: "\u641C\u7D22\u9879\u76EE" }),
          SettingsMenu && wsOptions.length > 0 && h(SettingsMenu, { open: wsMenu, onClose: () => setWsMenu(false), selectedId: targetWorkspace, items: [...wsOptions, ...chosenWorkspace && chosenWorkspace !== activeWorkspace ? [{ id: "", label: activeWorkspace ? "\u5207\u56DE\u5F53\u524D\u4F1A\u8BDD\u5DE5\u4F5C\u533A" : "\u53D6\u6D88\u5DE5\u4F5C\u533A\u7B5B\u9009" }] : []], onSelect: (id) => {
            setChosenWorkspace?.(id);
            setWsMenu(false);
            say("");
          }, anchor: h(Button, { variant: "outline", size: "sm", "aria-label": "\u9009\u62E9\u5DE5\u4F5C\u533A", onClick: () => setWsMenu((v) => !v) }, icon("IconFolderOpenOutlineMedium"), h("span", { className: "nwc-ellipsis" }, "\u5DE5\u4F5C\u533A\uFF1A" + targetLabel), icon("IconChevronDownOutlineMedium")) }),
          cfg.system_admin && h(Button, { size: "sm", variant: "primary", onClick: () => setDialog({ kind: "create" }) }, icon("IconPlusOutlineMedium"), "\u65B0\u5EFA\u9879\u76EE")
        ),
        h(Card, null, h(Table, { rowKey: (p) => p.id, rows, empty: "\u6CA1\u6709\u4E1A\u52A1\u9879\u76EE", columns: [
          { key: "name", title: "\u9879\u76EE", render: (p) => {
            const isGlobal = cfg.default_project === p.id;
            const isWs = targetWorkspace && cfg.workspace_defaults?.[targetWorkspace] === p.id;
            const wsCount = Object.values(cfg.workspace_defaults || {}).filter((bid) => bid === p.id).length;
            return h(
              "div",
              null,
              h(
                NameLine,
                { text: p.name },
                isGlobal && h(Tag, { tone: "info" }, "\u5168\u5C40\u9ED8\u8BA4"),
                isWs && h(Tag, { tone: "success" }, isHostWs ? "\u5F53\u524D\u5DE5\u4F5C\u533A\u9ED8\u8BA4" : "\u6240\u9009\u5DE5\u4F5C\u533A\u9ED8\u8BA4"),
                !isWs && wsCount > 0 && h(Tag, { tone: "neutral" }, wsCount + " \u4E2A\u5DE5\u4F5C\u533A\u9ED8\u8BA4")
              ),
              p.description && h("div", { className: "nwc-sub", title: p.description }, clip(p.description, 60))
            );
          } },
          { key: "state", title: "\u914D\u7F6E\u72B6\u6001", width: "150px", render: (p) => {
            const n = p.id === project?.id ? pendingCount : p.details_loaded === false ? p.pending_change_count : changes(p).length;
            return h("span", null, h(Tag, { tone: p.published_version ? "success" : "neutral" }, p.published_version ? "\u5DF2\u751F\u6548" : "\u5C1A\u672A\u751F\u6548"), n > 0 && h(Tag, { tone: "warning" }, n + " \u5904\u672A\u5E94\u7528"));
          } },
          { key: "updated", title: "\u6700\u8FD1\u751F\u6548\u65F6\u95F4", width: "150px", render: (p) => shortTime(liveEntry(p)?.at) },
          { key: "sources", title: "\u6570\u636E\u6E90 / \u8868", optional: true, width: "100px", render: (p) => {
            const d = draftOf(p) || { source_ids: [], tables: {} };
            return d.source_ids.length + " / " + Object.values(d.tables || {}).reduce((n, t) => n + t.length, 0);
          } },
          { key: "role", title: "\u6211\u7684\u89D2\u8272", optional: true, width: "100px", render: (p) => roles[p.role] || p.role || "\u2014" },
          { key: "ops", title: "\u64CD\u4F5C", width: "220px", wrap: true, render: (p) => h(RowActions, { items: [
            { label: "\u8FDB\u5165", onClick: () => chooseProject(p.id).then((loaded) => {
              if (loaded) go("scope");
            }).catch((e) => say(e.message, true)) },
            p.role === "admin" && { label: "\u7F16\u8F91\u4FE1\u606F", onClick: () => setDialog({ kind: "edit", project: p }) },
            cfg.default_project !== p.id && { label: "\u8BBE\u4E3A\u5168\u5C40\u9ED8\u8BA4", disabled: !p.published_version, onClick: () => rowAct(p, "default", {}, "\u5DF2\u8BBE\u4E3A\u65B0\u4F1A\u8BDD\u7684\u5168\u5C40\u9ED8\u8BA4\u9879\u76EE\u3002").catch((e) => say(e.message, true)) },
            targetWorkspace ? cfg.workspace_defaults?.[targetWorkspace] === p.id ? { label: "\u6E05\u9664\u5DE5\u4F5C\u533A\u9ED8\u8BA4", disabled: !p.published_version, onClick: () => rowAct(p, "workspace_default", { workspace: targetWorkspace, clear: true }, "\u5DF2\u6E05\u9664\u8BE5\u5DE5\u4F5C\u533A\u7684\u9ED8\u8BA4\u9879\u76EE\u8BBE\u7F6E\u3002").catch((e) => say(e.message, true)) } : { label: isHostWs ? "\u8BBE\u4E3A\u5F53\u524D\u5DE5\u4F5C\u533A\u9ED8\u8BA4" : "\u8BBE\u4E3A\u6240\u9009\u5DE5\u4F5C\u533A\u9ED8\u8BA4", disabled: !p.published_version, onClick: () => rowAct(p, "workspace_default", { workspace: targetWorkspace, business_project: p.id }, "\u5DF2\u8BBE\u4E3A\u8BE5\u5DE5\u4F5C\u533A\u7684\u9ED8\u8BA4\u9879\u76EE\u3002").catch((e) => say(e.message, true)) } : { label: "\u8BBE\u7F6E\u5DE5\u4F5C\u533A\u9ED8\u8BA4\u2026", disabled: !p.published_version, onClick: () => setDialog({ kind: "workspace_default", project: p }) },
            p.role === "admin" && { label: "\u5220\u9664", danger: true, onClick: () => setDialog({ kind: "delete", project: p }) }
          ] }) }
        ] })),
        (cfg.archived_projects || []).length > 0 && h(Card, { title: "\u5DF2\u5220\u9664\u9879\u76EE" }, h(Table, { rowKey: (p) => p.id, rows: cfg.archived_projects, columns: [{ key: "name", title: "\u9879\u76EE" }, { key: "ops", title: "\u64CD\u4F5C", width: "160px", wrap: true, render: (p) => h(RowActions, { items: [{ label: "\u6062\u590D\u4E3A\u8349\u7A3F", onClick: () => rowAct({ id: p.id }, "restore", { business_project: p.id }, "\u9879\u76EE\u5DF2\u6062\u590D\u4E3A\u8349\u7A3F\uFF0C\u8BF7\u6838\u5BF9\u6570\u636E\u8303\u56F4\u540E\u53D1\u5E03\u3002").catch((e) => say(e.message, true)) }] }) }] })),
        dialog?.kind === "create" && h(EditDialog, { title: "\u65B0\u5EFA\u4E1A\u52A1\u9879\u76EE", initial: { name: "" }, validate: (v) => !v.name.trim() && "\u8BF7\u586B\u5199\u9879\u76EE\u540D\u79F0\u3002", onClose: () => setDialog(null), onSave: async (v) => {
          const r = await request("business_update", { action: "create", expected_revision: cfg.revision, name: v.name.trim() });
          await reload(r?.selected_project);
          say("\u9879\u76EE\u5DF2\u521B\u5EFA\uFF0C\u8BF7\u914D\u7F6E\u6570\u636E\u8303\u56F4\u540E\u53D1\u5E03\u3002");
          go("scope");
        }, render: (v, set) => h(Text, { label: "\u9879\u76EE\u540D\u79F0", value: v.name, onChange: (name) => set({ name }) }) }),
        dialog?.kind === "edit" && h(EditDialog, { title: "\u7F16\u8F91\u9879\u76EE\u4FE1\u606F", initial: { name: dialog.project.name, description: dialog.project.description || "" }, validate: (v) => !v.name.trim() && "\u8BF7\u586B\u5199\u9879\u76EE\u540D\u79F0\u3002", onClose: () => setDialog(null), onSave: (v) => rowAct(dialog.project, "metadata", { name: v.name.trim(), description: v.description }, "\u9879\u76EE\u4FE1\u606F\u5DF2\u4FDD\u5B58\u3002"), render: (v, set) => h(React.Fragment, null, h(Text, { label: "\u9879\u76EE\u540D\u79F0", value: v.name, onChange: (name) => set({ name }) }), h(Area, { label: "\u9879\u76EE\u8BF4\u660E", value: v.description, rows: 3, onChange: (description) => set({ description }) })) }),
        dialog?.kind === "workspace_default" && h(EditDialog, {
          title: "\u8BBE\u7F6E\u300C" + dialog.project.name + "\u300D\u7684\u5DE5\u4F5C\u533A\u9ED8\u8BA4",
          initial: { workspace: targetWorkspace || "" },
          validate: (v) => !v.workspace.trim() && "\u8BF7\u586B\u5199\u5DE5\u4F5C\u533A\u8DEF\u5F84\u3002",
          onClose: () => setDialog(null),
          onSave: (v) => rowAct(dialog.project, "workspace_default", { workspace: v.workspace.trim(), business_project: dialog.project.id }, "\u5DF2\u5C06\u9879\u76EE\u7ED1\u5B9A\u4E3A\u8BE5\u5DE5\u4F5C\u533A\u7684\u9ED8\u8BA4\u9879\u76EE\u3002"),
          render: (v, set) => h(
            React.Fragment,
            null,
            wsOptions.length > 0 && h(Pick, { label: "\u9009\u62E9\u5DF2\u6709\u5DE5\u4F5C\u533A", value: wsOptions.some((o) => o.id === v.workspace) ? v.workspace : "", items: [{ id: "", label: "\u624B\u52A8\u8F93\u5165\u8DEF\u5F84\u2026" }, ...wsOptions], onChange: (id) => {
              if (id) set({ workspace: id });
            } }),
            h(Text, { label: "\u5DE5\u4F5C\u533A\u8DEF\u5F84", value: v.workspace, placeholder: "/path/to/workspace", onChange: (workspace) => set({ workspace }) }),
            Object.entries(cfg.workspace_defaults || {}).filter(([, bid]) => bid === dialog.project.id).map(([ws]) => h("div", { key: ws, className: "nwc-actions nwc-pad" }, h("span", { className: "nwc-sub" }, "\u5DF2\u7ED1\u5B9A\uFF1A" + ws), h(Button, { size: "sm", variant: "outline", onClick: () => {
              rowAct(dialog.project, "workspace_default", { workspace: ws, clear: true }, "\u5DF2\u6E05\u9664\u5DE5\u4F5C\u533A\u300C" + ws + "\u300D\u7684\u9ED8\u8BA4\u8BBE\u7F6E\u3002").then(() => setDialog(null)).catch((e) => say(e.message, true));
            } }, "\u6E05\u9664\u6B64\u7ED1\u5B9A"))),
            h("p", { className: "nwc-sub nwc-pad" }, "\u8BBE\u7F6E\u540E\uFF0C\u5728\u8BE5\u5DE5\u4F5C\u533A\u4E2D\u65B0\u5EFA\u4F1A\u8BDD\u5C06\u4F18\u5148\u4F7F\u7528\u6B64\u4E1A\u52A1\u9879\u76EE\u3002\u4E0D\u8986\u76D6\u5168\u5C40\u9ED8\u8BA4\uFF0C\u4E0D\u57FA\u4E8E\u5386\u53F2\u4F1A\u8BDD\u81EA\u52A8\u63A8\u65AD\u3002")
          )
        }),
        dialog?.kind === "delete" && h(Confirm, { title: "\u5220\u9664\u9879\u76EE\u300C" + dialog.project.name + "\u300D", description: "\u5220\u9664\u540E\u505C\u6B62\u65B0\u67E5\u8BE2\u5E76\u6E05\u9664\u9ED8\u8BA4\u9009\u62E9\uFF1B\u5386\u53F2\u7ED3\u679C\u4FDD\u7559\uFF0C\u53EF\u5728\u300C\u5DF2\u5220\u9664\u9879\u76EE\u300D\u4E2D\u6062\u590D\u4E3A\u8349\u7A3F\u3002", confirmLabel: "\u5220\u9664\u9879\u76EE", onClose: () => setDialog(null), onConfirm: () => rowAct(dialog.project, "delete", { confirm_delete: true }, "\u9879\u76EE\u5DF2\u5220\u9664\u3002") })
      );
    }
    function Scope({ cfg, project, admin, saveDraft, act, say, go }) {
      const draft = draftOf(project), [dialog, setDialog] = React.useState(null), [q, setQ] = React.useState("");
      const rows = cfg.sources.filter((s) => !q || s.name.includes(q));
      function bind(s, on) {
        return saveDraft((d) => ({ ...d, source_ids: on ? [...d.source_ids, s.id] : d.source_ids.filter((id) => id !== s.id), tables: on ? { ...d.tables, [s.id]: s.tables.map((t) => t.name) } : Object.fromEntries(Object.entries(d.tables).filter(([id]) => id !== s.id)), examples: on ? d.examples : d.examples.filter((r) => r.source_id !== s.id), terms: on ? d.terms : d.terms.filter((r) => (r.config?.source_id || r.source_id) !== s.id), mappings: on ? d.mappings || [] : (d.mappings || []).filter((r) => r.source_id !== s.id) }), on ? "\u6570\u636E\u6E90\u5DF2\u63A5\u5165\uFF0C\u53EF\u4EE5\u67E5\u8BE2\u3002" : "\u6570\u636E\u6E90\u5DF2\u79FB\u9664\uFF0C\u67E5\u8BE2\u6388\u6743\u5DF2\u64A4\u9500\u3002");
      }
      return h(
        React.Fragment,
        null,
        h(ConfigurationDiagnostics, { project, enabled: admin }),
        h(Toolbar, null, h(Search, { value: q, onChange: setQ, placeholder: "\u641C\u7D22\u6570\u636E\u6E90" }), cfg.system_admin && h(Button, { size: "sm", variant: "outline", onClick: () => go("connections") }, "\u7BA1\u7406\u6570\u636E\u6E90\u8FDE\u63A5")),
        h(Card, null, h(Table, { rowKey: (s) => s.id, rows, empty: "\u8FD8\u6CA1\u6709\u53EF\u7528\u7684\u6570\u636E\u6E90", columns: [
          { key: "name", title: "\u6570\u636E\u6E90", render: (s) => h("div", null, h(NameLine, { text: s.name }, s.synthetic && h(Tag, { tone: "neutral" }, "\u6A21\u62DF\u6570\u636E")), h("div", { className: "nwc-sub" }, s.dialect + (s.enabled === false ? " \xB7 \u8FDE\u63A5\u5DF2\u505C\u7528" : ""))) },
          { key: "state", title: "\u72B6\u6001", optional: true, width: "100px", render: (s) => draft.source_ids.includes(s.id) ? h(Tag, { tone: "success" }, "\u5DF2\u63A5\u5165") : h(Tag, { tone: "quiet" }, "\u672A\u63A5\u5165") },
          { key: "tables", title: "\u6388\u6743\u8868", width: "120px", render: (s) => draft.source_ids.includes(s.id) ? (draft.tables[s.id] || []).length + " / " + s.table_count : "\u2014" },
          { key: "ops", title: "\u64CD\u4F5C", width: "220px", wrap: true, render: (s) => {
            const on = draft.source_ids.includes(s.id);
            return h(RowActions, { items: [
              on && { label: admin ? "\u9009\u62E9\u6388\u6743\u8868" : "\u67E5\u770B\u6388\u6743\u8868", onClick: () => setDialog({ kind: "tables", source: s }) },
              on && { label: "\u6D4B\u8BD5\u8FDE\u63A5", onClick: () => request("test", { business_project: project.id, source_id: s.id }).then((v) => say(v?.message || "\u8FDE\u63A5\u6D4B\u8BD5\u5B8C\u6210\u3002")).catch((e) => say(e.message, true)) },
              admin && !on && { label: "\u63A5\u5165", onClick: () => bind(s, true).catch((e) => say(e.message, true)) },
              admin && on && { label: "\u79FB\u9664", danger: true, onClick: () => setDialog({ kind: "remove", source: s }) }
            ] });
          } }
        ] })),
        dialog?.kind === "tables" && h(TablePicker, { source: dialog.source, selected: draft.tables[dialog.source.id] || [], editable: admin, onClose: () => setDialog(null), onSave: (names) => saveDraft((d) => ({ ...d, tables: { ...d.tables, [dialog.source.id]: names } }), "\u6388\u6743\u8868\u5DF2\u4FDD\u5B58\u5230\u8349\u7A3F\u3002") }),
        dialog?.kind === "remove" && h(Confirm, { title: "\u79FB\u9664\u6570\u636E\u6E90\u300C" + dialog.source.name + "\u300D", description: "\u540C\u65F6\u79FB\u9664\u8BE5\u6570\u636E\u6E90\u7684\u6388\u6743\u8868\u3001\u5B57\u6BB5\u6620\u5C04\u3001\u67E5\u8BE2\u793A\u4F8B\u548C\u7ED3\u6784\u5316\u6307\u6807\uFF0C\u7ACB\u5373\u64A4\u9500\u67E5\u8BE2\u6388\u6743\uFF1B\u5386\u53F2\u7ED3\u679C\u4FDD\u7559\u3002", confirmLabel: "\u79FB\u9664\u6570\u636E\u6E90", onClose: () => setDialog(null), onConfirm: () => bind(dialog.source, false) })
      );
    }
    function TablePicker({ source, selected, editable, onSave, onClose }) {
      const [picked, setPicked] = React.useState(() => new Set(selected)), [q, setQ] = React.useState("");
      const all = source.tables.map((t) => t.name), missing = selected.filter((n) => !all.includes(n));
      const shown = source.tables.filter((t) => !q || t.name.toLowerCase().includes(q.toLowerCase()) || (t.label || "").includes(q));
      const toggle = (name, on) => setPicked((p) => {
        const n = new Set(p);
        on ? n.add(name) : n.delete(name);
        return n;
      });
      return h(EditDialog, { title: "\u6388\u6743\u8868 \xB7 " + source.name, initial: {}, wide: true, saveLabel: "\u4FDD\u5B58\u6388\u6743\u8868", onClose, onSave: () => {
        if (!editable) return;
        return onSave([...picked].filter((n) => all.includes(n) || missing.includes(n)));
      }, render: () => h(
        React.Fragment,
        null,
        h(Toolbar, null, h(Search, { value: q, onChange: setQ, placeholder: "\u641C\u7D22\u8868\u540D\u6216\u8BF4\u660E" }), h("span", { className: "nwc-sub" }, "\u5DF2\u9009 " + [...picked].filter((n) => all.includes(n)).length + " / " + all.length), editable && h(Button, { size: "sm", variant: "ghost", onClick: () => setPicked((p) => /* @__PURE__ */ new Set([...p, ...shown.map((t) => t.name)])) }, "\u5168\u9009"), editable && h(Button, { size: "sm", variant: "ghost", onClick: () => setPicked((p) => {
          const n = new Set(p);
          shown.forEach((t) => n.delete(t.name));
          return n;
        }) }, "\u5168\u4E0D\u9009")),
        missing.length > 0 && h("p", { className: "nwc-error" }, "\u4EE5\u4E0B\u8868\u5728\u6E90\u5E93\u4E2D\u5DF2\u4E0D\u5B58\u5728\uFF0C\u4FDD\u5B58\u65F6\u4F1A\u4FDD\u7559\u52FE\u9009\uFF0C\u8BF7\u53D6\u6D88\u6388\u6743\uFF1A" + missing.join("\u3001")),
        h("div", { className: "nwc-check-list" }, shown.map((t) => h("div", { key: t.name, className: "nwc-check-row", title: t.label || t.name }, h(Checkbox, { label: t.name, checked: picked.has(t.name), disabled: !editable, onChange: (on) => toggle(t.name, on) }), t.label && h("span", { className: "nwc-sub nwc-ellipsis" }, t.label))))
      ) });
    }
    function Members({ project, act }) {
      const [dialog, setDialog] = React.useState(null);
      const labels = { viewer: "\u4F7F\u7528\u8005\uFF08\u53EF\u54A8\u8BE2\uFF09", editor: "\u7F16\u8F91\u8005\uFF08\u53EF\u4FEE\u6539\u914D\u7F6E\uFF09", admin: "\u9879\u76EE\u7BA1\u7406\u5458" };
      const rows = Object.entries(project.members || {}).map(([user, role]) => ({ user, role }));
      const admins = rows.filter((r) => r.role === "admin").length;
      return h(
        React.Fragment,
        null,
        h(Toolbar, null, h("span", { className: "nwc-sub" }, "\u4F7F\u7528\u8D26\u6237\u7CFB\u7EDF\u63D0\u4F9B\u7684\u8EAB\u4EFD\u6807\u8BC6\u6388\u6743\uFF0C\u6DFB\u52A0\u6807\u8BC6\u4E0D\u4F1A\u521B\u5EFA\u767B\u5F55\u8D26\u6237\u3002"), h(Button, { size: "sm", variant: "primary", onClick: () => setDialog({ user: "", role: "viewer", isNew: true }) }, icon("IconPlusOutlineMedium"), "\u6DFB\u52A0\u6210\u5458")),
        h(Card, null, h(Table, { rowKey: (r) => r.user, rows, empty: "\u8FD8\u6CA1\u6709\u6210\u5458", columns: [
          { key: "user", title: "\u8EAB\u4EFD\u6807\u8BC6" },
          { key: "role", title: "\u89D2\u8272", width: "140px", render: (r) => labels[r.role] || r.role },
          { key: "ops", title: "\u64CD\u4F5C", width: "200px", wrap: true, render: (r) => h(RowActions, { items: [{ label: "\u4FEE\u6539\u89D2\u8272", onClick: () => setDialog({ ...r }) }, { label: "\u79FB\u9664", danger: true, disabled: r.role === "admin" && admins === 1, onClick: () => setDialog({ ...r, remove: true }) }] }) }
        ] })),
        dialog && !dialog.remove && h(EditDialog, { title: dialog.isNew ? "\u6DFB\u52A0\u6210\u5458" : "\u4FEE\u6539\u89D2\u8272", initial: dialog, validate: (v) => !v.user.trim() && "\u8BF7\u586B\u5199\u8EAB\u4EFD\u6807\u8BC6\u3002", onClose: () => setDialog(null), onSave: (v) => act("member", { user: v.user.trim(), role: v.role }, "\u6210\u5458\u6388\u6743\u5DF2\u4FDD\u5B58\u3002"), render: (v, set) => h(React.Fragment, null, h(Text, { label: "\u8EAB\u4EFD\u6807\u8BC6", value: v.user, disabled: !dialog.isNew, placeholder: "\u4F8B\u5982 local:\u7528\u6237\u540D", onChange: (user) => set({ user }) }), h(Pick, { label: "\u89D2\u8272", value: v.role, items: Object.entries(labels).map(([id, label]) => ({ id, label })), onChange: (role) => set({ role }) })) }),
        dialog?.remove && h(Confirm, { title: "\u79FB\u9664\u6210\u5458\u300C" + dialog.user + "\u300D", description: "\u79FB\u9664\u540E\u8BE5\u8EAB\u4EFD\u4E0D\u80FD\u518D\u4F7F\u7528\u6B64\u9879\u76EE\u3002", confirmLabel: "\u79FB\u9664\u6210\u5458", onClose: () => setDialog(null), onConfirm: () => act("member", { user: dialog.user, role: "remove" }, "\u6210\u5458\u5DF2\u79FB\u9664\u3002") })
      );
    }
    const KINDS = [["table", "\u8868\u4E1A\u52A1\u540D"], ["field", "\u5B57\u6BB5\u4E1A\u52A1\u540D"], ["term", "\u672F\u8BED\u4E0E\u540C\u4E49\u8BCD"], ["enum", "\u679A\u4E3E\u5BF9\u7167"], ["time", "\u65F6\u95F4\u8BED\u4E49"], ["relation", "\u8868\u5173\u7CFB"]];
    const kindLabel = (k) => KINDS.find((x) => x[0] === k)?.[1] || k;
    function Mappings(props) {
      const [tab, setTab] = React.useState("mappings");
      return h(
        React.Fragment,
        null,
        h(ConfigurationDiagnostics, { project: props.project, enabled: props.editor }),
        h("div", { className: "nwc-tabs" }, h(UI.SegmentedControl, { value: tab, label: "\u672F\u8BED\u4E0E\u5B57\u6BB5\u6620\u5C04", options: [{ value: "mappings", label: "\u5B57\u6BB5\u6620\u5C04" }, { value: "terms", label: "\u4E1A\u52A1\u672F\u8BED" }], onChange: setTab })),
        tab === "mappings" ? h(MappingList, props) : h(TermList, props)
      );
    }
    function MappingList({ cfg, project, editor, saveDraft, say }) {
      const draft = draftOf(project), [q, setQ] = React.useState(""), [kind, setKind] = React.useState("all"), [dialog, setDialog] = React.useState(null);
      const sources = cfg.sources.filter((s) => draft.source_ids.includes(s.id));
      const tablesOf = (sid) => (sources.find((s) => s.id === sid)?.tables || []).filter((t) => (draft.tables[sid] || []).includes(t.name));
      const sourceName = (sid) => sources.find((s) => s.id === sid)?.name || sid;
      const rows = (draft.mappings || []).filter((r) => (kind === "all" || r.kind === kind) && (!q || [r.name, r.description, r.table, r.column, ...r.synonyms || []].join(" ").toLowerCase().includes(q.toLowerCase())));
      const short = (t) => String(t || "").split(".").pop();
      const place = (r) => r.kind === "relation" ? short(r.table) + "(" + (r.from_columns || []).join(",") + ") \u2192 " + short(r.to_table) + "(" + (r.to_columns || []).join(",") + ")" : short(r.table) + (r.column ? "." + r.column : "");
      const detail = (r) => r.kind === "enum" ? (r.values || []).map((v) => v.value + "=" + v.label).join("\uFF0C") : r.kind === "time" ? { date: "\u65E5", month: "\u6708", year: "\u5E74", datetime: "\u65E5\u671F\u65F6\u95F4" }[r.grain] || r.grain : (r.synonyms || []).join("\uFF0C");
      function blank() {
        const s = sources[0], t = s && tablesOf(s.id)[0];
        return { id: uid(), kind: "field", name: "", description: "", source_id: s?.id || "", table: t?.name || "", column: "", synonyms: [], values: [], grain: "date", to_table: "", from_columns: [], to_columns: [], cardinality: "many_to_one" };
      }
      function clean(v) {
        const base = { id: v.id, kind: v.kind, name: v.name.trim(), description: v.description || "", source_id: v.source_id, table: v.table };
        if (!["table", "relation"].includes(v.kind)) base.column = v.column;
        if (["table", "field", "term"].includes(v.kind)) base.synonyms = v.synonyms || [];
        if (v.kind === "enum") base.values = (v.values || []).filter((x) => String(x.value).trim() !== "" || x.label).map((x) => ({ value: x.value, label: x.label, ...x.type && x.type !== "text" ? { type: x.type } : {} }));
        if (v.kind === "time") base.grain = v.grain;
        if (v.kind === "relation") Object.assign(base, { to_table: v.to_table, from_columns: v.from_columns, to_columns: v.to_columns, cardinality: v.cardinality });
        return base;
      }
      return h(
        React.Fragment,
        null,
        h(
          Toolbar,
          null,
          h(Search, { value: q, onChange: setQ, placeholder: "\u641C\u7D22\u540D\u79F0\u3001\u540C\u4E49\u8BCD\u3001\u8868\u6216\u5B57\u6BB5" }),
          h(FilterMenu, { label: "\u7C7B\u578B", value: kind, onChange: setKind, items: [["all", "\u5168\u90E8\u7C7B\u578B"], ...KINDS].map(([id, label]) => ({ id, label: label + "\uFF08" + (id === "all" ? (draft.mappings || []).length : (draft.mappings || []).filter((r) => r.kind === id).length) + "\uFF09" })) }),
          editor && h(Button, { size: "sm", variant: "primary", disabled: !sources.length, onClick: () => setDialog({ row: blank(), isNew: true }) }, icon("IconPlusOutlineMedium"), "\u65B0\u589E\u6620\u5C04")
        ),
        h(Card, null, h(Table, { rowKey: (r) => r.id, rows, empty: (draft.mappings || []).length ? "\u6CA1\u6709\u5339\u914D\u7684\u6620\u5C04" : "\u8FD8\u6CA1\u6709\u5B57\u6BB5\u6620\u5C04", columns: [
          { key: "kind", title: "\u7C7B\u578B", optional: true, width: "110px", render: (r) => h(Tag, { tone: "neutral" }, kindLabel(r.kind)) },
          { key: "name", title: "\u4E1A\u52A1\u540D\u79F0", render: (r) => h("div", null, h("div", { className: "nwc-strong", title: r.name }, r.name || "\u672A\u547D\u540D"), r.description && h("div", { className: "nwc-sub", title: r.description }, r.description)) },
          { key: "place", title: "\u5BF9\u5E94\u4F4D\u7F6E", render: (r) => h("span", { className: "nwc-mono", title: sourceName(r.source_id) + " \xB7 " + r.table + (r.column ? "." + r.column : "") }, place(r)) },
          { key: "detail", title: "\u540C\u4E49\u8BCD / \u53D6\u503C", optional: true, render: (r) => h("span", { title: detail(r) }, detail(r) || "\u2014") },
          { key: "ops", title: "\u64CD\u4F5C", width: "150px", wrap: true, render: (r) => h(RowActions, { items: [{ label: editor ? "\u7F16\u8F91" : "\u67E5\u770B", onClick: () => setDialog({ row: r }) }, editor && { label: "\u5220\u9664", danger: true, onClick: () => setDialog({ row: r, remove: true }) }] }) }
        ] })),
        dialog && !dialog.remove && h(EditDialog, {
          title: (dialog.isNew ? "\u65B0\u589E" : editor ? "\u7F16\u8F91" : "\u67E5\u770B") + "\u6620\u5C04",
          initial: { ...blank(), ...dialog.row },
          wide: true,
          saveLabel: editor ? "\u4FDD\u5B58" : "\u5173\u95ED",
          validate: (v) => !editor ? null : !v.name.trim() ? "\u8BF7\u586B\u5199\u4E1A\u52A1\u540D\u79F0\u3002" : !v.table ? "\u8BF7\u9009\u62E9\u7269\u7406\u8868\u3002" : !["table", "relation"].includes(v.kind) && !v.column ? "\u8BF7\u9009\u62E9\u7269\u7406\u5B57\u6BB5\u3002" : v.kind === "relation" && (!v.to_table || !v.from_columns.length || v.from_columns.length !== v.to_columns.length) ? "\u8BF7\u586B\u5199\u5173\u8054\u76EE\u6807\u8868\u548C\u7B49\u957F\u7684\u5173\u8054\u5B57\u6BB5\u3002" : null,
          onClose: () => setDialog(null),
          onSave: (v) => {
            if (!editor) return;
            const row = clean(v);
            return saveDraft((d) => ({ ...d, mappings: dialog.isNew ? [...d.mappings || [], row] : (d.mappings || []).map((r) => r.id === row.id ? row : r) }));
          },
          render: (v, set) => h(MappingForm, { v, set, sources, tablesOf, disabled: !editor, isNew: dialog.isNew })
        }),
        dialog?.remove && h(Confirm, { title: "\u5220\u9664\u6620\u5C04\u300C" + (dialog.row.name || "\u672A\u547D\u540D") + "\u300D", description: "\u5220\u9664\u540E\u5BF9\u8BDD\u4E0D\u518D\u4F7F\u7528\u8FD9\u6761\u6620\u5C04\u3002", confirmLabel: "\u5220\u9664\u6620\u5C04", onClose: () => setDialog(null), onConfirm: () => saveDraft((d) => ({ ...d, mappings: (d.mappings || []).filter((r) => r.id !== dialog.row.id) }), "\u6620\u5C04\u5DF2\u5220\u9664\u3002") })
      );
    }
    function MappingForm({ v, set, sources, tablesOf, disabled, isNew }) {
      const tables = tablesOf(v.source_id), table2 = tables.find((t) => t.name === v.table), columns = table2?.columns || [];
      const tableItems = tables.map((t) => ({ id: t.name, label: t.name + (t.label ? " \xB7 " + t.label : "") })), columnItems = columns.map((c) => ({ id: c.name, label: c.name + (c.label && c.label !== c.name ? " \xB7 " + c.label : "") }));
      const list = (s) => String(s || "").split(/[,，、\s]+/).map((x) => x.trim()).filter(Boolean);
      return h(
        "div",
        { className: "nwc-form-grid" },
        h(Pick, { label: "\u6620\u5C04\u7C7B\u578B", value: v.kind, disabled: disabled || !isNew, items: KINDS.map(([id, label]) => ({ id, label })), onChange: (kind) => set({ kind }) }),
        h(Text, { label: "\u4E1A\u52A1\u540D\u79F0", value: v.name, disabled, placeholder: "\u4E1A\u52A1\u4EBA\u5458\u4F7F\u7528\u7684\u53EB\u6CD5", onChange: (name) => set({ name }) }),
        h(Pick, { label: "\u6570\u636E\u6E90", value: v.source_id, disabled, items: sources.map((s) => ({ id: s.id, label: s.name })), onChange: (source_id) => set({ source_id, table: "", column: "", to_table: "", from_columns: [], to_columns: [] }) }),
        h(Pick, { label: "\u7269\u7406\u8868", value: v.table, disabled, items: tableItems, placeholder: tables.length ? "\u9009\u62E9\u8868" : "\u8BE5\u6570\u636E\u6E90\u6CA1\u6709\u6388\u6743\u8868", onChange: (t) => set({ table: t, column: "", from_columns: [] }) }),
        !["table", "relation"].includes(v.kind) && h(Pick, { label: "\u7269\u7406\u5B57\u6BB5", value: v.column, disabled: disabled || !table2, items: columnItems, placeholder: table2 ? "\u9009\u62E9\u5B57\u6BB5" : "\u5148\u9009\u62E9\u8868", onChange: (column) => set({ column }) }),
        ["table", "field", "term"].includes(v.kind) && h(Text, { label: "\u540C\u4E49\u8BCD", value: (v.synonyms || []).join("\uFF0C"), disabled, placeholder: "\u591A\u4E2A\u53EB\u6CD5\u7528\u9017\u53F7\u5206\u9694", onChange: (s) => set({ synonyms: list(s) }) }),
        v.kind === "time" && h(Pick, { label: "\u65F6\u95F4\u7C92\u5EA6", value: v.grain, disabled, items: [{ id: "date", label: "\u65E5" }, { id: "month", label: "\u6708" }, { id: "year", label: "\u5E74" }, { id: "datetime", label: "\u65E5\u671F\u65F6\u95F4" }], onChange: (grain2) => set({ grain: grain2 }) }),
        v.kind === "relation" && h(
          React.Fragment,
          null,
          h(Pick, { label: "\u5173\u8054\u76EE\u6807\u8868", value: v.to_table, disabled, items: tableItems, onChange: (to_table) => set({ to_table, to_columns: [] }) }),
          h(Text, { label: "\u672C\u8868\u5173\u8054\u5B57\u6BB5", value: (v.from_columns || []).join(","), disabled, placeholder: "\u590D\u5408\u952E\u7528\u9017\u53F7\u5206\u9694", onChange: (s) => set({ from_columns: list(s) }) }),
          h(Text, { label: "\u76EE\u6807\u8868\u5173\u8054\u5B57\u6BB5", value: (v.to_columns || []).join(","), disabled, placeholder: "\u4E0E\u672C\u8868\u5B57\u6BB5\u4E00\u4E00\u5BF9\u5E94", onChange: (s) => set({ to_columns: list(s) }) }),
          h(Pick, { label: "\u5BF9\u5E94\u5173\u7CFB", value: v.cardinality, disabled, items: [{ id: "many_to_one", label: "\u591A\u5BF9\u4E00" }, { id: "one_to_one", label: "\u4E00\u5BF9\u4E00" }, { id: "one_to_many", label: "\u4E00\u5BF9\u591A" }], onChange: (cardinality) => set({ cardinality }) })
        ),
        h("div", { className: "nwc-span" }, h(Area, { label: "\u8BF4\u660E", value: v.description, disabled, rows: 2, onChange: (description) => set({ description }) })),
        v.kind === "enum" && h("div", { className: "nwc-span" }, h(EnumEditor, { values: v.values || [], disabled, onChange: (values) => set({ values }) }))
      );
    }
    function EnumEditor({ values, onChange, disabled }) {
      const change = (i, patch) => onChange(values.map((x, j) => j === i ? { ...x, ...patch } : x));
      return h(
        "div",
        { className: "nwc-enum" },
        h("div", { className: "nwc-field-label" }, "\u53D6\u503C\u5BF9\u7167\uFF08\u539F\u59CB\u503C \u2192 \u4E1A\u52A1\u540D\u79F0\uFF09"),
        values.map((x, i) => h("div", { key: i, className: "nwc-enum-row" }, h(Input, { "aria-label": "\u539F\u59CB\u503C " + (i + 1), value: String(x.value ?? ""), disabled, placeholder: "\u539F\u59CB\u503C", onChange: (e) => change(i, { value: e.target.value }) }), h("span", null, "\u2192"), h(Input, { "aria-label": "\u4E1A\u52A1\u540D\u79F0 " + (i + 1), value: x.label || "", disabled, placeholder: "\u4E1A\u52A1\u540D\u79F0", onChange: (e) => change(i, { label: e.target.value }) }), !disabled && h(Button, { size: "sm", variant: "ghost", onClick: () => onChange(values.filter((_, j) => j !== i)) }, "\u5220\u9664"))),
        !disabled && h(Button, { size: "sm", variant: "outline", onClick: () => onChange([...values, { value: "", label: "" }]) }, icon("IconPlusOutlineMedium"), "\u6DFB\u52A0\u53D6\u503C")
      );
    }
    function TermList({ cfg, project, editor, saveDraft }) {
      const draft = draftOf(project), [q, setQ] = React.useState(""), [dialog, setDialog] = React.useState(null);
      const projectSources = cfg.sources.filter((s) => draft.source_ids.includes(s.id)), scopeName = (id) => id ? cfg.sources.find((s) => s.id === id)?.name || id : "\u5168\u90E8\u6570\u636E\u5E93";
      const terms2 = draft.terms.filter((r) => !isMetric(r) && !project.review_reasons?.[r.id] && r.status !== "review"), rows = terms2.filter((r) => !q || (r.name + " " + r.definition).includes(q));
      return h(
        React.Fragment,
        null,
        h(Toolbar, null, h(Search, { value: q, onChange: setQ, placeholder: "\u641C\u7D22\u672F\u8BED" }), editor && h(Button, { size: "sm", variant: "primary", onClick: () => setDialog({ row: { id: uid(), name: "", definition: "" }, isNew: true }) }, icon("IconPlusOutlineMedium"), "\u65B0\u589E\u672F\u8BED")),
        h(Card, null, h(Table, { rowKey: (r) => r.id, rows, empty: terms2.length ? "\u6CA1\u6709\u5339\u914D\u7684\u672F\u8BED" : "\u8FD8\u6CA1\u6709\u4E1A\u52A1\u672F\u8BED", columns: [
          { key: "name", title: "\u672F\u8BED", width: "200px", render: (r) => h("span", { className: "nwc-strong", title: r.name }, r.name || "\u672A\u547D\u540D") },
          { key: "scope", title: "\u9002\u7528\u6570\u636E\u5E93", optional: true, width: "150px", render: (r) => h("span", { title: scopeName(r.source_id) }, scopeName(r.source_id)) },
          { key: "definition", title: "\u5B9A\u4E49\u4E0E\u53E3\u5F84", render: (r) => h("span", { title: r.definition }, r.definition) },
          { key: "ops", title: "\u64CD\u4F5C", width: "150px", wrap: true, render: (r) => h(RowActions, { items: [{ label: editor ? "\u7F16\u8F91" : "\u67E5\u770B", onClick: () => setDialog({ row: r }) }, editor && { label: "\u5220\u9664", danger: true, onClick: () => setDialog({ row: r, remove: true }) }] }) }
        ] })),
        dialog && !dialog.remove && h(EditDialog, {
          title: (dialog.isNew ? "\u65B0\u589E" : "\u7F16\u8F91") + "\u672F\u8BED",
          initial: dialog.row,
          wide: true,
          saveLabel: editor ? "\u4FDD\u5B58" : "\u5173\u95ED",
          validate: (v) => editor && !v.name.trim() && "\u8BF7\u586B\u5199\u672F\u8BED\u540D\u79F0\u3002",
          onClose: () => setDialog(null),
          onSave: (v) => {
            if (!editor) return;
            const row = { ...dialog.row, name: v.name.trim(), definition: v.definition, source_id: v.source_id || "" };
            if (!row.source_id) delete row.source_id;
            return saveDraft((d) => ({ ...d, terms: dialog.isNew ? [...d.terms, row] : d.terms.map((r) => r.id === row.id ? row : r) }));
          },
          render: (v, set) => h(React.Fragment, null, h("div", { className: "nwc-form-grid" }, h(Text, { label: "\u672F\u8BED", value: v.name, disabled: !editor, onChange: (name) => set({ name }) }), h(Pick, { label: "\u9002\u7528\u6570\u636E\u5E93", value: v.source_id || "", disabled: !editor, items: [{ id: "", label: "\u5168\u90E8\u6570\u636E\u5E93 \xB7 \u5404\u5E93\u53E3\u5F84\u76F8\u540C\u65F6\u9009\u8FD9\u4E2A" }, ...projectSources.map((s) => ({ id: s.id, label: s.name + " \xB7 \u53EA\u7528\u4E8E\u8FD9\u4E2A\u5E93" }))], onChange: (source_id) => set({ source_id }) })), h(Area, { label: "\u5B9A\u4E49\u4E0E\u53E3\u5F84", value: v.definition, disabled: !editor, rows: 6, placeholder: "\u542B\u4E49\u3001\u5355\u4F4D\u3001\u8BA1\u7B97\u65B9\u5F0F\u3001\u65F6\u95F4\u89C4\u5219", onChange: (definition) => set({ definition }) }))
        }),
        dialog?.remove && h(Confirm, { title: "\u5220\u9664\u672F\u8BED\u300C" + dialog.row.name + "\u300D", confirmLabel: "\u5220\u9664\u672F\u8BED", onClose: () => setDialog(null), onConfirm: () => saveDraft((d) => ({ ...d, terms: d.terms.filter((r) => r.id !== dialog.row.id) }), "\u672F\u8BED\u5DF2\u5220\u9664\u3002") })
      );
    }
    const workbookOrigin = /^pack:chdl-2026@2\.1\.\d+$/;
    const workbookCodePattern = /^([A-Z][A-Z0-9_-]*\d[A-Z0-9_-]*)[\s:：]+/i;
    const workbookSourcePattern = /\r?\n[^\S\r\n]*唯一来源[：:]([^\r\n]+)\s*$/u;
    const workbookStates = {
      example: ["\u6709\u67E5\u8BE2\u793A\u4F8B", "info"],
      needs_history: ["\u7F3A\u5C11\u53EF\u6BD4\u5386\u53F2", "quiet"],
      no_example: ["\u793A\u4F8B\u672A\u5BFC\u5165", "quiet"],
      not_listed: ["\u5F53\u524D\u914D\u7F6E\u672A\u6536\u5F55", "quiet"]
    };
    function workbookEntries(config) {
      const entries = /* @__PURE__ */ new Map(), sourceIds = new Set(config?.source_ids || []);
      const codeFor = (row, title) => String(row.metric_code || row.code || title?.match(workbookCodePattern)?.[1] || "").trim().toUpperCase();
      const keyFor = (row, code) => row.source_id + "|" + code;
      for (const term of config?.terms || []) {
        if (!workbookOrigin.test(term.origin) || !sourceIds.has(term.source_id)) continue;
        const code = codeFor(term, term.name);
        if (!code) continue;
        const key = keyFor(term, code);
        entries.set(key, { key, code, term, sourceId: term.source_id, example: null });
      }
      for (const example of config?.examples || []) {
        if (!workbookOrigin.test(example.origin) || !example.logical_sql?.trim()) continue;
        const entry = entries.get(keyFor(example, codeFor(example, example.question)));
        if (entry) entry.example = example;
      }
      return entries;
    }
    function workbookMetricRows(project, scope) {
      const selected = scope === "draft" ? project.draft : published(project);
      const current = workbookEntries(selected);
      const known = new Map([...workbookEntries(published(project)), ...workbookEntries(project.draft)]);
      return [...known.values()].map((original) => {
        const entry = current.get(original.key);
        const { key, code, term, sourceId } = entry || original;
        const limitation = entry?.term.config?.kind === "metric-limitation" ? entry.term.config : null;
        const state = !entry ? "not_listed" : limitation ? "needs_history" : entry.example ? "example" : "no_example";
        const storedDefinition = entry?.term.definition;
        const source = storedDefinition?.match(workbookSourcePattern);
        const definition = (source ? storedDefinition.slice(0, source.index) : storedDefinition)?.trim();
        return {
          key,
          code,
          sourceId,
          state,
          name: term.name.replace(workbookCodePattern, ""),
          definition,
          provenance: source?.[1].trim(),
          unit: limitation?.unit || definition?.match(/^单位[：:]\s*([^。；\n]+)/)?.[1],
          reason: limitation?.reason,
          example: state === "example" ? entry.example : null
        };
      });
    }
    function WorkbookMetrics({ cfg, project, scope, hasChanges }) {
      const [query, setQuery] = React.useState(""), [opened, setOpened] = React.useState(null);
      const rows = workbookMetricRows(project, scope);
      const keyword = query.trim().toLocaleLowerCase();
      const filtered = rows.filter((row) => [row.code, row.name].some((value) => value.toLocaleLowerCase().includes(keyword)));
      const detail = filtered.find((row) => row.key === opened);
      const sourceName = (id) => cfg.sources.find((source) => source.id === id)?.name || id;
      const counts = Object.fromEntries(Object.keys(workbookStates).map((state) => [state, rows.filter((row) => row.state === state).length]));
      const summary = Object.entries(workbookStates).filter(([state]) => counts[state]).map(([state, [label]]) => label + " " + counts[state] + " \u9879").join(" \xB7 ");
      const scopeLabel = scope === "draft" ? hasChanges ? "\u8349\u7A3F \xB7 \u672A\u751F\u6548\u7684\u6539\u52A8\u987B\u5E94\u7528" : "\u8349\u7A3F \xB7 \u4E0E\u5DF2\u53D1\u5E03\u914D\u7F6E\u4E00\u81F4" : project.published_version ? "\u5DF2\u53D1\u5E03\u914D\u7F6E \xB7 \u7B2C " + project.published_version + " \u7248" : "\u5C1A\u672A\u53D1\u5E03";
      return h(
        React.Fragment,
        null,
        h(Toolbar, null, h(Search, { value: query, onChange: setQuery, placeholder: "\u641C\u7D22\u6307\u6807\u7F16\u7801\u6216\u540D\u79F0" })),
        h(
          Card,
          { title: "\u5185\u7F6E\u6307\u6807" },
          h("p", { className: "nwc-sub nwc-pad" }, scopeLabel + "\u3002" + summary + "\u3002\u67E5\u8BE2\u65F6\u8BF7\u6838\u5BF9\u7EDF\u8BA1\u671F\u95F4\u548C\u6570\u636E\u6E90\u3002"),
          h(Table, { rowKey: (row) => row.key, rows: filtered, empty: "\u6CA1\u6709\u5339\u914D\u7684\u5185\u7F6E\u6307\u6807", columns: [
            { key: "name", title: "\u6307\u6807", render: (row) => h("div", null, h(NameLine, { text: row.name }), h("div", { className: "nwc-sub" }, row.code)) },
            { key: "source", title: "\u6570\u636E\u6E90", optional: true, width: "150px", render: (row) => sourceName(row.sourceId) },
            { key: "state", title: "\u72B6\u6001", width: "124px", render: (row) => h(Tag, { tone: workbookStates[row.state][1] }, workbookStates[row.state][0]) },
            { key: "ops", title: "\u64CD\u4F5C", width: "110px", wrap: true, render: (row) => h(RowActions, { items: [{ label: "\u67E5\u770B\u53E3\u5F84", onClick: () => setOpened(row.key) }] }) }
          ] })
        ),
        detail && h(
          Dialog,
          { open: true, title: detail.code + " \xB7 " + detail.name, wide: true, onClose: () => setOpened(null) },
          h("p", { className: "nwc-sub" }, sourceName(detail.sourceId) + (detail.unit ? " \xB7 \u5355\u4F4D " + detail.unit : "")),
          detail.definition && h(Section, { title: "\u4E1A\u52A1\u5B9A\u4E49" }, h("p", { className: "nwc-text" }, detail.definition)),
          detail.state === "needs_history" && h("p", { className: "nwc-text" }, detail.reason + "\u8865\u9F50\u540C\u53E3\u5F84\u5386\u53F2\u540E\u518D\u6838\u7B97\u3002"),
          detail.state === "no_example" && h("p", { className: "nwc-text" }, "\u6B64\u6570\u636E\u6E90\u7684\u5F53\u524D\u914D\u7F6E\u672A\u5BFC\u5165\u5339\u914D\u7684 SQL \u793A\u4F8B\u3002\u53EF\u5728\u300C\u53E3\u5F84\u5BFC\u5165\u300D\u6838\u5BF9\u6570\u636E\u6E90\u4E0E\u65B9\u8A00\u3002"),
          detail.state === "not_listed" && h("p", { className: "nwc-text" }, "\u6240\u9009\u914D\u7F6E\u672A\u6536\u5F55\u8FD9\u9879\u5185\u7F6E\u6307\u6807\u3002"),
          detail.provenance && h(Section, { title: "\u5DE5\u4F5C\u7C3F\u6765\u6E90" }, h("p", { className: "nwc-text" }, detail.provenance)),
          detail.example && h(Area, { label: "\u53EA\u8BFB SQL \u793A\u4F8B", value: detail.example.logical_sql, disabled: true, rows: 10, mono: true })
        )
      );
    }
    function MetricPage(props) {
      const { project, hasChanges } = props;
      const live = published(project);
      const hasWorkbook = workbookEntries(project.draft).size > 0 || workbookEntries(live).size > 0;
      const [tab, setTab] = React.useState("workbook"), [selectedScope, setScope] = React.useState(null);
      if (!hasWorkbook) return h(Metrics, props);
      const scope = project.draft ? selectedScope || (hasChanges ? "draft" : "published") : "published";
      const metricCount = (config) => "\uFF08" + (config?.terms || []).filter(isMetric).length + "\uFF09";
      const views = [
        { value: "draft", label: "\u5F53\u524D\u8349\u7A3F" + (tab === "structured" ? metricCount(project.draft) : "") },
        { value: "published", label: "\u5DF2\u53D1\u5E03\u914D\u7F6E" + (tab === "structured" ? metricCount(live) : "") }
      ];
      const body = tab === "workbook" ? h(WorkbookMetrics, { ...props, scope }) : h(Metrics, {
        ...props,
        key: scope,
        configScope: scope,
        editor: props.editor && (scope === "draft" || !hasChanges)
      });
      return h(
        React.Fragment,
        null,
        h("div", { className: "nwc-tabs" }, h(UI.SegmentedControl, {
          id: "nwc-metrics",
          label: "\u6307\u6807\u53E3\u5F84",
          value: tab,
          options: [{ value: "workbook", label: "\u5185\u7F6E\u6307\u6807" }, { value: "structured", label: "\u7ED3\u6784\u5316\u6307\u6807" }],
          onChange: setTab
        })),
        h(
          "div",
          { id: "nwc-metrics-" + tab + "-panel", role: "tabpanel", "aria-labelledby": "nwc-metrics-" + tab },
          project.draft && h("div", { className: "nwc-tabs" }, h(UI.SegmentedControl, { id: "nwc-metrics-config", label: "\u6307\u6807\u914D\u7F6E", value: scope, options: views, onChange: setScope })),
          h("div", {
            id: "nwc-metrics-config-" + scope + "-panel",
            role: project.draft ? "tabpanel" : void 0,
            "aria-labelledby": project.draft ? "nwc-metrics-config-" + scope : void 0
          }, body)
        )
      );
    }
    function Metrics({ cfg, project, editor, saveDraft, say, configScope }) {
      const draft = (configScope === "published" ? published(project) : draftOf(project)) || { terms: [], source_ids: [], tables: {} };
      const [q, setQ] = React.useState(""), [dialog, setDialog] = React.useState(null);
      const reason = (r) => r && (configScope !== "published" && project.review_reasons?.[r.id] || r.origin === "excel" && r.status === "review" && (r.review_reason || "\u5F85\u590D\u6838"));
      const view = (r) => r?.config || r?.proposed_config || project.review_candidates?.[r?.id]?.proposed_config || { id: r?.code, source_id: r?.source_id, unit: r?.excel_input?.unit };
      const metrics = draft.terms.filter((r) => isMetric(r) || reason(r)), rows = metrics.filter((r) => !q || (r.name + " " + r.definition + " " + view(r).id).includes(q));
      const sourceName = (sid) => cfg.sources.find((s) => s.id === sid)?.name || sid;
      const AGG = [["sum", "\u5408\u8BA1"], ["count", "\u8BA1\u6570"], ["distinct", "\u4E0D\u540C\u503C\u8BA1\u6570"], ["avg", "\u7B97\u672F\u5E73\u5747"], ["min", "\u6700\u5C0F\u503C"], ["max", "\u6700\u5927\u503C"], ["snapshot_sum", "\u671F\u672B\u503C\u5408\u8BA1\uFF08\u5E93\u5B58\u7C7B\uFF09"], ["single_value", "\u552F\u4E00\u7EDF\u8BA1\u503C\uFF08\u91CD\u590D\u65F6\u5F85\u6838\u5BF9\uFF09"]].map(([id, label]) => ({ id, label }));
      const blankOperand = (n) => ({ id: n ? "value" + n : "value", table: "", column: "", aggregation: "sum", time_column: "", entity_columns: [], filters: [] });
      function formOf(row) {
        const c = view(row);
        return { name: row?.name || "", definition: row?.definition || "", id: c?.id || "", version: c?.version || "1.0.0", unit: c?.unit || "", source_id: c?.source_id || draft.source_ids[0] || "", time_mode: c?.time_mode || "period", coverage: (c?.coverage || []).join(", "), formula: c?.formula || "value", operands: c?.operands?.map((o) => ({ ...o, column: o.column || "", time_column: o.time_column || "" })) || [{ ...blankOperand(), table: row?.review_table || "" }], dimensions: c?.dimensions || [], relations: c?.relations || [], additive: Boolean(c?.additive) };
      }
      const sourceOf2 = (v) => cfg.sources.find((s) => s.id === v.source_id);
      const tablesOf = (v) => (sourceOf2(v)?.tables || []).filter((t) => (draft.tables[v.source_id] || []).includes(t.name));
      function check(v) {
        if (!v.name.trim() || !v.definition.trim() || !v.unit.trim()) return "\u8BF7\u586B\u5199\u6307\u6807\u540D\u79F0\u3001\u4E1A\u52A1\u5B9A\u4E49\u548C\u5355\u4F4D\u3002";
        if (!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(v.id)) return "\u6307\u6807\u6807\u8BC6\u9700\u4EE5\u5B57\u6BCD\u5F00\u5934\uFF0C\u53EA\u542B\u5B57\u6BCD\u3001\u6570\u5B57\u548C\u4E0B\u5212\u7EBF\u3002";
        if (!/^\d+\.\d+\.\d+$/.test(v.version)) return "\u7248\u672C\u8BF7\u4F7F\u7528\u4E09\u4E2A\u6570\u5B57\uFF0C\u4F8B\u5982 1.0.0\u3002";
        const coverage = v.coverage.split(/[,，\s]+/).filter(Boolean);
        if (v.time_mode === "period" && (!coverage.length || coverage.some((p) => !/^20\d{2}(-(?:0[1-9]|1[0-2]))?$/.test(p)))) return "\u8BF7\u586B\u5199\u53EF\u67E5\u8BE2\u7684\u671F\u95F4\uFF0C\u4F8B\u5982 2026 \u6216 2026-10\uFF0C\u591A\u4E2A\u7528\u9017\u53F7\u5206\u9694\u3002";
        const tables = tablesOf(v);
        if (v.operands.some((o) => !o.id || !tables.some((t) => t.name === o.table) || !o.column && o.aggregation !== "count" || v.time_mode === "period" && !o.time_column)) return "\u8BF7\u4E3A\u6BCF\u4E2A\u64CD\u4F5C\u6570\u9009\u62E9\u6388\u6743\u8868\u3001\u7EDF\u8BA1\u5B57\u6BB5" + (v.time_mode === "period" ? "\u548C\u65F6\u95F4\u5B57\u6BB5" : "") + "\u3002";
        return null;
      }
      function MetricEditor({ initial, onClose }) {
        const [observed, setObserved] = React.useState(null), [checking, setChecking] = React.useState(false);
        return h(EditDialog, {
          title: initial ? "\u7F16\u8F91\u6307\u6807\u53E3\u5F84" : "\u65B0\u589E\u6307\u6807\u53E3\u5F84",
          wide: true,
          initial: formOf(initial),
          saveLabel: editor ? reason(initial) ? "\u786E\u8BA4\u590D\u6838\u5E76\u4FDD\u5B58" : "\u4FDD\u5B58" : "\u5173\u95ED",
          onClose,
          validate: (v) => editor && check(v),
          onSave: (v) => {
            if (!editor) return;
            const src = sourceOf2(v), coverage = v.time_mode === "all" ? [] : v.coverage.split(/[,，\s]+/).filter(Boolean);
            const config = { ...view(initial), kind: "semantic-metric", id: v.id, version: v.version, source_id: v.source_id, source_revision: src?.target_revision || 0, unit: v.unit.trim(), definition: v.definition.trim(), time_mode: v.time_mode, coverage, operands: v.operands.map((o) => ({ ...o, column: o.column || null, time_column: v.time_mode === "all" ? null : o.time_column })), formula: v.formula, dimensions: v.dimensions, relations: v.relations, additive: v.additive };
            const row = { ...initial, id: initial?.id || uid(), name: v.name.trim(), definition: v.definition.trim(), config, ...reason(initial) ? { confirm_review: true } : {} };
            return saveDraft((d) => ({ ...d, terms: initial ? d.terms.map((t) => t.id === initial.id ? row : t) : [...d.terms, row] }), "\u6307\u6807\u53E3\u5F84\u5DF2\u4FDD\u5B58\u5E76\u751F\u6548\u3002");
          },
          render: (v, set) => {
            const off = !editor, tables = tablesOf(v), colsOf = (name) => tables.find((t) => t.name === name)?.columns || [];
            const op = (i, patch) => set((x) => ({ operands: x.operands.map((o, j) => j === i ? { ...o, ...patch } : o) }));
            async function inspect() {
              setChecking(true);
              setObserved(null);
              try {
                setObserved(await request("inspect_periods", { business_project: project.id, source_id: v.source_id, table: v.operands[0].table, column: v.operands[0].time_column }));
              } catch (e) {
                setObserved({ message: e.message, failed: true });
              } finally {
                setChecking(false);
              }
            }
            const dimTable = v.operands[0]?.table, dims = colsOf(dimTable);
            return h(
              React.Fragment,
              null,
              h(ConfigurationDiagnostics, { project, enabled: editor }),
              reason(initial) && h(Section, { title: "\u5F85\u590D\u6838" }, h("p", null, reason(initial)), h("p", null, initial.caliber_text)),
              h(
                Section,
                { title: "\u57FA\u672C\u4FE1\u606F" },
                h(
                  "div",
                  { className: "nwc-form-grid" },
                  h(Text, { label: "\u6307\u6807\u540D\u79F0", value: v.name, disabled: off, onChange: (name) => set({ name }) }),
                  h(Text, { label: "\u6307\u6807\u6807\u8BC6", value: v.id, disabled: off, placeholder: "\u4F8B\u5982 total_amount", onChange: (id) => set({ id }) }),
                  h(Text, { label: "\u5355\u4F4D", value: v.unit, disabled: off, placeholder: "\u4F8B\u5982 \u4E07\u5143\u3001%\u3001\u4E2A", onChange: (unit) => set({ unit }) }),
                  h(Text, { label: "\u7248\u672C", value: v.version, disabled: off, onChange: (version) => set({ version }) })
                ),
                h(Area, { label: "\u4E1A\u52A1\u5B9A\u4E49\u4E0E\u7EDF\u8BA1\u8303\u56F4", value: v.definition, disabled: off, rows: 3, onChange: (definition) => set({ definition }) })
              ),
              h(
                Section,
                { title: "\u6570\u636E\u4E0E\u65F6\u95F4" },
                h(
                  "div",
                  { className: "nwc-form-grid" },
                  h(Pick, { label: "\u6570\u636E\u6E90", value: v.source_id, disabled: off, items: cfg.sources.filter((s) => draft.source_ids.includes(s.id)).map((s) => ({ id: s.id, label: s.name })), onChange: (source_id) => set({ source_id, operands: [blankOperand()], dimensions: [], relations: [] }) }),
                  h(Pick, { label: "\u7EDF\u8BA1\u65F6\u95F4", value: v.time_mode, disabled: off, items: [{ id: "period", label: "\u6309\u671F\u95F4\u7EDF\u8BA1" }, { id: "all", label: "\u5168\u90E8\u6570\u636E\uFF08\u4E0D\u5206\u671F\u95F4\uFF09" }], onChange: (time_mode) => set({ time_mode }) })
                ),
                v.time_mode === "period" && h(Text, { label: "\u53EF\u67E5\u8BE2\u7684\u671F\u95F4", value: v.coverage, disabled: off, placeholder: "\u4F8B\u5982 2025, 2026-09, 2026-10", onChange: (coverage) => set({ coverage }) }),
                v.time_mode === "period" && h(
                  "div",
                  { className: "nwc-inline" },
                  h(Button, { size: "sm", variant: "outline", disabled: off || checking || !v.operands[0]?.table || !v.operands[0]?.time_column, onClick: inspect }, checking ? "\u6B63\u5728\u68C0\u67E5\u2026" : "\u68C0\u67E5\u65F6\u95F4\u5B57\u6BB5\u7684\u5B9E\u9645\u8303\u56F4"),
                  observed && h("span", { className: observed.failed ? "nwc-error" : "nwc-sub" }, observed.failed ? observed.message : "\u6700\u65E9 " + (observed.first_observed || "\u65E0\u8BB0\u5F55") + "\uFF0C\u6700\u665A " + (observed.latest_observed || "\u65E0\u8BB0\u5F55"))
                )
              ),
              h(
                Section,
                { title: "\u8BA1\u7B97", extra: editor && h(Button, { size: "sm", variant: "ghost", disabled: v.operands.length >= 10, onClick: () => set((x) => ({ operands: [...x.operands, blankOperand(x.operands.length + 1)] })) }, icon("IconPlusOutlineMedium", 14), "\u6DFB\u52A0\u64CD\u4F5C\u6570") },
                v.operands.map((o, i) => h(
                  "div",
                  { key: i, className: "nwc-operand" + (v.time_mode === "period" ? "" : " nwc-operand-notime") },
                  h(Text, { label: "\u6807\u8BC6", value: o.id, disabled: off, onChange: (id) => op(i, { id }) }),
                  h(Pick, { label: "\u6388\u6743\u8868", value: o.table, disabled: off, items: tables.map((t) => ({ id: t.name, label: t.label ? t.name + " \xB7 " + t.label : t.name })), onChange: (table2) => op(i, { table: table2, column: "", time_column: "", entity_columns: [] }) }),
                  h(Pick, { label: "\u805A\u5408\u65B9\u5F0F", value: o.aggregation, disabled: off, items: AGG, onChange: (aggregation) => op(i, { aggregation }) }),
                  h(Pick, { label: "\u7EDF\u8BA1\u5B57\u6BB5", value: o.column, disabled: off || !o.table, items: [...o.aggregation === "count" ? [{ id: "", label: "\u5168\u90E8\u8BB0\u5F55" }] : [], ...colsOf(o.table).map((c) => ({ id: c.name, label: c.label ? c.name + " \xB7 " + c.label : c.name }))], onChange: (column) => op(i, { column }) }),
                  v.time_mode === "period" && h(Pick, { label: "\u65F6\u95F4\u5B57\u6BB5", value: o.time_column, disabled: off || !o.table, items: colsOf(o.table).map((c) => ({ id: c.name, label: c.label ? c.name + " \xB7 " + c.label : c.name })), onChange: (time_column) => op(i, { time_column }) }),
                  h("div", { className: "nwc-operand-remove" }, editor && v.operands.length > 1 && h(Button, { size: "sm", variant: "ghost", "aria-label": "\u79FB\u9664\u64CD\u4F5C\u6570 " + (i + 1), onClick: () => set((x) => ({ operands: x.operands.filter((_, j) => j !== i) })) }, "\u79FB\u9664"))
                )),
                h(Text, { label: "\u8BA1\u7B97\u516C\u5F0F", value: v.formula, disabled: off, placeholder: "value \u6216 spent / budget * 100", hint: "\u5F15\u7528\u4E0A\u9762\u7684\u64CD\u4F5C\u6570\u6807\u8BC6\uFF0C\u652F\u6301 + \u2212 \xD7 \xF7 \u548C\u62EC\u53F7\uFF1B\u5206\u6BCD\u4E3A\u96F6\u65F6\u7ED3\u679C\u4E3A\u7A7A\u3002", onChange: (formula) => set({ formula }) })
              ),
              reason(initial) && v.operands.map((o, i) => {
                const choices = [...initial.review_filters || project.review_candidates?.[initial.id]?.review_filters || [], ...(view(initial).operands || []).flatMap((a) => a.filters || [])];
                const unique = [...new Map(choices.map((f) => [JSON.stringify(f), f])).values()];
                if (!unique.length) return null;
                return h(
                  Section,
                  { key: o.id + i, title: (o.id === "numerator" ? "\u5206\u5B50" : o.id === "denominator" ? "\u5206\u6BCD" : o.id) + "\u7684\u7B5B\u9009\u6761\u4EF6" },
                  unique.map((f) => {
                    const key = JSON.stringify(f), on = (o.filters || []).some((x) => JSON.stringify(x) === key);
                    return h(Checkbox, { key, label: f.column + " " + ({ eq: "\u7B49\u4E8E", ne: "\u4E0D\u7B49\u4E8E", in: "\u5C5E\u4E8E", gt: "\u5927\u4E8E", gte: "\u5927\u4E8E\u7B49\u4E8E", lt: "\u5C0F\u4E8E", lte: "\u5C0F\u4E8E\u7B49\u4E8E" }[f.op] || f.op) + " " + f.values.join("\u3001"), checked: on, disabled: off, onChange: (yes) => op(i, { filters: yes ? [...o.filters || [], f] : (o.filters || []).filter((x) => JSON.stringify(x) !== key) }) });
                  })
                );
              }),
              h(
                Section,
                { title: "\u5206\u7EC4\u5B57\u6BB5", extra: h("span", { className: "nwc-sub" }, dimTable ? "\u5DF2\u9009 " + v.dimensions.length : "\u5148\u9009\u62E9\u7B2C\u4E00\u4E2A\u64CD\u4F5C\u6570\u7684\u6388\u6743\u8868") },
                dims.length > 0 && h("div", { className: "nwc-check-grid" }, dims.map((c) => h(Checkbox, {
                  key: c.name,
                  label: c.label ? c.name + " \xB7 " + c.label : c.name,
                  disabled: off,
                  checked: v.dimensions.some((d) => d.table === dimTable && d.column === c.name),
                  onChange: (on) => set((x) => ({ dimensions: on ? [...x.dimensions, { id: "dim" + (x.dimensions.length + 1) + "_" + c.name.replace(/[^a-zA-Z0-9_]/g, "").slice(0, 25), table: dimTable, column: c.name }] : x.dimensions.filter((d) => d.table !== dimTable || d.column !== c.name) }))
                })))
              ),
              h(Checkbox, { label: "\u5DF2\u786E\u8BA4\u8BE5\u6307\u6807\u53EF\u4EE5\u6309\u6240\u9700\u8303\u56F4\u7D2F\u52A0", checked: v.additive, disabled: off, onChange: (additive) => set({ additive }) })
            );
          }
        });
      }
      return h(
        React.Fragment,
        null,
        h(Toolbar, null, h(Search, { value: q, onChange: setQ, placeholder: "\u641C\u7D22\u6307\u6807" }), editor && h(Button, { size: "sm", variant: "primary", disabled: !draft.source_ids.length, onClick: () => setDialog({ kind: "builder" }) }, icon("IconPlusOutlineMedium"), "\u65B0\u589E\u6307\u6807")),
        h(Card, null, h(Table, { rowKey: (r) => r.id, rows, empty: metrics.length ? "\u6CA1\u6709\u5339\u914D\u7684\u6307\u6807" : "\u8FD8\u6CA1\u6709\u7ED3\u6784\u5316\u6307\u6807", columns: [
          { key: "name", title: "\u6307\u6807", render: (r) => h("div", null, h("div", { className: "nwc-strong", title: r.name }, r.name, reason(r) && h(Tag, { tone: "warning" }, "\u5F85\u590D\u6838")), h("div", { className: "nwc-sub", title: sourceName(view(r).source_id) + " \xB7 " + view(r).id + " \xB7 v" + (view(r).version || "1.0.0") }, sourceName(view(r).source_id) + " \xB7 " + view(r).id + " \xB7 v" + (view(r).version || "1.0.0"))) },
          { key: "unit", title: "\u5355\u4F4D", optional: true, width: "80px", render: (r) => view(r).unit || "\u2014" },
          { key: "periods", title: "\u53EF\u67E5\u671F\u95F4", optional: true, width: "140px", render: (r) => view(r).time_mode === "all" ? "\u5168\u90E8\u6570\u636E" : (view(r).coverage || []).length ? view(r).coverage[0] + " \u81F3 " + view(r).coverage[view(r).coverage.length - 1] : h(Tag, { tone: "warning" }, "\u672A\u586B\u5199") },
          { key: "definition", title: "\u53E3\u5F84\u8BF4\u660E", render: (r) => h("span", { title: r.definition }, r.definition) },
          { key: "ops", title: "\u64CD\u4F5C", width: "210px", wrap: true, render: (r) => h(RowActions, { items: [editor && { label: reason(r) ? "\u590D\u6838" : "\u7F16\u8F91", onClick: () => setDialog({ kind: "builder", row: r }) }, { label: editor ? "\u9AD8\u7EA7\u914D\u7F6E" : "\u67E5\u770B\u914D\u7F6E", onClick: () => setDialog({ kind: "json", row: r }) }, editor && { label: "\u5220\u9664", danger: true, onClick: () => setDialog({ kind: "remove", row: r }) }] }) }
        ] })),
        dialog?.kind === "builder" && h(MetricEditor, { initial: dialog.row || null, onClose: () => setDialog(null) }),
        dialog?.kind === "json" && h(EditDialog, {
          title: "\u9AD8\u7EA7\u914D\u7F6E \xB7 " + dialog.row.name,
          wide: true,
          initial: { text: JSON.stringify(view(dialog.row), null, 2) },
          saveLabel: editor ? reason(dialog.row) ? "\u786E\u8BA4\u590D\u6838\u5E76\u4FDD\u5B58" : "\u4FDD\u5B58" : "\u5173\u95ED",
          onClose: () => setDialog(null),
          validate: (v) => {
            if (!editor) return null;
            try {
              const c = JSON.parse(v.text);
              if (!c || typeof c !== "object" || Array.isArray(c)) return "\u9700\u8981\u4E00\u4E2A JSON \u5BF9\u8C61\u3002";
            } catch {
              return "JSON \u683C\u5F0F\u4E0D\u6B63\u786E\u3002";
            }
            return null;
          },
          onSave: (v) => {
            if (!editor) return;
            const config = JSON.parse(v.text);
            return saveDraft((d) => ({ ...d, terms: d.terms.map((r) => r.id === dialog.row.id ? { ...r, config, ...reason(r) ? { confirm_review: true } : {} } : r) }), "\u6307\u6807\u914D\u7F6E\u5DF2\u4FDD\u5B58\u5230\u8349\u7A3F\u3002");
          },
          render: (v, set) => h(Area, { label: "\u7ED3\u6784\u5316\u6307\u6807\u914D\u7F6E\uFF08JSON\uFF09", value: v.text, disabled: !editor, rows: 16, mono: true, onChange: (text) => set({ text }) })
        }),
        dialog?.kind === "remove" && h(Confirm, { title: "\u5220\u9664\u6307\u6807\u300C" + dialog.row.name + "\u300D", description: "\u5220\u9664\u540E\u5BF9\u8BDD\u4E0D\u80FD\u518D\u4F7F\u7528\u8FD9\u4E2A\u6307\u6807\u3002", confirmLabel: "\u5220\u9664\u6307\u6807", onClose: () => setDialog(null), onConfirm: () => saveDraft((d) => ({ ...d, terms: d.terms.filter((r) => r.id !== dialog.row.id) }), "\u6307\u6807\u5DF2\u5220\u9664\u3002") })
      );
    }
    function Examples({ cfg, project, editor, saveDraft, reload, rev }) {
      const draft = draftOf(project), [q, setQ] = React.useState(""), [dialog, setDialog] = React.useState(null), [verification, setVerification] = React.useState(null), [testing, setTesting] = React.useState(false);
      const [review, setReview] = React.useState(null);
      const openReview = async () => {
        setTesting(true);
        try {
          const value = await request("admin_sessions", { business_project: project.id });
          const history = await request("admin_validation", { business_project: project.id, action: "review_history" });
          setReview({ sessions: value.sessions, history: history.records, choice: "", category: "delivery", note: "" });
        } catch (error) {
          setReview({ error: error.message });
        } finally {
          setTesting(false);
        }
      };
      const captureReview = async () => {
        setTesting(true);
        try {
          const selected = review.sessions[Number(review.choice)];
          if (!selected) throw Error("\u8BF7\u9009\u62E9\u5B9E\u9645\u95EE\u9898\u3002");
          const value = await request("admin_session_review", { business_project: project.id, ...selected, category: review.category, note: review.note });
          const candidate = value.record.candidates.at(-1);
          setReview({ ...review, record: value.record, source_id: candidate?.source_id || draft.source_ids[0], logical_sql: candidate?.sql || "", expectedText: "", assumptions: "", error: "" });
          await reload();
        } catch (error) {
          setReview({ ...review, error: error.message });
        } finally {
          setTesting(false);
        }
      };
      const stageReview = async () => {
        setTesting(true);
        try {
          const expected = review.expectedText.trim() ? JSON.parse(review.expectedText) : void 0;
          await request("admin_validation", { business_project: project.id, action: "stage_review_example", review_id: review.record.id, source_id: review.source_id, logical_sql: review.logical_sql, expected_rows: expected, assumptions: review.assumptions, expected_revision: rev() });
          await reload();
          setReview({ ...review, staged: true, error: "" });
        } catch (error) {
          setReview({ ...review, error: error.message });
        } finally {
          setTesting(false);
        }
      };
      const reviewTrial = async (row, r) => {
        setTesting(true);
        try {
          const value = await request("admin_trial_review", { business_project: project.id, session_id: r.session_id, turn: 1, trial_id: r.id });
          setVerification({ row, value });
        } catch (error) {
          setVerification({ row, error: error.message });
        } finally {
          setTesting(false);
        }
      };
      const trial = async (row) => {
        setTesting(true);
        try {
          const value = await request("admin_trial", { business_project: project.id, example_id: row.id });
          openSession(value.sessionId);
        } catch (error) {
          setVerification({ row, error: error.message });
        } finally {
          setTesting(false);
        }
      };
      const verify = async (row, action) => {
        setTesting(true);
        try {
          const value = await request("admin_validation", { business_project: project.id, example_id: row.id, action });
          setVerification({ row, value });
        } catch (error) {
          setVerification({ row, error: error.message });
        } finally {
          setTesting(false);
        }
      };
      const sources = cfg.sources.filter((s) => draft.source_ids.includes(s.id)), rows = draft.examples.filter((r) => !q || (r.question + " " + r.logical_sql).includes(q));
      return h(
        React.Fragment,
        null,
        h(Toolbar, null, h(Search, { value: q, onChange: setQ, placeholder: "\u641C\u7D22\u95EE\u6CD5\u6216 SQL" }), editor && h(Button, { size: "sm", variant: "outline", disabled: testing, onClick: openReview }, "\u4ECE\u4F1A\u8BDD\u590D\u6838"), editor && h(Button, { size: "sm", variant: "primary", disabled: !sources.length, onClick: () => setDialog({ row: { id: uid(), question: "", logical_sql: "", source_id: sources[0]?.id || "" }, isNew: true }) }, icon("IconPlusOutlineMedium"), "\u65B0\u589E\u793A\u4F8B")),
        h(Card, null, h(Table, { rowKey: (r) => r.id, rows, empty: draft.examples.length ? "\u6CA1\u6709\u5339\u914D\u7684\u793A\u4F8B" : "\u8FD8\u6CA1\u6709\u67E5\u8BE2\u793A\u4F8B", columns: [
          { key: "question", title: "\u4E1A\u52A1\u95EE\u6CD5", render: (r) => h("div", null, h("span", { className: "nwc-strong", title: r.question }, r.question), r.origin_review && h(Tag, { tone: "neutral" }, "\u6765\u81EA\u590D\u6838")) },
          { key: "source", title: "\u6570\u636E\u6E90", optional: true, width: "160px", render: (r) => sources.find((s) => s.id === r.source_id)?.name || "\u2014" },
          { key: "sql", title: "SQL", render: (r) => h("span", { className: "nwc-mono", title: r.logical_sql }, r.logical_sql) },
          { key: "ops", title: "\u64CD\u4F5C", width: "180px", wrap: true, render: (r) => h(RowActions, { items: [{ label: editor ? "\u7F16\u8F91" : "\u67E5\u770B", onClick: () => setDialog({ row: r, expectedText: r.expected_rows ? JSON.stringify(r.expected_rows, null, 2) : "" }) }, editor && r.origin_review && { label: "\u539F\u53CD\u9988", disabled: testing, onClick: async () => {
            setTesting(true);
            try {
              const history = await request("admin_validation", { business_project: project.id, action: "review_history" });
              const orig = history.records.find((x) => x.id === r.origin_review);
              if (orig) {
                setReview({ sessions: [], history: history.records, record: orig, source_id: r.source_id, logical_sql: r.logical_sql, expectedText: r.expected_rows ? JSON.stringify(r.expected_rows, null, 2) : "", assumptions: r.assumptions || "", error: "" });
              } else {
                setVerification({ row: r, error: "\u672A\u627E\u5230\u5173\u8054\u7684\u539F\u59CB\u53CD\u9988\u8BB0\u5F55\u3002" });
              }
            } catch (e) {
              setVerification({ row: r, error: e.message });
            } finally {
              setTesting(false);
            }
          } }, editor && { label: "\u539F\u751F\u8BD5\u95EE", disabled: testing, onClick: () => trial(r) }, editor && { label: "\u8BD5\u8FD0\u884C", disabled: testing, onClick: () => verify(r, "run") }, editor && { label: "\u9A8C\u8BC1\u8BB0\u5F55", disabled: testing, onClick: () => verify(r, "history") }, editor && { label: "\u5220\u9664", danger: true, onClick: () => setDialog({ row: r, remove: true }) }] }) }
        ] })),
        dialog && !dialog.remove && h(EditDialog, {
          title: (dialog.isNew ? "\u65B0\u589E" : "\u7F16\u8F91") + "\u67E5\u8BE2\u793A\u4F8B",
          initial: { ...dialog.row, expectedText: dialog.expectedText || "" },
          wide: true,
          saveLabel: editor ? "\u4FDD\u5B58" : "\u5173\u95ED",
          validate: (v) => editor && (!v.question.trim() || !v.logical_sql.trim()) && "\u8BF7\u586B\u5199\u95EE\u6CD5\u548C SQL\u3002",
          onClose: () => setDialog(null),
          onSave: (v) => {
            if (!editor) return;
            const { expectedText, ...fields } = v;
            const row = { ...fields, question: v.question.trim() };
            if (expectedText?.trim()) {
              row.expected_rows = JSON.parse(expectedText);
              if (!Array.isArray(row.expected_rows)) throw Error("\u72EC\u7ACB\u9884\u671F\u987B\u4E3A\u7ED3\u679C\u884C\u6570\u7EC4\u3002");
            } else delete row.expected_rows;
            return saveDraft((d) => ({ ...d, examples: dialog.isNew ? [...d.examples, row] : d.examples.map((r) => r.id === row.id ? row : r) }));
          },
          render: (v, set) => h(React.Fragment, null, h(Text, { label: "\u4E1A\u52A1\u95EE\u6CD5", value: v.question, disabled: !editor, onChange: (question) => set({ question }) }), h(Pick, { label: "\u6570\u636E\u6E90", value: v.source_id, disabled: !editor, items: sources.map((s) => ({ id: s.id, label: s.name })), onChange: (source_id) => set({ source_id }) }), h(Area, { label: "\u53EA\u8BFB SQL", value: v.logical_sql, disabled: !editor, rows: 10, mono: true, onChange: (logical_sql) => set({ logical_sql }) }), h(Area, { label: "\u72EC\u7ACB\u9884\u671F\u7ED3\u679C\uFF08\u53EF\u9009 JSON \u884C\u6570\u7EC4\uFF09", value: v.expectedText || "", disabled: !editor, rows: 5, mono: true, onChange: (expectedText) => set({ expectedText }) }), h("p", { className: "nwc-sub" }, "\u8BF7\u901A\u8FC7\u72EC\u7ACB SQL \u6216\u5DF2\u786E\u8BA4\u4E1A\u52A1\u5B9A\u4E49\u63D0\u4F9B\u9884\u671F\uFF1B\u6A21\u578B\u81EA\u8EAB\u56DE\u7B54\u4E0D\u80FD\u4F5C\u4E3A\u771F\u503C\u3002\u4FDD\u5B58\u6210\u529F\u53EA\u8868\u793A\u914D\u7F6E\u751F\u6548\u3002"))
        }),
        verification && h(Modal, { open: true, onClose: () => setVerification(null), title: "\u793A\u4F8B\u9A8C\u8BC1", closeLabel: "\u5173\u95ED" }, h(
          "div",
          { className: "nwc-pad" },
          h("p", null, verification.row.question),
          verification.error ? h("p", { role: "alert" }, verification.error) : h(
            React.Fragment,
            null,
            h("p", null, "\u914D\u7F6E\u5DF2\u751F\u6548\u3001\u6267\u884C\u5DF2\u9A8C\u8BC1\u3001\u7B54\u6848\u5DF2\u6838\u5BF9\u5206\u522B\u8BB0\u5F55\u3002\u6570\u636E\u6216\u914D\u7F6E\u53D8\u5316\u540E\u9700\u8981\u590D\u6D4B\u3002"),
            ...(verification.value.record ? [verification.value.record] : verification.value.records.filter((r) => r.example_id === verification.row.id)).map((r) => h(
              "div",
              { key: r.id },
              h(Tag, null, r.state),
              h("p", null, r.freshness === "expired" ? "\u9A8C\u8BC1\u5DF2\u8FC7\u671F" : r.freshness === "unknown" ? "\u6570\u636E\u53D8\u5316\u72B6\u6001\u672A\u77E5" : "\u5BF9\u5E94\u5F53\u524D\u6570\u636E\u4E0E\u914D\u7F6E"),
              h("p", null, r.invalid_reason || (r.kind === "native_trial_review" ? r.result_match === true ? "\u67E5\u8BE2\u7ED3\u679C\u4E0E\u72EC\u7ACB\u9884\u671F\u4E00\u81F4\uFF1B\u56DE\u7B54\u6587\u5B57\u4ECD\u9700\u72EC\u7ACB\u590D\u6838\u3002" : r.result_match === false ? "\u67E5\u8BE2\u7ED3\u679C\u4E0E\u72EC\u7ACB\u9884\u671F\u4E0D\u4E00\u81F4\u3002" : "\u539F\u751F\u8BD5\u95EE\u5C1A\u672A\u5B8C\u6210\u72EC\u7ACB\u6838\u5BF9\u3002" : r.answer_match === true ? "\u5DF2\u4E0E\u72EC\u7ACB\u9884\u671F\u9010\u884C\u6838\u5BF9\u3002" : r.answer_match === false ? "\u6267\u884C\u6210\u529F\uFF0C\u4F46\u4E0E\u72EC\u7ACB\u9884\u671F\u4E0D\u4E00\u81F4\u3002" : "\u5C1A\u672A\u5B8C\u6210\u72EC\u7ACB\u9884\u671F\u6838\u5BF9\u3002")),
              r.session_id && h(Button, { size: "sm", variant: "outline", onClick: () => openSession(r.session_id) }, "\u6253\u5F00\u539F\u751F\u4F1A\u8BDD"),
              r.kind === "native_trial" && h(Button, { size: "sm", variant: "outline", disabled: testing, onClick: () => reviewTrial(verification.row, r) }, "\u6838\u5BF9\u8BD5\u95EE\u7ED3\u679C")
            ))
          ),
          h(Button, { onClick: () => setVerification(null) }, "\u5173\u95ED")
        )),
        review && h(Modal, { open: true, onClose: () => setReview(null), title: "\u4ECE\u5B9E\u9645\u95EE\u9898\u5F62\u6210\u5F85\u590D\u6838\u793A\u4F8B", closeLabel: "\u5173\u95ED" }, h(
          "div",
          { className: "nwc-pad" },
          review.error && h("p", { role: "alert" }, review.error),
          review.staged ? h("p", null, "\u5DF2\u4FDD\u5B58\u4E3A\u5F85\u590D\u6838\u8349\u7A3F\u3002\u8BF7\u68C0\u67E5\u672A\u751F\u6548\u7684\u6539\u52A8\uFF0C\u786E\u8BA4\u540E\u5E94\u7528\uFF0C\u518D\u8FD0\u884C\u793A\u4F8B\u548C\u539F\u751F\u8BD5\u95EE\uFF1B\u539F\u95EE\u9898\u4E0E\u5931\u8D25\u8BB0\u5F55\u7EE7\u7EED\u4FDD\u7559\u3002") : review.record ? h(
            React.Fragment,
            null,
            h("p", null, review.record.question),
            h("p", { className: "nwc-sub" }, "\u539F\u56DE\u7B54\uFF08\u4E0D\u4F5C\u4E3A\u771F\u503C\uFF09\uFF1A" + review.record.answer),
            h(Pick, { label: "\u6570\u636E\u6E90", value: review.source_id, items: sources.map((s) => ({ id: s.id, label: s.name })), onChange: (source_id) => setReview({ ...review, source_id }) }),
            h(Area, { label: "\u5F85\u590D\u6838\u7684\u53EA\u8BFB SQL", value: review.logical_sql, rows: 7, mono: true, onChange: (logical_sql) => setReview({ ...review, logical_sql }) }),
            h(Area, { label: "\u9002\u7528\u524D\u63D0", value: review.assumptions, onChange: (assumptions) => setReview({ ...review, assumptions }) }),
            h(Area, { label: "\u72EC\u7ACB\u9884\u671F\u7ED3\u679C\uFF08JSON \u884C\u6570\u7EC4\uFF09", value: review.expectedText, rows: 4, mono: true, onChange: (expectedText) => setReview({ ...review, expectedText }) }),
            h(Button, { disabled: testing || !review.logical_sql.trim(), onClick: stageReview }, "\u4FDD\u5B58\u5F85\u590D\u6838\u8349\u7A3F")
          ) : review.sessions && h(
            React.Fragment,
            null,
            review.history?.filter((r) => r.kind === "session_feedback").length > 0 && h(Section, { title: "\u5DF2\u8BB0\u5F55\u7684\u95EE\u9898" }, review.history.filter((r) => r.kind === "session_feedback").map((r) => h("div", { key: r.id }, h("p", null, r.question), h("p", { className: "nwc-sub" }, r.note), h(Button, { size: "sm", variant: "outline", onClick: () => openSession(r.session_id) }, "\u6253\u5F00\u539F\u4F1A\u8BDD"), h(Button, { size: "sm", variant: "outline", onClick: () => {
              const c = r.candidates.at(-1);
              setReview({ ...review, record: r, source_id: c?.source_id || draft.source_ids[0], logical_sql: c?.sql || "", expectedText: "", assumptions: "", error: "" });
            } }, "\u7EE7\u7EED\u590D\u6838"), draft.examples.filter((e) => e.origin_review === r.id).map((e) => h(Button, { key: e.id, size: "sm", variant: "outline", onClick: () => {
              setReview(null);
              verify(e, "history");
            } }, "\u5173\u8054\u793A\u4F8B\u9A8C\u8BC1\u8BB0\u5F55"))))),
            h(Pick, { label: "\u672C\u9879\u76EE\u7684\u5B9E\u9645\u95EE\u9898", value: review.choice, items: review.sessions.map((s, i) => ({ id: String(i), label: s.question })), onChange: (choice) => setReview({ ...review, choice }) }),
            !review.sessions.length && h("p", null, "\u6682\u65E0\u53EF\u590D\u6838\u7684\u5DF2\u7ED1\u5B9A\u4F1A\u8BDD\u3002"),
            h(Pick, { label: "\u95EE\u9898\u5206\u7C7B", value: review.category, items: [["scope", "\u8303\u56F4"], ["relation", "\u5173\u8054"], ["retrieval", "\u68C0\u7D22"], ["term", "\u672F\u8BED"], ["delivery", "\u4EA4\u4ED8"]].map(([id, label]) => ({ id, label })), onChange: (category) => setReview({ ...review, category }) }),
            h(Area, { label: "\u9700\u8981\u7EA0\u6B63\u7684\u884C\u4E3A", value: review.note, onChange: (note) => setReview({ ...review, note }) }),
            h("p", { className: "nwc-sub" }, "\u53CD\u9988\u4F1A\u4FDD\u7559\u4E3A\u5F85\u590D\u6838\u8BB0\u5F55\uFF0C\u4E0D\u81EA\u52A8\u4FEE\u6539\u5DF2\u751F\u6548\u53E3\u5F84\uFF0C\u4E5F\u4E0D\u628A\u539F\u56DE\u7B54\u5F53\u6210\u6807\u51C6\u7B54\u6848\u3002"),
            h(Button, { disabled: testing || review.choice === "" || !review.note.trim(), onClick: captureReview }, "\u8BB0\u5F55\u5E76\u51C6\u5907\u793A\u4F8B")
          ),
          h(Button, { variant: "outline", onClick: () => setReview(null) }, "\u5173\u95ED")
        )),
        dialog?.remove && h(Confirm, { title: "\u5220\u9664\u67E5\u8BE2\u793A\u4F8B", description: clip(dialog.row.question, 80), confirmLabel: "\u5220\u9664\u793A\u4F8B", onClose: () => setDialog(null), onConfirm: () => saveDraft((d) => ({ ...d, examples: d.examples.filter((r) => r.id !== dialog.row.id) }), "\u793A\u4F8B\u5DF2\u5220\u9664\u3002") })
      );
    }
    function Skills({ project, editor, admin, saveDraft, say }) {
      const draft = draftOf(project), [tab, setTab] = React.useState("installed");
      return h(
        React.Fragment,
        null,
        h("div", { className: "nwc-tabs" }, h(UI.SegmentedControl, { value: tab, label: "\u5206\u6790\u6280\u80FD", options: [{ value: "installed", label: "\u5DF2\u5B89\u88C5\u6280\u80FD" }, { value: "notes", label: "\u5206\u6790\u65B9\u6CD5\u8BF4\u660E\uFF08" + draft.skills.length + "\uFF09" }], onChange: setTab })),
        tab === "installed" ? h(InstalledSkills, { project, editable: admin, say }) : h(SkillNotes, { project, editor, saveDraft })
      );
    }
    function InstalledSkills({ project, editable, say }) {
      const [data, setData] = React.useState(null), [q, setQ] = React.useState(""), [busy, setBusy] = React.useState(false);
      const load = () => getJson("project_skills", { business_project: project.id }).then(setData);
      React.useEffect(() => {
        load().catch((e) => say(e.message, true));
      }, [project.id]);
      if (!data) return h(Empty, null, "\u6B63\u5728\u8BFB\u53D6\u6280\u80FD\u2026");
      async function toggle(skill, value) {
        setBusy(true);
        try {
          await request("policy_update", { project: project.id, business_project: project.id, scope: "project", changes: {}, skills: { [skill.id]: value }, expected_revision: data.revision });
          await load();
        } catch (e) {
          say(e.message, true);
          await load().catch(() => {
          });
        } finally {
          setBusy(false);
        }
      }
      const rows = data.skills.filter((s) => !q || (s.title + " " + (s.category || "")).toLowerCase().includes(q.toLowerCase()));
      const on = data.skills.filter((s) => s.enabled && s.applicable).length;
      return h(
        React.Fragment,
        null,
        h(Toolbar, null, h(Search, { value: q, onChange: setQ, placeholder: "\u641C\u7D22\u6280\u80FD" }), h("span", { className: "nwc-sub" }, "\u5DF2\u542F\u7528 " + on + " / " + data.skills.length)),
        h(Card, null, h(Table, { rowKey: (s) => s.id, rows, columns: [
          { key: "title", title: "\u6280\u80FD", render: (s) => h("span", { className: "nwc-strong", title: s.title }, s.title) },
          { key: "category", title: "\u5206\u7C7B", optional: true, width: "140px", render: (s) => s.category || "\u6269\u5C55\u6280\u80FD" },
          { key: "requires", title: "\u9002\u7528\u6761\u4EF6", render: (s) => {
            const t = String(s.requires || "").replace(/\s*（?(?:options\.\w+|comparison_result_ref)）?/g, "").replace(/\s*dimensions\s*/g, "").replace(/\s*columns\s*/g, "") + (s.applicable ? "" : "\uFF08\u5F53\u524D\u9879\u76EE\u7F3A\u5C11\u6240\u9700\u6307\u6807\uFF09");
            return h("span", { title: t }, t);
          } },
          { key: "on", title: "\u542F\u7528", width: "80px", wrap: true, render: (s) => h(Switch, { label: "\u542F\u7528" + s.title, checked: s.enabled && s.applicable, disabled: busy || !editable || !s.applicable, onChange: (v) => toggle(s, v) }) }
        ] }))
      );
    }
    function SkillNotes({ project, editor, saveDraft }) {
      const draft = draftOf(project), [dialog, setDialog] = React.useState(null);
      return h(
        React.Fragment,
        null,
        h(Toolbar, null, h("span", { className: "nwc-sub" }, "\u8BF4\u660E\u4E1A\u52A1\u4E0A\u7684\u5206\u6790\u65B9\u6CD5\u548C\u9002\u7528\u8303\u56F4\uFF0C\u4F9B\u95EE\u7B54\u53C2\u8003\u3002"), editor && h(Button, { size: "sm", variant: "primary", onClick: () => setDialog({ row: { id: uid(), name: "", description: "" }, isNew: true }) }, icon("IconPlusOutlineMedium"), "\u65B0\u589E\u8BF4\u660E")),
        h(Card, null, h(Table, { rowKey: (r) => r.id, rows: draft.skills, empty: "\u8FD8\u6CA1\u6709\u5206\u6790\u65B9\u6CD5\u8BF4\u660E", columns: [
          { key: "name", title: "\u540D\u79F0", width: "200px", render: (r) => h("span", { className: "nwc-strong" }, r.name) },
          { key: "description", title: "\u9002\u7528\u8303\u56F4\u4E0E\u4E1A\u52A1\u89C4\u5219", render: (r) => h("span", { title: r.description }, clip(r.description, 90)) },
          { key: "ops", title: "\u64CD\u4F5C", width: "150px", wrap: true, render: (r) => h(RowActions, { items: [{ label: editor ? "\u7F16\u8F91" : "\u67E5\u770B", onClick: () => setDialog({ row: r }) }, editor && { label: "\u5220\u9664", danger: true, onClick: () => setDialog({ row: r, remove: true }) }] }) }
        ] })),
        dialog && !dialog.remove && h(EditDialog, {
          title: (dialog.isNew ? "\u65B0\u589E" : "\u7F16\u8F91") + "\u5206\u6790\u65B9\u6CD5\u8BF4\u660E",
          initial: dialog.row,
          wide: true,
          saveLabel: editor ? "\u4FDD\u5B58" : "\u5173\u95ED",
          validate: (v) => editor && !v.name.trim() && "\u8BF7\u586B\u5199\u540D\u79F0\u3002",
          onClose: () => setDialog(null),
          onSave: (v) => {
            if (!editor) return;
            const row = { ...v, name: v.name.trim() };
            return saveDraft((d) => ({ ...d, skills: dialog.isNew ? [...d.skills, row] : d.skills.map((r) => r.id === row.id ? row : r) }));
          },
          render: (v, set) => h(React.Fragment, null, h(Text, { label: "\u540D\u79F0", value: v.name, disabled: !editor, onChange: (name) => set({ name }) }), h(Area, { label: "\u9002\u7528\u8303\u56F4\u4E0E\u4E1A\u52A1\u89C4\u5219", value: v.description, disabled: !editor, rows: 6, onChange: (description) => set({ description }) }))
        }),
        dialog?.remove && h(Confirm, { title: "\u5220\u9664\u300C" + dialog.row.name + "\u300D", confirmLabel: "\u5220\u9664", onClose: () => setDialog(null), onConfirm: () => saveDraft((d) => ({ ...d, skills: d.skills.filter((r) => r.id !== dialog.row.id) }), "\u5DF2\u5220\u9664\u3002") })
      );
    }
    function Imports({ cfg, project, act, admin, editor, say }) {
      const [packs, setPacks] = React.useState(null), [error, setError] = React.useState(""), [dialog, setDialog] = React.useState(null), [exporting, setExporting] = React.useState(false);
      const scopeKey = (project.draft?.source_ids || []).join(",") + ":" + JSON.stringify(project.draft?.tables || {}).length;
      React.useEffect(() => {
        let live = true;
        request("business_packs", { business_project: project.id }).then((v) => {
          if (live) {
            setPacks(v.packs || []);
            setError("");
          }
        }).catch((e) => {
          if (live) setError(e.message);
        });
        return () => {
          live = false;
        };
      }, [project.id, scopeKey]);
      const packMessage = (v) => {
        const r = v?.pack_report || {}, i = r.imported || {}, sk = r.skipped || [];
        return "\u5DF2\u5BFC\u5165\uFF1A\u5B57\u6BB5\u4E0E\u53D6\u503C " + (i.mappings || 0) + " \u6761\u3001\u53E3\u5F84\u8BF4\u660E " + (i.terms || 0) + " \u6761\u3001\u7ED3\u6784\u5316\u6307\u6807 " + (i.metrics || 0) + " \u4E2A\u3001\u67E5\u8BE2\u793A\u4F8B " + (i.examples || 0) + " \u4E2A" + (sk.length ? "\uFF1B\u8DF3\u8FC7 " + sk.length + " \u9879" : "") + "\u3002\u5C1A\u672A\u751F\u6548\uFF0C\u8BF7\u70B9\u9876\u90E8\u300C\u672A\u751F\u6548\u7684\u6539\u52A8\u300D\u6838\u5BF9\u540E\u751F\u6548\u3002";
      };
      async function exportTemplate() {
        setExporting(true);
        try {
          const value = await request("business_template", { business_project: project.id });
          const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
          const a = document.createElement("a");
          a.href = url;
          a.download = "ask-data-business-template.json";
          a.click();
          URL.revokeObjectURL(url);
        } catch (e) {
          say(e.message, true);
        } finally {
          setExporting(false);
        }
      }
      const sourceName = (id) => cfg.sources.find((s) => s.id === id)?.name || id;
      return h(
        React.Fragment,
        null,
        h(
          Card,
          { title: "\u5185\u7F6E\u53E3\u5F84\u5305" },
          error ? h("p", { role: "alert", className: "nwc-error nwc-pad" }, error) : !packs ? h(Empty, null, "\u6B63\u5728\u8BFB\u53D6\u2026") : h(Table, { rowKey: (p) => p.id, rows: packs, empty: "\u6CA1\u6709\u5185\u7F6E\u53E3\u5F84\u5305", columns: [
            { key: "name", title: "\u53E3\u5F84\u5305", render: (p) => h("div", null, h(NameLine, { text: p.name + " \xB7 v" + p.version }, p.imported_rows ? h(Tag, { tone: "success" }, "\u5DF2\u5BFC\u5165") : null), h("div", { className: "nwc-sub", title: p.description }, p.description)) },
            { key: "content", title: "\u5185\u5BB9", optional: true, width: "170px", render: (p) => {
              const t = p.tables + " \u5F20\u8868 \xB7 " + p.terms + " \u53E3\u5F84 \xB7 " + p.examples + " \u67E5\u8BE2\u793A\u4F8B" + (p.metrics ? " \xB7 " + p.metrics + " \u7ED3\u6784\u5316\u6307\u6807" : "");
              return h("span", { title: t }, t);
            } },
            { key: "match", title: "\u5339\u914D\u5F53\u524D\u9879\u76EE", width: "130px", render: (p) => {
              const m = p.matches[0];
              return !m ? h(Tag, { tone: "quiet" }, "\u6CA1\u6709\u5339\u914D\u7684\u8868") : h("span", { title: p.matches.map((x) => sourceName(x.source_id) + "\uFF1A\u5339\u914D " + x.matched_tables + " \u5F20" + (x.missing_tables.length ? "\uFF0C\u7F3A\u5C11 " + x.missing_tables.length + " \u5F20" : "")).join("\n") }, m.matched_tables + " \u5F20" + (m.missing_tables.length ? "\uFF08\u7F3A " + m.missing_tables.length + "\uFF09" : ""));
            } },
            { key: "ops", title: "\u64CD\u4F5C", width: "150px", wrap: true, render: (p) => h(RowActions, { items: [editor && { label: p.imported_rows ? "\u91CD\u65B0\u5BFC\u5165" : "\u5BFC\u5165", disabled: !p.matches.length, onClick: () => setDialog({ kind: "pack", pack: p }) }, admin && { label: "\u5168\u91CF\u66FF\u6362", disabled: !p.matches.some((m) => !m.missing_tables.length), onClick: () => setDialog({ kind: "pack", pack: p, replace: true }) }, p.pending?.length > 0 && { label: "\u5F85\u786E\u8BA4 " + p.pending.length, onClick: () => setDialog({ kind: "pending", pack: p }) }] }) }
          ] })
        ),
        h(
          Card,
          { title: "\u4E1A\u52A1\u6A21\u677F", actions: h(React.Fragment, null, h(Button, { size: "sm", variant: "outline", disabled: exporting, onClick: exportTemplate }, exporting ? "\u6B63\u5728\u5BFC\u51FA\u2026" : "\u5BFC\u51FA\u6A21\u677F"), editor && h(Button, { size: "sm", variant: "primary", onClick: () => setDialog({ kind: "template" }) }, "\u5BFC\u5165\u6A21\u677F")) },
          h("p", { className: "nwc-sub nwc-pad" }, "\u6A21\u677F\u5305\u542B\u5B57\u6BB5\u6620\u5C04\u3001\u53E3\u5F84\u8BF4\u660E\u3001\u6307\u6807\u548C\u67E5\u8BE2\u793A\u4F8B\uFF0C\u4E0D\u542B\u8FDE\u63A5\u51ED\u636E\u3001\u7528\u6237\u6388\u6743\u548C\u5386\u53F2\u6570\u636E\u3002\u5BFC\u51FA\u540E\u8BF7\u81EA\u884C\u68C0\u67E5\u5176\u4E2D\u7684\u4E1A\u52A1\u654F\u611F\u4FE1\u606F\u3002")
        ),
        dialog?.kind === "pack" && h(PackDialog, { pack: dialog.pack, sourceName, replace: dialog.replace, onClose: () => setDialog(null), onImport: (sid) => act("import_pack", { pack: dialog.pack.id, source_id: sid, ...dialog.replace ? { replace_calibers: true } : {} }, packMessage) }),
        dialog?.kind === "pending" && h(Dialog, { open: true, title: "\u5F85\u4E1A\u52A1\u786E\u8BA4\u7684\u53E3\u5F84 \xB7 " + dialog.pack.name, onClose: () => setDialog(null), footer: h(Button, { size: "sm", variant: "primary", onClick: () => setDialog(null) }, "\u5173\u95ED") }, h("ol", { className: "nwc-list" }, dialog.pack.pending.map((x, k) => h("li", { key: k }, x)))),
        dialog?.kind === "template" && h(TemplateDialog, { project, sources: cfg.sources.filter((x) => (project.draft?.source_ids || []).includes(x.id)), onClose: () => setDialog(null), onImport: (template, bindings) => act("import_template", { template, source_bindings: bindings }, "\u6A21\u677F\u5DF2\u5BFC\u5165\uFF0C\u5C1A\u672A\u751F\u6548\u3002\u8BF7\u70B9\u9876\u90E8\u300C\u672A\u751F\u6548\u7684\u6539\u52A8\u300D\u6838\u5BF9\u540E\u751F\u6548\u3002") })
      );
    }
    function PackDialog({ pack, sourceName, replace, onClose, onImport }) {
      const matches = replace ? pack.matches.filter((m2) => !m2.missing_tables.length) : pack.matches;
      const [sid, setSid] = React.useState(matches[0]?.source_id || ""), m = matches.find((x) => x.source_id === sid);
      return h(
        Confirm,
        {
          title: (replace ? "\u5168\u91CF\u66FF\u6362\u4E3A" : pack.imported_rows ? "\u91CD\u65B0\u5BFC\u5165" : "\u5BFC\u5165") + "\u300C" + pack.name + "\u300D",
          danger: replace,
          confirmLabel: replace ? "\u751F\u6210\u66FF\u6362\u8349\u7A3F" : "\u5BFC\u5165",
          blocked: !sid,
          onClose,
          onConfirm: () => onImport(sid),
          description: replace ? "\u4EE5\u9009\u5B9A\u7684\u5185\u7F6E\u53E3\u5F84\u5305\u91CD\u5EFA\u9879\u76EE\u53E3\u5F84\u8349\u7A3F\u3002\u751F\u6548\u540E\uFF0C\u95EE\u6570\u7EDF\u4E00\u4F7F\u7528\u65B0\u53E3\u5F84\u3002\u6570\u636E\u5E93\u6388\u6743\u4E0E\u4F1A\u8BDD\u5386\u53F2\u72EC\u7ACB\u7BA1\u7406\u3002" : "\u5BFC\u5165\u5B57\u6BB5\u4E2D\u6587\u540D\u3001\u53D6\u503C\u3001\u53E3\u5F84\u8BF4\u660E\u3001\u7ED3\u6784\u5316\u6307\u6807\u548C\u67E5\u8BE2\u793A\u4F8B\u3002\u5E93\u91CC\u7F3A\u5C11\u7684\u8868\u6216\u5B57\u6BB5\u4F1A\u8DF3\u8FC7\uFF1B\u5DF2\u6709\u5B57\u6BB5\u6620\u5C04\u5168\u90E8\u4FDD\u7559\uFF0C\u53EA\u8865\u5145\u7F3A\u9879\uFF0C\u4EBA\u5DE5\u4FEE\u6539\u7684\u53E3\u5F84\u548C\u793A\u4F8B\u4E5F\u4FDD\u7559\u3002\u5BFC\u5165\u540E\u9700\u5728\u9876\u90E8\u300C\u672A\u751F\u6548\u7684\u6539\u52A8\u300D\u4E2D\u6838\u5BF9\u5E76\u751F\u6548\u3002"
        },
        matches.length > 1 && h(Pick, { label: "\u6570\u636E\u6E90", value: sid, items: matches.map((x) => ({ id: x.source_id, label: sourceName(x.source_id) + " \xB7 \u5339\u914D " + x.matched_tables + " \u5F20\u8868" })), onChange: setSid }),
        m && h("p", { className: "nwc-sub" }, "\u5339\u914D " + m.matched_tables + " \u5F20\u8868" + (m.missing_tables.length ? "\uFF0C\u7F3A\u5C11\uFF1A" + m.missing_tables.join("\u3001") : "\u3002"))
      );
    }
    function TemplateDialog({ sources, onClose, onImport }) {
      const input = React.useRef(null), [file, setFile] = React.useState(null), [template, setTemplate] = React.useState(null), [bindings, setBindings] = React.useState({}), [error, setError] = React.useState(""), [busy, setBusy] = React.useState(false);
      async function choose(e) {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        setError("");
        setTemplate(null);
        setBindings({});
        if (f.size > 3e5) {
          setError("\u6A21\u677F\u6587\u4EF6\u8D85\u8FC7 300 KB\u3002");
          return;
        }
        setFile(f.name);
        try {
          setTemplate(JSON.parse(await f.text()));
        } catch {
          setError("\u4E0D\u662F\u6709\u6548\u7684\u6A21\u677F\u6587\u4EF6\uFF08JSON \u683C\u5F0F\u9519\u8BEF\uFF09\u3002");
        }
      }
      const ready = template && (template.sources || []).every((s) => bindings[s.alias]);
      async function go() {
        setBusy(true);
        setError("");
        try {
          await onImport(template, bindings);
          onClose();
        } catch (e) {
          setError(e.message);
        } finally {
          setBusy(false);
        }
      }
      return h(
        Dialog,
        { open: true, title: "\u5BFC\u5165\u4E1A\u52A1\u6A21\u677F", onClose, footer: h("div", { className: "nwc-actions" }, h(Button, { size: "sm", variant: "ghost", disabled: busy, onClick: onClose }, "\u53D6\u6D88"), h(Button, { size: "sm", variant: "primary", disabled: busy || !ready, onClick: go }, busy ? "\u6B63\u5728\u5BFC\u5165\u2026" : "\u6821\u9A8C\u5E76\u5BFC\u5165")) },
        // Native file input is hidden; the DSH button opens it.
        h("input", { ref: input, type: "file", accept: ".json,application/json", hidden: true, onChange: choose }),
        h(Field, { label: "\u6A21\u677F\u6587\u4EF6" }, h("div", { className: "nwc-inline" }, h(Button, { size: "sm", variant: "outline", onClick: () => input.current?.click() }, file ? "\u91CD\u65B0\u9009\u62E9" : "\u9009\u62E9\u6587\u4EF6"), h("span", { className: file ? "" : "nwc-sub" }, file || "\u672A\u9009\u62E9\uFF08.json\uFF0C300 KB \u4EE5\u5185\uFF09"))),
        template && (template.sources || []).map((s) => h(Pick, { key: s.alias, label: "\u6A21\u677F\u4E2D\u7684\u300C" + s.alias + "\u300D\u5BF9\u5E94", value: bindings[s.alias] || "", items: sources.map((x) => ({ id: x.id, label: x.name })), onChange: (id) => setBindings((b) => ({ ...b, [s.alias]: id })) })),
        template && !(template.sources || []).length && h("p", { className: "nwc-sub" }, "\u6A21\u677F\u6CA1\u6709\u58F0\u660E\u6570\u636E\u6E90\u3002"),
        error && h("p", { role: "alert", className: "nwc-error" }, error)
      );
    }
    const EXCEL_RESULT = { added: ["\u65B0\u589E", "success"], updated: ["\u66F4\u65B0", "info"], unchanged: ["\u65E0\u53D8\u5316", "quiet"], review: ["\u5F85\u590D\u6838", "warning"], rejected: ["\u88AB\u62D2", "error"] };
    function downloadBase64(name, b64, type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") {
      const bin = atob(b64), bytes = new Uint8Array(bin.length);
      for (let k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
      const url = URL.createObjectURL(new Blob([bytes], { type })), a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1e3);
    }
    const readBase64 = (file) => new Promise((ok, fail) => {
      const r = new FileReader();
      r.onload = () => ok(String(r.result).split(",")[1] || "");
      r.onerror = () => fail(Error("\u8BFB\u53D6\u6587\u4EF6\u5931\u8D25\u3002"));
      r.readAsDataURL(file);
    });
    function ExcelImport({ project, reload, say, rev }) {
      const input = React.useRef(null), [busy, setBusy] = React.useState(""), [preview, setPreview] = React.useState(null), [filter, setFilter] = React.useState("all"), [history, setHistory] = React.useState(null), [error, setError] = React.useState("");
      const loadHistory = () => request("excel_history", { business_project: project.id }).then((v) => setHistory(v.items || [])).catch(() => setHistory([]));
      React.useEffect(() => {
        loadHistory();
      }, []);
      async function run(kind, fn) {
        setBusy(kind);
        setError("");
        try {
          await fn();
        } catch (e) {
          setError(e.message);
        } finally {
          setBusy("");
        }
      }
      const template = (prefill) => run(prefill ? "prefill" : "blank", async () => {
        const v = await request("excel_template", { business_project: project.id, prefill });
        downloadBase64(v.name, v.content_base64);
      });
      async function choose(e) {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        if (!/\.xlsx$/i.test(f.name)) {
          setError("\u8BF7\u9009\u62E9 .xlsx \u683C\u5F0F\u7684\u6587\u4EF6\u3002");
          return;
        }
        if (f.size > 5 * 1048576) {
          setError("\u6587\u4EF6\u8D85\u8FC7 5 MB\u3002");
          return;
        }
        await run("preview", async () => {
          setPreview(null);
          const v = await request("excel_import", { business_project: project.id, mode: "preview", name: f.name, content_base64: await readBase64(f) });
          setPreview({ ...v, file: f.name });
          setFilter(v.summary?.rejected ? "rejected" : "all");
        });
      }
      const apply = () => run("apply", async () => {
        const v = await request("excel_import", { business_project: project.id, mode: "apply", token: preview.token, expected_revision: rev() });
        await reload();
        setPreview(null);
        loadHistory();
        const c = v.import_counts || {};
        say("\u5DF2\u5199\u5165\uFF1A\u65B0\u589E " + (c.added || 0) + " \u6761\uFF0C\u66F4\u65B0 " + (c.updated || 0) + " \u6761\u3002\u5C1A\u672A\u751F\u6548\uFF0C\u8BF7\u70B9\u9876\u90E8\u300C\u672A\u751F\u6548\u7684\u6539\u52A8\u300D\u6838\u5BF9\u540E\u751F\u6548\u3002");
      });
      const sum = preview?.summary || {}, writable = (sum.added || 0) + (sum.updated || 0) + (sum.review || 0);
      const rows = (preview?.rows || []).filter((r) => filter === "all" || r.result === filter);
      const counts = (x) => Object.entries(EXCEL_RESULT).filter(([k]) => x[k]).map(([k, [l]]) => l + " " + x[k]).concat(x.missing ? ["\u8868\u4E2D\u672A\u5217\u51FA " + x.missing] : []).join(" \xB7 ") || "\u6CA1\u6709\u53D8\u5316";
      return h(
        React.Fragment,
        null,
        h(
          Card,
          { title: "\u6A21\u677F", actions: h(React.Fragment, null, h(Button, { size: "sm", variant: "outline", disabled: Boolean(busy), onClick: () => template(false) }, busy === "blank" ? "\u6B63\u5728\u751F\u6210\u2026" : "\u4E0B\u8F7D\u7A7A\u767D\u6A21\u677F"), h(Button, { size: "sm", variant: "outline", disabled: Boolean(busy), onClick: () => template(true) }, busy === "prefill" ? "\u6B63\u5728\u751F\u6210\u2026" : "\u4E0B\u8F7D\u542B\u5DF2\u6709\u53E3\u5F84\u7684\u6A21\u677F")) },
          h("p", { className: "nwc-sub nwc-pad" }, "\u5728\u6A21\u677F\u91CC\u586B\u5199\u6216\u4FEE\u6539\u4E1A\u52A1\u53E3\u5F84\uFF0C\u518D\u4E0A\u4F20\u3002\u4E0A\u4F20\u540E\u5148\u9884\u89C8\u9010\u884C\u68C0\u67E5\u7ED3\u679C\uFF0C\u786E\u8BA4\u540E\u624D\u5199\u5165\u3002")
        ),
        h(
          Card,
          { title: "\u4E0A\u4F20\u4E0E\u9884\u89C8", actions: h(
            React.Fragment,
            null,
            h("input", { ref: input, type: "file", accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", hidden: true, onChange: choose }),
            preview?.receipt_base64 && h(Button, { size: "sm", variant: "outline", onClick: () => downloadBase64("\u6821\u9A8C\u56DE\u6267_" + preview.file, preview.receipt_base64) }, "\u4E0B\u8F7D\u6821\u9A8C\u56DE\u6267"),
            h(Button, { size: "sm", variant: preview ? "outline" : "primary", disabled: Boolean(busy), onClick: () => input.current?.click() }, busy === "preview" ? "\u6B63\u5728\u68C0\u67E5\u2026" : preview ? "\u91CD\u65B0\u9009\u62E9\u6587\u4EF6" : "\u9009\u62E9 .xlsx \u6587\u4EF6"),
            preview && h(Button, { size: "sm", variant: "primary", disabled: Boolean(busy) || !writable, onClick: apply }, busy === "apply" ? "\u6B63\u5728\u5199\u5165\u2026" : "\u5199\u5165")
          ) },
          error && h("p", { role: "alert", className: "nwc-error nwc-pad" }, error),
          !preview ? h("p", { className: "nwc-sub nwc-pad" }, "\u8FD8\u6CA1\u6709\u4E0A\u4F20\u6587\u4EF6\uFF08.xlsx\uFF0C5 MB \u4EE5\u5185\uFF09\u3002") : h(
            React.Fragment,
            null,
            h(
              "div",
              { className: "nwc-pad nwc-inline" },
              h("span", { className: "nwc-strong" }, preview.file),
              h("span", { className: "nwc-sub" }, counts(sum)),
              h(FilterMenu, { label: "\u7ED3\u679C", value: filter, onChange: setFilter, items: [["all", "\u5168\u90E8"], ...Object.entries(EXCEL_RESULT).map(([k, [l]]) => [k, l])].map(([id, l]) => ({ id, label: l + "\uFF08" + (id === "all" ? (preview.rows || []).length : sum[id] || 0) + "\uFF09" })) })
            ),
            (preview.unknown_columns || []).length > 0 && h("div", { className: "nwc-pad" }, h("div", { className: "nwc-notice is-warning" }, "\u8FD9\u4E9B\u5217\u4E0D\u8BA4\u8BC6\uFF0C\u5DF2\u5FFD\u7565\uFF1A" + preview.unknown_columns.join("\u3001"))),
            sum.rejected > 0 && h("p", { className: "nwc-sub nwc-pad" }, "\u88AB\u62D2\u7684\u884C\u4E0D\u4F1A\u5199\u5165\uFF1B\u53EF\u4E0B\u8F7D\u6821\u9A8C\u56DE\u6267\uFF0C\u6539\u597D\u540E\u91CD\u65B0\u4E0A\u4F20\u3002"),
            h(Table, { rowKey: (r) => r.row + ":" + r.code, rows, empty: "\u6CA1\u6709\u7B26\u5408\u7B5B\u9009\u7684\u884C", columns: [
              { key: "row", title: "\u884C", width: "50px", render: (r) => String(r.row) },
              { key: "name", title: "\u53E3\u5F84", render: (r) => h("div", null, h("div", { className: "nwc-strong", title: r.name }, r.name || "\u2014"), h("div", { className: "nwc-sub", title: r.code }, (r.code || "") + (r.version ? " \xB7 v" + r.version : ""))) },
              { key: "result", title: "\u7ED3\u679C", width: "80px", render: (r) => {
                const [l, t] = EXCEL_RESULT[r.result] || [r.result, "neutral"];
                return h(Tag, { tone: t }, l);
              } },
              { key: "msg", title: "\u539F\u56E0", render: (r) => {
                const t = (r.messages || []).map((m) => m.text).join("\uFF1B");
                return h("span", { className: (r.messages || []).some((m) => m.level === "reject") ? "nwc-error-text" : void 0, title: t }, t || "\u2014");
              } }
            ] })
          )
        ),
        h(Card, { title: "\u5BFC\u5165\u8BB0\u5F55" }, history === null ? h(Empty, null, "\u6B63\u5728\u8BFB\u53D6\u2026") : h(Table, { rowKey: (x, k) => k, rows: history, empty: "\u8FD8\u6CA1\u6709\u5BFC\u5165\u8BB0\u5F55", columns: [
          { key: "at", title: "\u65F6\u95F4", width: "110px", render: (x) => shortTime(x.at) },
          { key: "name", title: "\u6587\u4EF6", render: (x) => h("span", { title: x.name }, x.name) },
          { key: "actor", title: "\u64CD\u4F5C\u4EBA", optional: true, width: "130px", render: (x) => h("span", { title: x.actor }, x.actor) },
          { key: "counts", title: "\u7ED3\u679C", render: (x) => {
            const t = counts(x);
            return h("span", { title: t }, t);
          } }
        ] }))
      );
    }
    const TARGET_TYPE_NAMES = { metric: "\u6307\u6807", example: "\u793A\u4F8B / \u4EE3\u8868\u95EE\u9898", verification_record: "\u9A8C\u8BC1\u8BB0\u5F55" };
    function Changes({ cfg, project, editor, act, pending, say, onClose }) {
      const [check, setCheck] = React.useState(null), [busy, setBusy] = React.useState(""), [error, setError] = React.useState("");
      const [impact, setImpact] = React.useState(null), [loadingImpact, setLoadingImpact] = React.useState(false);
      const tone = { \u65B0\u589E: "success", \u4FEE\u6539: "info", \u5220\u9664: "error", \u79FB\u9664: "error" };
      const loadImpact = async () => {
        setLoadingImpact(true);
        try {
          const r = await request("admin_validation", { business_project: project.id, action: "impact", draft: draftOf(project) });
          setImpact(r.impact);
        } catch (_) {
        } finally {
          setLoadingImpact(false);
        }
      };
      React.useEffect(() => {
        loadImpact();
      }, [project.id]);
      async function run(kind, fn) {
        setBusy(kind);
        setError("");
        try {
          await fn();
        } catch (e) {
          setError(e.message);
        } finally {
          setBusy("");
        }
      }
      const validate = () => run("check", async () => {
        setCheck(await request("admin_check", { business_project: project.id, expected_revision: cfg.revision, draft: draftOf(project) }));
        await loadImpact();
      });
      const apply = () => run("apply", async () => {
        await act("publish", {}, "\u6539\u52A8\u5DF2\u751F\u6548\u3002");
        onClose();
      });
      const discard = () => run("discard", async () => {
        await act("draft", { draft: structuredClone(published(project)) }, "\u5DF2\u653E\u5F03\u672A\u751F\u6548\u7684\u6539\u52A8\u3002");
        onClose();
      });
      return h(
        Dialog,
        { open: true, wide: true, title: "\u672A\u751F\u6548\u7684\u6539\u52A8\u4E0E\u5F71\u54CD\u9884\u89C8", onClose, footer: h(
          "div",
          { className: "nwc-dialog-footer" },
          h("div", { className: "nwc-actions" }, editor && project.published_version && h(Button, { size: "sm", variant: "ghost", className: "nwc-danger-text", disabled: Boolean(busy), onClick: discard }, busy === "discard" ? "\u6B63\u5728\u653E\u5F03\u2026" : "\u653E\u5F03\u8FD9\u4E9B\u6539\u52A8")),
          h("div", { className: "nwc-actions" }, editor && h(Button, { size: "sm", variant: "outline", disabled: Boolean(busy), onClick: validate }, busy === "check" ? "\u6B63\u5728\u68C0\u67E5\u2026" : "\u68C0\u67E5\u4E0E\u4F9D\u8D56\u5206\u6790"), editor && h(Button, { size: "sm", variant: "primary", disabled: Boolean(busy), onClick: apply }, busy === "apply" ? "\u6B63\u5728\u751F\u6548\u2026" : "\u5168\u90E8\u751F\u6548"))
        ) },
        h("p", { className: "nwc-sub" }, project.published_version ? "\u4E0B\u9762\u7684\u6539\u52A8\u5DF2\u4FDD\u5B58\u5230\u8349\u7A3F\uFF0C\u5C1A\u672A\u5E94\u7528\u3002\u7EAF\u53EA\u8BFB\u5F71\u54CD\u9884\u89C8\uFF0C\u4E0D\u5199\u53D1\u5E03\u914D\u7F6E\uFF1B\u95EE\u6570\u4ECD\u4F7F\u7528 " + time(liveEntry(project)?.at) + " \u751F\u6548\u7684\u914D\u7F6E\u3002" : "\u4E0B\u9762\u7684\u6539\u52A8\u5C1A\u672A\u5E94\u7528\uFF0C\u6B64\u9879\u76EE\u8FD8\u4E0D\u80FD\u7528\u4E8E\u95EE\u6570\u3002"),
        check && h("div", { role: check.valid ? "status" : "alert", className: "nwc-notice" + (check.valid ? "" : " is-error") }, check.valid ? check.message || "\u68C0\u67E5\u901A\u8FC7\u3002" : h("ul", null, check.errors.map((e, k) => h("li", { key: k }, e.message)))),
        h(
          Section,
          { title: "\u672A\u751F\u6548\u6539\u52A8\u9879\uFF08" + pending.length + "\uFF09" },
          h(Table, { rowKey: (r, k) => k, rows: pending, empty: "\u6CA1\u6709\u672A\u751F\u6548\u7684\u6539\u52A8", columns: [{ key: "area", title: "\u5206\u7C7B", width: "120px" }, { key: "kind", title: "\u53D8\u66F4", width: "70px", render: (r) => h(Tag, { tone: tone[r.kind] || "neutral" }, r.kind) }, { key: "name", title: "\u6761\u76EE", render: (r) => h("span", { title: r.name }, r.name) }] })
        ),
        loadingImpact ? h("p", { className: "nwc-sub nwc-pad" }, "\u6B63\u5728\u57FA\u4E8E SQL AST \u4E0E\u8BED\u4E49\u5408\u540C\u5206\u6790\u4F9D\u8D56\u4E0E\u5F71\u54CD\u2026") : impact && h(
          React.Fragment,
          null,
          h(
            Section,
            { title: "\u5DF2\u77E5\u53D7\u5F71\u54CD\u9879\uFF08" + (impact.known_impacts?.length || 0) + "\uFF09", extra: impact.known_impacts?.length > 0 && h(Tag, { tone: "error" }, "\u9700\u91CD\u65B0\u590D\u6D4B/\u5DF2\u5931\u6548") },
            h(Table, { rowKey: (r, k) => k, rows: impact.known_impacts || [], empty: "\u672A\u68C0\u6D4B\u5230\u56E0\u672C\u6B21\u53D8\u66F4\u76F4\u63A5\u5931\u6548\u7684\u6307\u6807\u3001\u793A\u4F8B\u6216\u9A8C\u8BC1\u8BB0\u5F55", columns: [
              { key: "target_type", title: "\u5BF9\u8C61\u7C7B\u578B", width: "110px", render: (r) => TARGET_TYPE_NAMES[r.target_type] || r.target_type },
              { key: "name", title: "\u6761\u76EE / \u4EE3\u8868\u95EE\u9898", width: "200px", render: (r) => h("span", { className: "nwc-strong", title: r.name }, r.name) },
              { key: "impact_label", title: "\u5F71\u54CD\u5224\u5B9A", width: "110px", render: (r) => h(Tag, { tone: "error" }, r.impact_label) },
              { key: "reason", title: "\u539F\u56E0", render: (r) => h("span", { title: r.reason }, r.reason) }
            ] })
          ),
          impact.pending_dependencies?.length > 0 && h(
            Section,
            { title: "\u5F85\u6838\u5BF9\u4F9D\u8D56\uFF08" + impact.pending_dependencies.length + "\uFF09", extra: h(Tag, { tone: "warning" }, "\u5F85\u6838\u5BF9") },
            h(Table, { rowKey: (r, k) => k, rows: impact.pending_dependencies, empty: "\u65E0\u5F85\u6838\u5BF9\u4F9D\u8D56", columns: [
              { key: "target_type", title: "\u5BF9\u8C61\u7C7B\u578B", width: "110px", render: (r) => TARGET_TYPE_NAMES[r.target_type] || r.target_type },
              { key: "name", title: "\u6761\u76EE / \u4EE3\u8868\u95EE\u9898", width: "200px", render: (r) => h("span", { className: "nwc-strong", title: r.name }, r.name) },
              { key: "impact_label", title: "\u5173\u6CE8\u70B9", width: "110px", render: (r) => h(Tag, { tone: "warning" }, r.impact_label) },
              { key: "reason", title: "\u539F\u56E0", render: (r) => h("span", { title: r.reason }, r.reason) }
            ] })
          ),
          h("p", { className: "nwc-sub nwc-pad" }, "\u5F71\u54CD\u8303\u56F4\u8BF4\u660E\uFF1A\u5171\u6709 " + (impact.summary?.unaffected_count || 0) + " \u9879\u65E0\u76F4\u63A5\u4F9D\u8D56\u7684\u6307\u6807\u4E0E\u9A8C\u8BC1\u8BB0\u5F55\u7EE7\u7EED\u4FDD\u6301\u6709\u6548\u3002")
        ),
        error && h("p", { role: "alert", className: "nwc-error" }, error)
      );
    }
    function Versions({ project, editor, admin, act }) {
      const [dialog, setDialog] = React.useState(null);
      const { historyImpact, loadingImpact } = dialog || {};
      const rows = [...project.versions].reverse();
      const scopeChanged = (v) => ["source_ids", "tables"].some((f) => JSON.stringify(v.config[f]) !== JSON.stringify(draftOf(project)[f]));
      const historyChanges = (v) => {
        if (Array.isArray(v.changes)) return v.changes;
        const previous = project.versions.filter((x) => x.version < v.version).sort((a, b) => b.version - a.version)[0];
        return previous ? changes({ draft: v.config, published_version: previous.version, versions: [previous] }) : null;
      };
      const summary = (v) => {
        const list = historyChanges(v);
        if (!list) return v.note || "\u5386\u53F2\u914D\u7F6E";
        if (!list.length) return v.note || "\u914D\u7F6E\u672A\u53D8\u5316";
        return list.slice(0, 2).map((x) => x.kind + "\u300C" + x.name + "\u300D").join("\uFF1B") + (list.length > 2 ? " \u7B49 " + list.length + " \u9879" : "");
      };
      const openDetails = (row) => {
        const detail = { kind: "details", row, loadingImpact: editor };
        setDialog(detail);
        if (!editor) return;
        const finish = (historyImpact2) => setDialog((current) => current === detail ? { ...detail, loadingImpact: false, historyImpact: historyImpact2 } : current);
        request("admin_validation", { business_project: project.id, action: "impact", version: row.version }).then((res) => finish(res.impact), () => finish(null));
      };
      return h(
        React.Fragment,
        null,
        h(Toolbar, null, h("span", { className: "nwc-sub" }, "\u4FDD\u7559\u6700\u8FD1 3 \u4E2A\u6062\u590D\u70B9\u3002\u66F4\u65E9\u8BB0\u5F55\u5B8C\u6574\u5F52\u6863\uFF0C\u76F8\u540C\u5185\u5BB9\u4E0D\u91CD\u590D\u8BB0\u5F55\uFF0C\u6062\u590D\u64CD\u4F5C\u4E5F\u4F1A\u7559\u4E0B\u8BB0\u5F55\u3002")),
        h(Card, null, h(Table, { rowKey: (v) => v.version, rows, empty: "\u8FD8\u6CA1\u6709\u751F\u6548\u8BB0\u5F55", columns: [
          { key: "at", title: "\u751F\u6548\u65F6\u95F4", width: "180px", render: (v) => h("span", null, shortTime(v.at), v.version === project.published_version && h(Tag, { tone: "success" }, "\u5F53\u524D")) },
          { key: "summary", title: "\u53D8\u66F4\u5185\u5BB9", render: (v) => {
            const label = summary(v);
            return h("div", null, h("span", { title: label }, label), v.restored_from_at && h("div", { className: "nwc-sub" }, "\u6062\u590D\u81EA " + time(v.restored_from_at)));
          } },
          { key: "actor", title: "\u4FEE\u6539\u4EBA", width: "140px", render: (v) => v.actor || "\u2014" },
          { key: "ops", title: "\u64CD\u4F5C", width: "180px", wrap: true, render: (v) => h(RowActions, { items: [
            { label: "\u67E5\u770B\u6539\u52A8", onClick: () => openDetails(v) },
            editor && v.version !== project.published_version && { label: "\u6062\u590D\u6B64\u914D\u7F6E", disabled: !admin && scopeChanged(v), onClick: () => setDialog({ kind: "restore", row: v }) }
          ] }) }
        ] })),
        dialog?.kind === "details" && h(
          Dialog,
          { open: true, wide: true, title: time(dialog.row.at) + " \u7684\u914D\u7F6E\u53D8\u66F4\u4E0E\u5F71\u54CD\u8BE6\u60C5", onClose: () => setDialog(null) },
          h("p", { className: "nwc-sub" }, "\u4FEE\u6539\u4EBA\uFF1A" + (dialog.row.actor || "\u2014") + "\u3002" + (dialog.row.note || "")),
          h(
            Section,
            { title: "\u53D8\u66F4\u6761\u76EE" },
            historyChanges(dialog.row) ? h(Table, { rowKey: (r, k) => k, rows: historyChanges(dialog.row), empty: "\u672C\u6B21\u6CA1\u6709\u5185\u5BB9\u53D8\u5316", columns: [{ key: "area", title: "\u5206\u7C7B", width: "130px" }, { key: "kind", title: "\u53D8\u66F4", width: "70px" }, { key: "name", title: "\u6761\u76EE" }] }) : h(Empty, null, "\u8FD9\u6761\u65E7\u8BB0\u5F55\u6CA1\u6709\u9010\u9879\u53D8\u66F4\u6458\u8981\uFF0C\u5B8C\u6574\u914D\u7F6E\u4ECD\u53EF\u6062\u590D\u3002")
          ),
          loadingImpact ? h("p", { className: "nwc-sub nwc-pad" }, "\u6B63\u5728\u57FA\u4E8E\u4F9D\u8D56\u56FE\u5206\u6790\u8BE5\u6B21\u53D8\u66F4\u7684\u5177\u4F53\u5F71\u54CD\u2026") : historyImpact && h(
            React.Fragment,
            null,
            h(
              Section,
              { title: "\u53D7\u672C\u6B21\u53D8\u66F4\u5F71\u54CD\u9879\uFF08" + (historyImpact.known_impacts?.length || 0) + "\uFF09", extra: historyImpact.known_impacts?.length > 0 && h(Tag, { tone: "error" }, "\u5DF2\u5931\u6548/\u53E3\u5F84\u53D8\u5316") },
              h(Table, { rowKey: (r, k) => k, rows: historyImpact.known_impacts || [], empty: "\u672C\u6B21\u53D8\u66F4\u672A\u76F4\u63A5\u5BFC\u81F4\u65E2\u6709\u6307\u6807\u6216\u9A8C\u8BC1\u8BB0\u5F55\u5931\u6548", columns: [
                { key: "target_type", title: "\u5BF9\u8C61\u7C7B\u578B", width: "110px", render: (r) => TARGET_TYPE_NAMES[r.target_type] || r.target_type },
                { key: "name", title: "\u6761\u76EE / \u4EE3\u8868\u95EE\u9898", width: "200px", render: (r) => h("span", { className: "nwc-strong", title: r.name }, r.name) },
                { key: "impact_label", title: "\u5F71\u54CD\u5224\u5B9A", width: "110px", render: (r) => h(Tag, { tone: "error" }, r.impact_label) },
                { key: "reason", title: "\u539F\u56E0", render: (r) => h("span", { title: r.reason }, r.reason) }
              ] })
            ),
            historyImpact.pending_dependencies?.length > 0 && h(
              Section,
              { title: "\u5F85\u6838\u5BF9\u4F9D\u8D56\uFF08" + historyImpact.pending_dependencies.length + "\uFF09", extra: h(Tag, { tone: "warning" }, "\u5F85\u6838\u5BF9") },
              h(Table, { rowKey: (r, k) => k, rows: historyImpact.pending_dependencies, empty: "\u65E0\u5F85\u6838\u5BF9\u4F9D\u8D56", columns: [
                { key: "target_type", title: "\u5BF9\u8C61\u7C7B\u578B", width: "110px", render: (r) => TARGET_TYPE_NAMES[r.target_type] || r.target_type },
                { key: "name", title: "\u6761\u76EE / \u4EE3\u8868\u95EE\u9898", width: "200px", render: (r) => h("span", { className: "nwc-strong", title: r.name }, r.name) },
                { key: "impact_label", title: "\u5173\u6CE8\u70B9", width: "110px", render: (r) => h(Tag, { tone: "warning" }, r.impact_label) },
                { key: "reason", title: "\u539F\u56E0", render: (r) => h("span", { title: r.reason }, r.reason) }
              ] })
            ),
            h("p", { className: "nwc-sub nwc-pad" }, "\u5F71\u54CD\u8303\u56F4\u8BF4\u660E\uFF1A\u5F53\u65F6\u5171\u6709 " + (historyImpact.summary?.unaffected_count || 0) + " \u9879\u65E0\u76F4\u63A5\u4F9D\u8D56\u7684\u6307\u6807\u4E0E\u9A8C\u8BC1\u8BB0\u5F55\u7EE7\u7EED\u4FDD\u6301\u6709\u6548\u3002")
          )
        ),
        dialog?.kind === "restore" && h(Confirm, { title: "\u6062\u590D\u5386\u53F2\u914D\u7F6E", description: "\u5C06\u6062\u590D\u5230 " + time(dialog.row.at) + " \u7684\u914D\u7F6E\u5E76\u7ACB\u5373\u751F\u6548\u3002\u5F53\u524D\u914D\u7F6E\u548C\u672A\u5E94\u7528\u7684\u6539\u52A8\u4F1A\u88AB\u66FF\u6362\uFF1B\u672C\u6B21\u6062\u590D\u4F1A\u8BB0\u5F55\u5230\u53D8\u66F4\u5386\u53F2\u3002", confirmLabel: "\u6062\u590D\u5E76\u751F\u6548", onClose: () => setDialog(null), onConfirm: () => act("rollback", { version: dialog.row.version }, "\u5386\u53F2\u914D\u7F6E\u5DF2\u6062\u590D\u5E76\u751F\u6548\u3002") })
      );
    }
    return { Console, changes, workbookMetricRows };
  }
  var consoleCss = `
.nwc{container-type:inline-size;display:grid;grid-template-columns:auto minmax(0,1fr);height:100%;min-height:0;color:var(--dsw-alias-label-primary);background:inherit;font-size:13px}
.nwc-loading{grid-column:1/-1;padding:32px;display:flex;gap:12px;align-items:center}
.nwc-nav{width:216px;box-sizing:border-box;border-right:1px solid var(--dsw-alias-border-l2);overflow:auto;padding:16px 10px;display:flex;flex-direction:column;gap:14px}
.nwc-brand{display:flex;align-items:center;justify-content:space-between;padding:0 6px 6px;font-size:15px;font-weight:500}.nwc-brand-version{font-size:12px;font-weight:400;color:var(--dsw-alias-label-secondary);white-space:nowrap}
.nwc-nav-title{font-size:12px;color:var(--dsw-alias-label-secondary);padding:0 8px 4px}
.nwc-nav-item{all:unset;box-sizing:border-box;display:flex;align-items:center;gap:8px;width:100%;padding:7px 8px;border-radius:8px;cursor:pointer;color:var(--dsw-alias-label-primary)}
.nwc-nav-item:hover{background:var(--dsw-alias-fill-l1,rgba(127,127,127,.08))}
.nwc-nav-item.is-active{background:var(--dsw-alias-fill-l2,rgba(127,127,127,.14));font-weight:500}
.nwc-nav-item:focus-visible{outline:2px solid var(--dsw-alias-border-focus,currentColor);outline-offset:-2px}
.nwc-nav-icon{width:16px;display:inline-flex;color:var(--dsw-alias-label-secondary)}
.nwc-count{margin-left:auto;font-size:11px;padding:0 6px;border-radius:9px;background:var(--dsw-alias-state-warning-primary,#d97706);color:#fff}
.nwc-main{display:flex;flex-direction:column;min-width:0;min-height:0;container:nwc-main/inline-size}
.nwc-top{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 24px;border-bottom:1px solid var(--dsw-alias-border-l2)}
.nwc-crumb{display:flex;align-items:baseline;gap:8px;color:var(--dsw-alias-label-secondary);min-width:0}
.nwc-crumb h1{font-size:18px;font-weight:500;margin:0;color:var(--dsw-alias-label-primary)}
.nwc-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.nwc-notice{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:12px 24px 0;padding:8px 12px;border:1px solid var(--dsw-alias-border-l2);border-radius:8px}
.nwc-notice>span{flex:1;min-width:0}
.nwc-notice>button{flex-shrink:0;white-space:nowrap}
.nwc-notice.is-error{border-color:var(--dsw-alias-state-error-primary);color:var(--dsw-alias-state-error-primary)}
.nwc-notice ul{margin:0;padding-left:18px}
.nwc-content .nwc-notice,.nwc-dialog-body .nwc-notice{margin:0}
.nwc-notice.is-warning{border-color:var(--dsw-alias-state-warning-primary)}
.nwc-notice.is-info{color:var(--dsw-alias-label-secondary)}
.nwc-content{flex:1;min-height:0;overflow:auto;padding:16px 24px 32px;display:flex;flex-direction:column;gap:14px}
.nwc-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:nowrap;min-height:36px}
.nwc-toolbar-end{display:flex;align-items:center;gap:8px;margin-left:auto;flex-shrink:0}
.nwc-row-menu{min-width:140px}
.nwc-pick-anchor{display:flex!important;width:100%}
.nwc-pick-menu{min-width:320px;width:max-content!important;max-width:min(560px,90vw)!important;max-height:360px!important;overflow-y:auto!important}
.nwc-pick-menu [role=menuitem]{height:auto!important;min-height:32px;align-items:center;white-space:normal!important}
.nwc-pick-menu [role=menuitem] *{white-space:normal!important;text-overflow:clip!important}
.nwc-opt{display:flex;flex-direction:column;gap:1px;min-width:0;padding:3px 0;overflow-wrap:anywhere}
.nwc-opt-sub{font-size:12px;color:var(--dsw-alias-label-secondary)}
.nwc-toolbar>.nwc-sub{flex:1;min-width:0}
.nwc-search{width:280px;max-width:100%}
.nwc-toolbar>.nwc-search{width:auto;flex:0 1 280px;min-width:160px}
.nwc-pills{display:flex;gap:6px;flex-wrap:nowrap;flex-shrink:0}
.nwc-card{border:1px solid var(--dsw-alias-border-l2);border-radius:10px;padding:4px 0;min-width:0}
.nwc-card-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 16px}
.nwc-card-head h3{margin:0;font-size:14px;font-weight:500}
.nwc-card>.nwc-text,.nwc-card>.nwc-empty{padding:0 16px 12px}
.nwc-card .nw-setting-section,.nwc-card .nw-connections,.nwc-card>.nw-setting-row{padding:0 16px}
.nwc-table-wrap{overflow:auto}
.nwc-table{width:100%;border-collapse:collapse;table-layout:fixed}
.nwc-table th{white-space:nowrap;text-align:left;font-weight:500;font-size:12px;color:var(--dsw-alias-label-secondary);padding:8px 12px;border-bottom:1px solid var(--dsw-alias-border-l2)}
.nwc-table td{white-space:nowrap;overflow:hidden;padding:9px 12px;border-bottom:1px solid var(--dsw-alias-border-l2);vertical-align:middle}
.nwc-table tr:last-child td{border-bottom:none}
.nwc-cell{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
.nwc-cell .nwc-sub,.nwc-cell .nwc-strong{max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nwc-cell .nwc-strong{display:block}
.nwc-cell .nwc-name-line{display:flex;align-items:center;gap:6px;min-width:0}
.nwc-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nwc-name-line>:not(.nwc-name){flex-shrink:0}
.nwc-table td.nwc-ops{overflow:visible}
.nwc-row-actions{display:flex;gap:2px;flex-wrap:nowrap}
.nwc-table .nwc-ops{position:sticky;right:0;background:var(--dsw-alias-bg-base,#fff);box-shadow:-1px 0 0 var(--dsw-alias-border-l2)}
.nwc-strong{font-weight:500;display:inline-flex;gap:6px;align-items:center}
.nwc-sub{font-size:12px;color:var(--dsw-alias-label-secondary);overflow:hidden;text-overflow:ellipsis}
.nwc-mono{font-family:var(--dsw-font-mono,ui-monospace,Menlo,monospace);font-size:12px}
.nwc-ellipsis{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:240px;display:inline-block;vertical-align:middle}
.nwc-empty{color:var(--dsw-alias-label-secondary);padding:14px 16px;margin:0}
.nwc-text{margin:0;line-height:1.6}
.nwc-error{color:var(--dsw-alias-state-error-primary);margin:8px 0 0}
.nwc-danger-text{color:var(--dsw-alias-state-error-primary)}
.nwc-danger-button{color:var(--dsw-alias-state-error-primary);border-color:var(--dsw-alias-state-error-primary)}
.nwc-stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}
@media(max-width:1100px){.nwc-stats{grid-template-columns:repeat(3,minmax(0,1fr))}}
.nwc-stat{all:unset;box-sizing:border-box;border:1px solid var(--dsw-alias-border-l2);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:4px;cursor:pointer}
.nwc-stat:disabled{cursor:default}
.nwc-stat:not(:disabled):hover{background:var(--dsw-alias-fill-l1,rgba(127,127,127,.08))}
.nwc-stat-value{font-size:20px;font-weight:500}
.nwc-stat-label{font-size:12px;color:var(--dsw-alias-label-secondary)}
.nwc-tabs{display:flex}
.nwc-field{display:flex;flex-direction:column;gap:6px;min-width:0}
.nwc-field-label{font-size:12px;color:var(--dsw-alias-label-secondary)}
.nwc-field-hint{font-size:12px;color:var(--dsw-alias-label-secondary)}
.nwc-pick{justify-content:space-between;width:100%;max-width:100%}
.nwc-textarea{box-sizing:border-box;width:100%;resize:vertical;font:inherit;color:inherit;background:transparent;border:1px solid var(--dsw-alias-border-l2);border-radius:8px;padding:8px 10px;line-height:1.5}
.nwc-textarea:focus{outline:2px solid var(--dsw-alias-border-focus,currentColor);outline-offset:-1px}
.nwc-dialog-body{display:flex;flex-direction:column;gap:14px;max-height:min(62vh,640px);overflow:auto;overscroll-behavior:contain;padding:2px}
.nwc-dialog{width:min(560px,92vw)!important;max-width:92vw!important;max-height:100%}
.nwc-dialog-wide{width:min(820px,92vw)!important}
.nwc-dialog-content,.nwc-dialog-content>:last-child{min-height:0}
.nwc-diagnostic-list{display:grid;gap:8px;margin:0;padding-inline-start:20px;overflow-wrap:anywhere}
.nwc-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.nwc-form-grid-host{grid-template-columns:minmax(0,1fr) 140px}
.nwc-dialog-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%}
.nwc-pad{padding:0 16px 12px}
.nwc-form-section{display:flex;flex-direction:column;gap:12px;padding-bottom:16px;border-bottom:1px solid var(--dsw-alias-border-l2)}
.nwc-form-section:last-of-type{border-bottom:none;padding-bottom:0}
.nwc-form-section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:28px}
.nwc-form-section-head h4{margin:0;font-size:13px;font-weight:500}
.nwc-operand{display:grid;grid-template-columns:110px repeat(4,minmax(0,1fr)) 56px;gap:10px;align-items:end}
.nwc-operand-notime{grid-template-columns:110px repeat(3,minmax(0,1fr)) 56px}
.nwc-operand-remove{display:flex;align-items:center;min-height:34px}
.nwc-check-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px 14px;max-height:180px;overflow:auto}
.nwc-inline{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.nwc-list{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:6px}
.nwc-error-text{color:var(--dsw-alias-state-error-primary)}
.nwc-ok-text{color:var(--dsw-alias-state-success-primary,#16a34a)}
.nwc-footer-status{min-width:0;flex:1;flex-wrap:nowrap}
.nwc-footer-status>span{min-width:0;max-width:360px}
.nwc-field .nw-menu-anchor,.nwc-field .nw-menu-anchor>span{display:flex;width:100%}
.nwc-field .nwc-pick{width:100%;height:34px;justify-content:space-between}
.nwc-pick .nwc-ellipsis{flex:1;min-width:0;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nwc-pick svg{flex-shrink:0}
@media (max-width:900px){.nwc-operand,.nwc-operand-notime{grid-template-columns:1fr 1fr}}
.nwc-span{grid-column:1/-1}
.nwc-enum{display:flex;flex-direction:column;gap:8px}
.nwc-enum-row{display:grid;grid-template-columns:1fr auto 1fr auto;gap:8px;align-items:center}
.nwc-check-list{display:flex;flex-direction:column;max-height:46vh;overflow:auto;border:1px solid var(--dsw-alias-border-l2);border-radius:8px}
.nwc-check-row{display:flex;align-items:center;gap:12px;padding:6px 10px;border-bottom:1px solid var(--dsw-alias-border-l2)}
.nwc-check-row:last-child{border-bottom:none}
@container (max-width:860px){.nwc-nav{width:180px}.nwc-stats{grid-template-columns:repeat(3,minmax(0,1fr))}.nwc-form-grid{grid-template-columns:1fr}.nwc-top{flex-wrap:wrap}}
@container (max-width:680px){.nwc-nav{width:52px}.nwc-nav-title,.nwc-nav-item>span:not(.nwc-nav-icon):not(.nwc-count){display:none}.nwc-nav{padding:12px 6px}.nwc-brand>span{display:none}}
`;

  // plugin/packages/natural-data/result-lineage.js
  function mergeResultReceipt(prior, value) {
    if (!prior) return value;
    const merged = { ...prior, ...value };
    for (const field of ["claims", "verified_claims"]) {
      const claims = [...prior[field] || [], ...value[field] || []];
      if (claims.length) merged[field] = [...new Map(claims.map((claim) => [claim.claim_id || JSON.stringify(claim), claim])).values()];
    }
    return merged;
  }
  function mergedEvidence(evidence) {
    const byRef = /* @__PURE__ */ new Map(), other = [];
    for (const value of evidence || []) {
      if (!value?.result_ref) {
        other.push(value);
        continue;
      }
      byRef.set(value.result_ref, mergeResultReceipt(byRef.get(value.result_ref), value));
    }
    return [...other, ...byRef.values()];
  }
  function replacedResultRefs(evidence) {
    const merged = mergedEvidence(evidence), byRef = new Map(merged.map((e) => [e?.result_ref, e]));
    return new Set(merged.flatMap((e) => {
      const l = e?.result_lineage, old = byRef.get(l?.replaces_result_ref), p = old?.result_lineage;
      if (e?.status !== "succeeded" || l?.relationship !== "requested_correction" || !l.request_ref || !l.population_fingerprint || ["same_request", "same_source_configuration", "same_population"].some((k) => l.checks?.[k] !== "checked")) return [];
      if (!old || old === e || old.status !== "succeeded" || l.purpose !== "answer" || p?.purpose !== "answer" || JSON.stringify(l.request_ref) !== JSON.stringify(p.request_ref) || ["session_id", "source_id", "config_fingerprint", "population_fingerprint"].some((k) => !l[k] || l[k] !== p[k])) return [];
      return [old.result_ref];
    }));
  }

  // plugin/packages/natural-data/presentation-intent.js
  var charts = {
    "percent-stacked-bar": "(?:100\\s*%|\u767E\u5206\u6BD4|\u767E\u5206\u4E4B\u767E)\\s*\u5806\u53E0(?:\u67F1\u72B6|\u67F1\u5F62|\u6761\u5F62)?\u56FE",
    "stacked-bar": "\u5806\u53E0(?:\u67F1\u72B6|\u67F1\u5F62|\u6761\u5F62)?\u56FE",
    "grouped-bar": "\u5206\u7EC4(?:\u67F1\u72B6|\u67F1\u5F62|\u6761\u5F62)?\u56FE",
    bar: "\u6A2A\u5411(?:\u67F1\u72B6|\u67F1\u5F62)\u56FE|(?:\u6A2A\u5411)?\u6761\u5F62\u56FE",
    column: "(?:\u7AD6\u5411|\u7EB5\u5411|\u5782\u76F4)?(?:\u67F1\u72B6|\u67F1\u5F62)\u56FE",
    line: "\u6298\u7EBF\u56FE|\u8D8B\u52BF\u7EBF|\u6298\u7EBF",
    area: "\u9762\u79EF\u56FE",
    pie: "\u997C(?:\u72B6|\u5F62)?\u56FE",
    donut: "\u73AF\u5F62\u56FE|\u5706\u73AF\u56FE",
    scatter: "\u6563\u70B9\u56FE",
    bubble: "\u6C14\u6CE1\u56FE",
    heatmap: "\u70ED\u529B\u56FE",
    treemap: "\u6811\u72B6\u56FE|\u6811\u56FE",
    histogram: "\u76F4\u65B9\u56FE",
    radar: "\u96F7\u8FBE\u56FE",
    funnel: "\u6F0F\u6597\u56FE",
    gauge: "\u4EEA\u8868\u76D8",
    waterfall: "\u7011\u5E03\u56FE",
    boxplot: "\u7BB1\u7EBF\u56FE",
    kpi: "\u6307\u6807\u5361",
    "pivot-table": "\u591A\u7EF4\u900F\u89C6\u8868|\u900F\u89C6\u8868|\u4EA4\u53C9\u8868"
  };
  var table = "(?:\u8868\u683C|\u6570\u636E\u8868|\u7EDF\u8BA1\u8868|\u6C47\u603B\u8868|\u660E\u7EC6\u8868)";
  var tableName = new RegExp("^" + table + "$");
  var terms = new RegExp([table, ...Object.values(charts)].join("|"), "gu");
  var grain = "(?:(?:\u9010|\u6309)(?:\u6708|\u5B63|\u5E74)(?:\u5EA6)?)?";
  var join = new RegExp("^\\s*(?:\u548C|\u4E0E|\u4EE5\u53CA|\u53CA|\u3001)\\s*" + grain + "\\s*$");
  var also = new RegExp(
    "^(?:\\s*(?:\u663E\u793A|\u5C55\u793A|\u5448\u73B0|\u5217\u51FA))?(?:[\uFF0C,]\\s*[^\uFF0C,\u3002\uFF1B;\\n]{1,50})?[\uFF0C,\u3002\uFF1B;\\n]?\\s*(?:\u5E76\u4E14?|\u540C\u65F6|\u4E5F|\u518D(?=\u628A|\u5C06))\\s*(?:(?:\u628A|\u5C06)[^\uFF0C,\u3002\uFF1B;\\n]{0,28}?)?(?:\u5217\u6210|\u5217\u4E3A|\u6574\u7406\u6210|\u7ED9\u51FA|\u663E\u793A|\u5C55\u793A|\u63D0\u4F9B|\u8F93\u51FA|\u753B|\u7528|\u4EE5|\u9644\u4E0A)?\\s*" + grain + "\\s*$"
  );
  var rejectBefore = /(?:不要|不用|无需|无须|不需要|不必|只(?:要|用|给)|仅(?:要|用|给)|(?:改|换)(?:为|成|用))\s*(?:(?:给出|使用|绘制|展示|显示|输出|生成|做|画|用|以)\s*)?$/;
  var rejectAfter = /^\s*(?:就|都)?\s*(?:不要(?:了)?|不用了|不需要了|即可|就行|就好)(?:[，,。；;！!？?\s]|$)/;
  var chartKinds = Object.entries(charts).map(([kind, pattern]) => [kind, new RegExp("^(?:" + pattern + ")$", "u")]);
  var kindOf = (term) => tableName.test(term) ? "table" : chartKinds.find(([, pattern]) => pattern.test(term))?.[0];
  function explicitChartType(text = "") {
    const joint = jointChartType(text);
    if (joint) return joint;
    let s = String(text).normalize("NFKC");
    const change = [...s.matchAll(/(?:改|换)(?:成|为|用)|(?:只|仅)(?:要|用|给)/g)].at(-1);
    if (change && [...s.slice(change.index + change[0].length).matchAll(terms)].length) s = s.slice(change.index + change[0].length);
    const kinds = new Set([...s.matchAll(terms)].filter((term) => !rejectBefore.test(s.slice(0, term.index)) && !rejectAfter.test(s.slice(term.index + term[0].length))).map((term) => kindOf(term[0])));
    return kinds.size === 1 ? [...kinds][0] : null;
  }
  function jointChartType(text = "") {
    const s = String(text).normalize("NFKC");
    const matches = [...s.matchAll(terms)];
    if (matches.some((term) => rejectBefore.test(s.slice(0, term.index)) || rejectAfter.test(s.slice(term.index + term[0].length)))) return null;
    const kinds = /* @__PURE__ */ new Set();
    for (let i = 1; i < matches.length; i++) {
      const left = matches[i - 1], right = matches[i];
      const leftTable = tableName.test(left[0]), rightTable = tableName.test(right[0]);
      if (leftTable === rightTable) continue;
      const gap = s.slice(left.index + left[0].length, right.index);
      if (!join.test(gap) && !also.test(gap)) continue;
      const lead = s.slice(0, left.index).split(/[。；;！!？?\n]/).at(-1);
      const clause = lead + s.slice(left.index, right.index + right[0].length);
      if (/分别|各自|另一(?:份|个)|另查|另外(?:再|查)|(?:前一半|后一半)/.test(clause)) continue;
      if (/^\s*(?:或(?:者|是)?|还是)(?=[^\n。；;]*图)/.test(s.slice(right.index + right[0].length))) continue;
      const chart = leftTable ? right[0] : left[0];
      const kind = kindOf(chart);
      if (kind) kinds.add(kind);
    }
    return kinds.size === 1 ? [...kinds][0] : null;
  }
  function isExploratory(result) {
    return [result?.purpose, result?.result_lineage?.purpose].some((value) => value === "explore" || value === "probe");
  }
  var resultCaption = new RegExp("^(\\s*(?:\u672C\u8F6E|\u672C\u6B21|\u4E0B\u65B9)?(?:\u7684)?\u7ED3\u679C\u5361\\s*(?:\u5DF2(?:\u7ECF)?\\s*)?(?:\u7528|\u4EE5)\\s*)(" + terms.source + ")(\\s*(?:\u7684)?(?:\u5F62\u5F0F)?\\s*(?:\u5C55\u793A|\u663E\u793A|\u5448\u73B0|\u8F93\u51FA)[\u3002\uFF01!]?\\s*)$", "u");
  var chartActionBefore = new RegExp("(?:\u7528|\u4EE5|\u6539\u6210|\u6539\u4E3A|\u6362\u6210|\u6362\u4E3A|\u5207\u6362\u5230|\u5C55\u793A|\u663E\u793A|\u5448\u73B0|\u7ED8\u5236|\u753B|\u751F\u6210|\u7ED9\u51FA|\u63D0\u4F9B|\u9644\u4E0A|\u8981)\\s*(?:\u4E00[\u5F20\u4E2A\u4EFD\u5E45]\\s*)?" + grain + "\\s*$");

  // plugin/packages/natural-data/answer-delivery.js
  var businessLabel = /^[\p{Script=Han}\p{N} A-Za-z（）()·、，,。%％—–/-]{1,100}$/u;
  var technical = /\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b/;
  var hasChinese = /\p{Script=Han}/u;
  var plainLabel = (value) => typeof value === "string" && businessLabel.test(value) && !technical.test(value);
  function formatTable(headers, rows) {
    return [
      "| " + headers.join(" | ") + " |",
      "| " + headers.map(() => "---").join(" | ") + " |",
      ...rows.map((row) => "| " + row.join(" | ") + " |")
    ].join("\n");
  }
  function businessColumn(column) {
    return typeof column === "string" ? column.replace(/[_＿](?:亿元|万元|元|家|件|条|项|笔|个|次|台|吨|人)$/, "") : column;
  }
  function literalSqlRows(result) {
    const values = result.values, contract = result.answer_contract;
    const aggregate = contract?.kind === "sql_aggregate" && contract.complete === true && contract.result_ref === result.result_ref;
    const detail = !contract && !result.metric_contract && result.result_lineage?.purpose === "answer" && Boolean(result.receipt?.query_hash) && ["data.sql_query", "data.query"].includes(result.execution?.operation);
    if (!aggregate && !detail || values?.complete !== true || !Array.isArray(values.columns) || !values.columns.length || values.columns.length > 8 || !Array.isArray(values.rows) || values.rows.length !== result.row_count || values.rows.length !== values.row_count || !values.rows.length || values.rows.length > 80) return null;
    const columns = values.columns;
    const proof = result.validation?.annual_forecast_history;
    const reference = proof?.reference_only === true && proof.state === "checked" && proof.result_ref === result.result_ref && proof.request_ref === result.result_lineage?.request_ref && proof.source_id === result.receipt?.source_id && plainLabel(proof.measure_label) && columns.includes(proof.value_column) ? proof : null;
    const labels = columns.map((column) => column === reference?.value_column ? reference.measure_label : businessColumn(column));
    if (new Set(labels).size !== labels.length || labels.some((label) => !plainLabel(label) || !hasChinese.test(label))) return null;
    const titles = labels.map((label, index) => {
      const unit = result.analysis_options?.column_units?.[columns[index]];
      if (!plainLabel(unit) || /\(.*\)|（.*）|%|％/.test(label)) return label;
      return label + "\uFF08" + unit + "\uFF09";
    });
    let hasValues = false;
    const rows = [];
    for (const row of values.rows) {
      if (!Array.isArray(row) || row.length !== columns.length) return null;
      const formatted = [];
      for (const [index, cell] of row.entries()) {
        if (cell === null) {
          formatted.push("");
          continue;
        }
        if (Number.isFinite(cell)) {
          hasValues = true;
          const unit = result.analysis_options?.column_units?.[columns[index]];
          formatted.push(String(cell) + (unit === "%" && !/[％%]/.test(titles[index]) ? "%" : ""));
        } else if (plainLabel(cell)) {
          if (detail) hasValues = true;
          formatted.push(cell);
        } else return null;
      }
      rows.push(formatted);
    }
    if (!hasValues) return null;
    const title = reference ? reference.measure_label + "\u5386\u53F2\u8BB0\u5F55" : String(result.title || "").replace(/（合成测试）$/, "");
    return {
      title: plainLabel(title) && hasChinese.test(title) ? title : "\u67E5\u8BE2\u7ED3\u679C",
      text: formatTable(titles, rows),
      ...reference ? { reference: true } : {}
    };
  }
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
    return value;
  }
  function metricDeliveryIdentity(result) {
    const metric = result.metric_contract, scope = metric?.scope, proof = metric?.confirmation;
    const receipt = result.receipt, project = result.business_project, answer = result.answer_contract;
    const revision = result.source_provenance?.connection_revision;
    if (result.execution?.operation !== "data.metric_query" || result.status !== "succeeded" || result.truncated || isExploratory(result) || receipt?.read_only !== true || result.validation?.state === "conflict" || result.validation?.period_grain?.state === "auxiliary" || answer?.complete !== true || answer.result_ref !== result.result_ref || answer.metric_id !== metric?.id || answer.unit !== metric?.unit || result.analysis_options?.mode !== "metric_facts" || result.analysis_options.complete !== true || !metric?.id || !metric.version || !metric.unit || !proof?.definition_fingerprint || !proof.state || proof.basis !== "published_configuration" || proof.metric_id !== metric.id || proof.definition_version !== metric.version || !receipt.source_id || receipt.source_id !== proof.source_id || !project?.id || !Number.isSafeInteger(project.version) || project.version !== proof.project_version || !Number.isSafeInteger(revision) || !result.execution_scope || !Array.isArray(scope?.periods) || !Array.isArray(scope.dimensions) || !Array.isArray(scope.filters) || !Array.isArray(result.facts) || !result.facts.length || result.facts.length !== result.row_count || result.facts.some((fact) => fact?.unit !== metric.unit || typeof fact.id !== "string" || !fact.id.startsWith(result.result_ref + ":")))
      return null;
    return {
      source: receipt.source_id,
      project,
      revision,
      executionScope: result.execution_scope,
      metric: metric.id,
      version: metric.version,
      definition: proof.definition_fingerprint,
      state: proof.state,
      businessConfirmation: metric.business_confirmation,
      unit: metric.unit,
      scope,
      defaultFilters: metric.default_filters,
      limitations: answer.limitations,
      lineage: result.result_lineage?.population_fingerprint
    };
  }
  function metricDeliveryKey(result) {
    const identity = metricDeliveryIdentity(result);
    if (!identity) return null;
    const facts = result.facts.map(({ id, ...fact }) => JSON.stringify(canonical(fact))).sort();
    return JSON.stringify(canonical({ ...identity, facts }));
  }
  var profileRoles = ["group", "average", "zero", "missing"];
  var optionalProfileRoles = ["total", "positive"];
  function groupedProfile(result) {
    const contract = result.answer_contract, proof = contract?.grouped_measure_profile;
    const lineage = result.result_lineage, values = result.values;
    const project = result.business_project, receipt = result.receipt;
    if (result.status !== "succeeded" || !result.result_ref || !result.view_url || result.truncated || isExploratory(result) || receipt?.read_only !== true || !receipt.source_id || !receipt.query_hash || !result.execution_scope || lineage?.purpose !== "answer" || !lineage.request_ref || !lineage.session_id || !lineage.config_fingerprint || lineage.source_id !== receipt.source_id || !project?.id || !Number.isSafeInteger(project.version) || !["data.sql_query", "data.query"].includes(result.execution?.operation) || result.validation?.state === "conflict" || result.validation?.period_grain?.state === "auxiliary" || contract?.kind !== "sql_aggregate" || contract.complete !== true || contract.result_ref !== result.result_ref || proof?.schema_version !== 1 || !proof.signature || !proof.roles || values?.complete !== true || !Array.isArray(values.rows) || !Array.isArray(values.columns) || values.rows.length !== result.row_count || values.rows.length !== values.row_count || values.rows.length < 1 || values.rows.length > 80) return null;
    const roles = Object.keys(proof.roles).sort(), indices = roles.map((role) => proof.roles[role]);
    if (!profileRoles.every((role) => roles.includes(role)) || roles.some((role) => ![...profileRoles, ...optionalProfileRoles].includes(role)) || indices.some((index) => !Number.isSafeInteger(index) || index < 0 || index >= values.columns.length) || roles.length !== values.columns.length || new Set(indices).size !== indices.length) return null;
    const counts = (n) => Number.isSafeInteger(n) && n >= 0;
    const groups = /* @__PURE__ */ new Set();
    const { group, average, zero, missing, total } = proof.roles;
    for (const row of values.rows) {
      if (!Array.isArray(row) || row.length !== indices.length || !plainLabel(row[group]) || groups.has(row[group]) || !Number.isFinite(row[average]) && row[average] !== null || roles.some((role) => role !== "group" && role !== "average" && !counts(row[proof.roles[role]]))) return null;
      if (total !== void 0 && (row[zero] + row[missing] > row[total] || row[average] === null !== (row[missing] === row[total]))) return null;
      groups.add(row[group]);
    }
    const units = roles.map((role) => result.analysis_options?.column_units?.[values.columns[proof.roles[role]]] ?? null);
    const rows = values.rows.map((row) => JSON.stringify(indices.map((index) => row[index]))).sort();
    return { proof, values, key: JSON.stringify([
      receipt.source_id,
      result.execution_scope,
      project.id,
      project.version,
      lineage.config_fingerprint,
      lineage.session_id,
      lineage.request_ref,
      proof.signature,
      roles,
      units,
      rows
    ]) };
  }
  function uniqueSqlProfileDeliveries(results, evidence = []) {
    const hasClaims = (result) => Boolean(result.claims?.length || result.verified_claims?.length);
    const claims = new Set(evidence.filter(hasClaims).map((result) => result.result_ref));
    const selected = /* @__PURE__ */ new Map(), kept = /* @__PURE__ */ new Set();
    for (const result of results) {
      const profile = groupedProfile(result);
      if (!profile || hasClaims(result) || claims.has(result.result_ref)) {
        kept.add(result);
        continue;
      }
      const prior = selected.get(profile.key);
      if (!prior || !literalSqlRows(prior) && literalSqlRows(result)) selected.set(profile.key, result);
    }
    for (const result of selected.values()) kept.add(result);
    return results.filter((result) => kept.has(result));
  }
  function comparisonMonths(question) {
    const segments = question.split(/[。？?；;\n]/);
    const pairs = segments.flatMap((segment) => [...segment.matchAll(/((?:19|20)\d{2})年\s*(\d{1,2})月[^，,]{0,38}?比(?:较)?(?:(\d{4})年\s*)?(\d{1,2})月/g)]);
    const share = /各(?:自)?占(?:总量|总数|比)|分别占(?:总量|总数|比)|(?:各类|每类|分别).{0,8}占比/;
    const shares = segments.flatMap((segment) => {
      const match = share.exec(segment);
      if (!match) return [];
      const clause = segment.slice(0, match.index).split(/[，,]/).at(-1);
      return [...clause.matchAll(/(?:(\d{4})年\s*)?(\d{1,2})月/g)];
    });
    const shareMentions = segments.flatMap((segment) => [...segment.matchAll(/占比|占总量|占总数|份额/g)]);
    if (pairs.length !== 1 || shares.length !== 1 || shareMentions.length !== 1) return null;
    const [, year, month, previousYear, previousMonth] = pairs[0], [, shareYear, shareMonth] = shares[0];
    const period = (y, m) => Number(m) >= 1 && Number(m) <= 12 ? y + "-" + m.padStart(2, "0") : null;
    const current = period(year, month), baseline = period(previousYear || year, previousMonth);
    return current && baseline && current !== baseline && period(shareYear || year, shareMonth) === current ? { current, baseline } : null;
  }
  var sameEvidence = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
  var sameIds = (actual, expected) => Array.isArray(actual) && sameEvidence([...actual].sort(), [...expected].sort());
  var countValue = (value) => Number.isSafeInteger(value) && value >= 0;
  var closePercent = (value, expected) => expected === null ? value === null : Number.isFinite(value) && Math.abs(value - expected) < 1e-7;
  var signedCount = (value) => (value > 0 ? "+" : "") + value;
  var shareClaim = (claim) => (claim.operation === void 0 || claim.operation === "share") && Object.hasOwn(claim, "share_percent");
  function comparisonMatches(claim, before, after, unit) {
    const delta = after - before;
    return claim.baseline?.value === before && claim.current?.value === after && claim.baseline.unit === unit && claim.current.unit === unit && claim.difference === delta && claim.difference_unit === unit && closePercent(claim.change_percent, before ? delta * 100 / before : null);
  }
  function groupedComparisonSection(result, question) {
    const periods = comparisonMonths(question), metric = result.metric_contract;
    const dim = metric?.scope?.dimensions?.[0], unit = metric?.unit;
    if (!periods || !metricDeliveryKey(result) || metric.additive !== true || metric.ratio !== false || !plainLabel(metric.name) || !hasChinese.test(metric.name) || !plainLabel(unit) || /[%％]/.test(unit) || metric.scope.dimensions.length !== 1 || metric.overlapping_dimensions?.includes(dim) || !sameIds(metric.periods, [periods.baseline, periods.current]) || result.row_count < 4 || result.row_count > 40) return null;
    const { baseline, current } = periods;
    const groups = /* @__PURE__ */ new Map();
    for (const fact of result.facts) {
      const dims = fact.dimensions || {}, label = dims[dim], period = dims.period;
      if (!plainLabel(label) || !label.trim() || !hasChinese.test(label) || /^(?:合计|总计|全部|未分类|未知|未注明)$/.test(label.trim()) || Object.keys(dims).length !== 2 || ![baseline, current].includes(period) || !countValue(fact.value)) return null;
      const key = label.normalize("NFKC").trim().toLowerCase(), group = groups.get(key) || { label };
      if (group.label !== label || group[period]) return null;
      group[period] = fact;
      groups.set(key, group);
    }
    const rows = [...groups.values()];
    if (rows.length < 2 || rows.some((group) => !group[baseline] || !group[current])) return null;
    const before = rows.reduce((sum, row) => sum + row[baseline].value, 0), after = rows.reduce((sum, row) => sum + row[current].value, 0);
    if (!countValue(before) || !countValue(after)) return null;
    const claims = result.claims || [];
    if (claims.some((claim) => !claim || claim.metric_id !== metric.id || !sameEvidence(claim.scope, metric.scope) || !["period_compare", "aggregate_compare"].includes(claim.operation) && !shareClaim(claim))) return null;
    const comparisons = claims.filter((claim) => claim.operation === "period_compare");
    const beforeIds = rows.map((row) => row[baseline].id), afterIds = rows.map((row) => row[current].id);
    if ((/* @__PURE__ */ new Set([...beforeIds, ...afterIds])).size !== result.row_count) return null;
    const totals = comparisons.filter((claim) => sameIds(claim.baseline?.derived_from, beforeIds) && sameIds(claim.current?.derived_from, afterIds));
    if (!totals.length || !totals.every((claim) => comparisonMatches(claim, before, after, unit) && claim.baseline.dimensions?.period === baseline && claim.current.dimensions?.period === current && sameIds(claim.fact_refs, [...beforeIds, ...afterIds]))) return null;
    const table2 = [];
    const usedClaims = new Set(totals);
    for (const row of rows) {
      const previous = row[baseline], latest = row[current], ids = [previous.id, latest.id];
      const compared = comparisons.filter((claim) => sameIds(claim.fact_refs, ids));
      if (!compared.length || !compared.every((claim) => comparisonMatches(claim, previous.value, latest.value, unit) && claim.baseline.id === previous.id && claim.current.id === latest.id && sameEvidence(claim.baseline.dimensions, previous.dimensions) && sameEvidence(claim.current.dimensions, latest.dimensions))) return null;
      const duplicates = claims.filter((claim) => claim.operation === "aggregate_compare" && sameIds(claim.fact_refs, ids));
      if (duplicates.some((claim) => !comparisonMatches(claim, previous.value, latest.value, unit) || claim.change_threshold !== void 0 || !sameIds(claim.baseline?.derived_from, [previous.id]) || !sameIds(claim.current?.derived_from, [latest.id]))) return null;
      const shares = claims.filter((claim) => claim.fact?.id === latest.id && shareClaim(claim));
      const percentage = after ? 100 * latest.value / after : null;
      if (!shares.length || !shares.every((claim) => sameIds(claim.fact_refs, [latest.id]) && claim.fact.value === latest.value && claim.fact.unit === unit && sameEvidence(claim.fact.dimensions, latest.dimensions) && claim.denominator === after && closePercent(claim.share_percent, percentage))) return null;
      for (const claim of [...compared, ...duplicates, ...shares]) usedClaims.add(claim);
      const delta2 = compared[0].difference;
      table2.push({ label: row.label, delta: delta2, values: [
        row.label,
        String(previous.value),
        String(latest.value),
        signedCount(delta2),
        percentage === null ? "\u65E0\u6CD5\u8BA1\u7B97\uFF08\u5206\u6BCD\u4E3A\u96F6\uFF09" : shares[0].share_percent.toFixed(2) + "%"
      ] });
    }
    if (claims.some((claim) => !usedClaims.has(claim) && !(shareClaim(claim) && beforeIds.includes(claim.fact?.id)))) return null;
    const { difference: delta, change_percent: change } = totals[0];
    const summary = change === null ? "\u57FA\u671F\u5408\u8BA1\u4E3A\u96F6\uFF0C\u65E0\u6CD5\u8BA1\u7B97\u6574\u4F53\u53D8\u5316\u7387\u3002" : change === 0 ? "\u6574\u4F53\u6301\u5E73\u3002" : "\u6574\u4F53" + (delta > 0 ? "\u589E\u5E45" : "\u964D\u5E45") + " " + Math.abs(change).toFixed(2) + "%\u3002";
    const notes = [summary];
    const mainContribution = /主要来自|主要贡献|变化来源/.test(question);
    if (delta !== 0 && mainContribution) {
      const direction = Math.sign(delta);
      const highest = Math.max(...table2.map((row) => row.delta * direction));
      const leaders = table2.filter((row) => row.delta * direction === highest).map((row) => row.label);
      notes.push("\u603B\u91CF" + (delta > 0 ? "\u589E\u52A0" : "\u51CF\u5C11") + "\u4E3B\u8981\u6765\u81EA" + leaders.join("\u3001") + (leaders.length > 1 ? "\uFF08\u5E76\u5217\uFF09" : "") + "\u3002");
    } else if (delta === 0 && mainContribution && table2.some((row) => row.delta !== 0)) {
      notes.push("\u5404\u7C7B\u589E\u51CF\u76F8\u4E92\u62B5\u6D88\uFF0C\u603B\u91CF\u6CA1\u6709\u51C0\u53D8\u5316\u3002");
    }
    const headers = ["\u5206\u7C7B", baseline + "\uFF08" + unit + "\uFF09", current + "\uFF08" + unit + "\uFF09", "\u589E\u51CF\uFF08" + unit + "\uFF09", current + "\u5360\u6BD4"];
    const values = [...table2.map((row) => row.values), ["\u5408\u8BA1", String(before), String(after), signedCount(delta), after ? "100.00%" : "\u65E0\u6CD5\u8BA1\u7B97\uFF08\u5206\u6BCD\u4E3A\u96F6\uFF09"]];
    return { result, table: { title: metric.name, text: formatTable(headers, values) }, claims: notes };
  }
  function formatReceiptDelivery(sections, missing) {
    const blocks = [];
    for (const { table: table2, claims } of sections) blocks.push("**" + table2.title + "**", table2.text, ...claims.slice(0, 10));
    if (missing) blocks.push(missing);
    return blocks.join("\n\n");
  }
  function withDefinitionNotice(text, section) {
    return text;
  }
  function verifiedGroupedCountShare(result) {
    const proof = result?.answer_contract?.grouped_count_share;
    const values = result?.values;
    if (result?.status !== "succeeded" || !result.result_ref || result.truncated || isExploratory(result) || result.receipt?.read_only !== true || result.validation?.state === "conflict" || result.answer_contract?.kind !== "sql_aggregate" || result.answer_contract?.complete !== true || result.answer_contract.result_ref !== result.result_ref || proof?.schema_version !== 1 || proof.result_ref !== result.result_ref || !Number.isSafeInteger(proof.denominator) || proof.denominator <= 0 || !plainLabel(proof.unit) || !Number.isInteger(proof.rounding_digits) || proof.rounding_digits < 0 || proof.rounding_digits > 6 || values?.complete !== true || !Array.isArray(values.columns) || values.columns.length !== 3 || !Array.isArray(values.rows) || values.rows.length !== values.row_count || values.rows.length !== result.row_count || values.rows.length < 2 || values.rows.length > 80 || values.rows.some((row) => !Array.isArray(row) || row.length !== 3)) return null;
    const { category_column: category, count_column: count, share_column: share, denominator, unit } = proof;
    const columns = [category, count, share];
    if (new Set(columns).size !== 3 || typeof share !== "string" || !share.trim() || [category, count].some((column) => !plainLabel(column) || !hasChinese.test(column))) return null;
    const [ci, ni, si] = columns.map((column) => values.columns.indexOf(column));
    const units = result.analysis_options?.column_units;
    if (Math.min(ci, ni, si) < 0 || units?.[count] !== unit || units?.[share] !== "%") return null;
    const rows = values.rows.map((row) => [row[ci], row[ni], row[si]]);
    const tolerance = 0.5 * 10 ** -proof.rounding_digits + 1e-7;
    if (rows.some(([label, n, p]) => !plainLabel(label) || !label.trim() || !Number.isSafeInteger(n) || n < 0 || !Number.isFinite(p) || p < 0 || p > 100 || Math.abs(p - n * 100 / denominator) > tolerance) || new Set(rows.map(([label]) => label.normalize("NFKC").trim())).size !== rows.length || rows.reduce((sum, row) => sum + row[1], 0) !== denominator) return null;
    return { category, count, share, denominator, unit, rows, digits: proof.rounding_digits };
  }
  function verifiedCountTable(result) {
    const { answer_contract: contract, values } = result;
    if (contract?.kind !== "sql_aggregate" || contract.result_ref !== result.result_ref || contract.complete !== true || result.receipt?.read_only !== true || result.truncated || result.validation?.state === "conflict" || values?.complete !== true || values.row_count !== result.row_count || !Array.isArray(values.columns) || values.columns.length !== 2 || !Array.isArray(values.rows) || values.rows.length !== result.row_count || values.rows.length < 2 || values.rows.length > 50) return null;
    const [category, count] = values.columns, unit = result.analysis_options?.column_units?.[count];
    if (![category, count].every((name) => plainLabel(name) && hasChinese.test(name)) || category === count || !/^(?:条|件|个|家|笔|人|项|张|份|宗|批|次|本|台|辆|户)$/.test(unit || "")) return null;
    const rows = values.rows;
    if (rows.some((row) => !Array.isArray(row) || row.length !== 2 || !plainLabel(row[0]) || !row[0].trim() || !countValue(row[1])) || new Set(rows.map(([label]) => label.normalize("NFKC").trim())).size !== rows.length) return null;
    return { category, count, unit, rows };
  }
  var groupedDeliveryExcluded = /同比|环比|预测|变化|以及另|另外|另查|再查|[前后][几一二三四五六七八九十\d]+(?:名|位)|最多|最少|最高|最低|排名|排行|阈值|建议|明细|导出|下载/;
  var groupShareRequested = /占比|比例|百分比|份额|(?:各|分别|各自)占/;
  function scopedCountReceipt(result, question, requestRef, sessionId) {
    const lineage = result.result_lineage, scope = result.validation?.request_scope?.request;
    return ["data.sql_query", "data.query"].includes(result.execution?.operation) && result.receipt?.query_hash && result.execution_scope && lineage?.schema_version === 1 && lineage.request_ref === requestRef && lineage.session_id === sessionId && lineage.purpose === "answer" && lineage.source_id && lineage.source_id === result.receipt.source_id && scope?.user_ref === requestRef && scope.quote === question;
  }
  function countPartitionReceipt(result, question, requestRef, sessionId) {
    const contract = result.answer_contract, proof = contract?.row_count_partition, values = result.values;
    if (!scopedCountReceipt(result, question, requestRef, sessionId) || !result.result_lineage.config_fingerprint || contract?.kind !== "sql_aggregate" || contract.complete !== true || contract.result_ref !== result.result_ref || contract.grouped_distinct || proof?.schema_version !== 1 || proof.result_ref !== result.result_ref || !proof.population_fingerprint || result.receipt.read_only !== true || result.truncated || result.validation?.state === "conflict" || values?.complete !== true) return null;
    if (proof.group_fingerprint) {
      const original = verifiedGroupedCountShare(result);
      const grouped = original || verifiedCountTable(result);
      if (!grouped || grouped.count !== proof.count_column || grouped.category !== proof.category_column) return null;
      const rows = JSON.stringify(grouped.rows.map(([label, value]) => [label.normalize("NFKC").trim(), value]).sort());
      return { result, proof, grouped, original, rows, unit: grouped.unit };
    }
    if (proof.category_column || values.row_count !== 1 || result.row_count !== 1 || values.columns?.length !== 1 || values.columns[0] !== proof.count_column || values.rows?.length !== 1 || values.rows[0]?.length !== 1 || !countValue(values.rows[0][0])) return null;
    return { result, proof, total: values.rows[0][0], unit: result.analysis_options?.column_units?.[proof.count_column] };
  }
  function sameCountPartition(entry, first, denominator) {
    const left = entry.result.result_lineage, right = first.result.result_lineage;
    if (left.source_id !== right.source_id || left.config_fingerprint !== right.config_fingerprint || entry.result.execution_scope !== first.result.execution_scope || entry.unit !== first.unit) return false;
    const samePopulation = entry.proof.population_fingerprint === first.proof.population_fingerprint;
    if (!entry.grouped) return samePopulation && entry.total === denominator;
    const sameNonNull = entry.proof.non_null_category_population && entry.proof.non_null_category_population === first.proof.non_null_category_population;
    return Boolean((samePopulation || sameNonNull) && entry.proof.group_fingerprint === first.proof.group_fingerprint && entry.rows === first.rows);
  }
  var partitionShares = (entry) => JSON.stringify(entry.original.rows.map(([label, , value]) => [label.normalize("NFKC").trim(), value]).sort());
  function uniqueCountShareDeliveries(results, question) {
    if (!groupShareRequested.test(question) || groupedDeliveryExcluded.test(question)) return results;
    const entries = [];
    return results.filter((result) => {
      const lineage = result.result_lineage;
      if (!lineage?.request_ref || !lineage.session_id) return true;
      const entry = countPartitionReceipt(result, question, lineage.request_ref, lineage.session_id);
      if (!entry?.original || result.claims?.length || result.verified_claims?.length) return true;
      const prior = entries.find((other) => other.result.result_lineage.request_ref === lineage.request_ref && other.result.result_lineage.session_id === lineage.session_id && sameCountPartition(entry, other, other.original.denominator) && entry.original.digits === other.original.digits && partitionShares(entry) === partitionShares(other));
      if (prior) return false;
      entries.push(entry);
      return true;
    });
  }

  // plugin/packages/natural-data/answer-cards.js
  var NO_CARD_TOOLS = /* @__PURE__ */ new Set(["data_inspect"]);
  function answerNumbers(text) {
    const dates = /(?<![\d.,])(?:19|20)\d{2}\s*(?:年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?)?|[-/]\s*(?:0?[1-9]|1[0-2])(?!\d)(?:[-/]\s*\d{1,2}(?!\d))?)|(?<!\d)\d{1,2}\s*月/g;
    return [...String(text || "").replace(dates, " ").matchAll(/-?\d[\d,]*(?:\.\d+)?/g)].map((m) => Number(m[0].replaceAll(",", ""))).filter(Number.isFinite);
  }
  var close = (n, v) => Math.abs(n - v) <= Math.max(0.011, Math.abs(v) * 5e-4) || Math.abs(n - v * 100) <= 0.06;
  var numeric = (v) => typeof v === "number" && Number.isFinite(v) || typeof v === "string" && /^-?\d+(?:\.\d+)?$/.test(v);
  function timeColumn(column, value) {
    return /^(?:年|年份|年度|月|年月|月份|日期|期间|统计期间|period|stat_year|year|month|date)$/i.test(column || "") || Array.isArray(value?.period_coverage) && value.period_coverage.some((p) => p?.column === column && timeKey(p.from) && timeKey(p.to));
  }
  function timeKey(value) {
    const match = /^([0-9]{4})(?:\s*(?:[-/]|年\s*)(0?[1-9]|1[0-2])\s*月?)?\s*年?$/.exec(String(value ?? "").trim());
    return match ? match[1] + (match[2] ? "-" + match[2].padStart(2, "0") : "") : null;
  }
  var sourceOf = (value) => value?.result_lineage?.source_id || value?.receipt?.source_id;
  var purposeOf = (value) => value?.result_lineage?.purpose || value?.purpose;
  var rawRanking = (value) => Array.isArray(value?.validation?.request_scope?.constraints) && value.validation.request_scope.constraints.some((item) => item?.kind === "raw_dimension_grouping");
  function tableIdentity(value) {
    const rows = value?.values?.rows;
    if (!Array.isArray(rows) || !rows.length) return null;
    const lineage = value?.result_lineage;
    const population = lineage?.population_fingerprint;
    if (population) return JSON.stringify([sourceOf(value) || null, population, value.values?.columns || [], rows]);
    if (!sourceOf(value) || !lineage?.request_ref || !lineage.session_id || !lineage.config_fingerprint || lineage.purpose !== "answer" || !value.query_identity || value.receipt?.read_only !== true || value.values.complete !== true || value.validation?.state === "conflict") return null;
    return JSON.stringify([
      sourceOf(value),
      lineage.request_ref,
      lineage.session_id,
      lineage.config_fingerprint,
      value.query_identity,
      value.values.columns,
      rows
    ]);
  }
  function availableFiscalTitle(value) {
    const proof = value.validation?.published_report_budget;
    const spending = value.validation?.published_report_spending;
    const { columns, rows } = value.values || {};
    if (!Array.isArray(columns) || !Array.isArray(rows) || rows.length !== 1 || !Array.isArray(rows[0])) return value;
    const present = (column) => columns.includes(column) && numeric(rows[0][columns.indexOf(column)]);
    if (spending?.period_kind === "current_project_spending" && present(spending.columns?.projects) && !present(spending.columns?.amount)) {
      return { ...value, title: spending.as_of_date?.slice(0, 4) + "\u5E74\u5EA6\u9879\u76EE\u6570\u91CF\u4E0E\u652F\u51FA\u62A5\u9001\u8986\u76D6" };
    }
    if (proof?.period === "current_year" && present(proof.count_column) && Object.keys(proof.measurements || {}).length > 0 && !Object.keys(proof.measurements).some(present)) {
      return { ...value, title: proof.as_of_date?.slice(0, 4) + "\u5E74\u5EA6\u9879\u76EE\u6570\u91CF\u4E0E\u9884\u7B97\u62A5\u9001\u8986\u76D6" };
    }
    return value;
  }
  function sameColumns(a, b) {
    return JSON.stringify(a?.values?.columns || []) === JSON.stringify(b?.values?.columns || []) && (sourceOf(a) || null) === (sourceOf(b) || null);
  }
  function prefixOf(short, long) {
    const rows = short?.values?.rows || [], full = long?.values?.rows || [];
    return rows.length > 0 && rows.length < full.length && rows.every((row, index) => JSON.stringify(row) === JSON.stringify(full[index]));
  }
  function measureNumbers(value) {
    return (value?.values?.rows || []).flatMap((row) => row.flatMap((v, i) => numeric(v) && !timeColumn(value.values.columns?.[i], value) ? [Number(v)] : [])).concat((value?.facts || []).filter((f) => typeof f.value === "number").map((f) => f.value));
  }
  function citedSmallScalars(cards, answer) {
    const matches = /* @__PURE__ */ new Map();
    const claims = new Set([...answer.replaceAll("**", "").matchAll(/(?<![\d.\-+])(?:0|[1-9]|1[0-2])\s*(?:条|件|家|本|张|笔|宗|单|台次|次|人|户|个(?!月))(?![\p{N}])/gu)].map((match) => match[0].replace(/\s/g, "")));
    if (!claims.size) return /* @__PURE__ */ new Set();
    for (const card of cards) {
      const { value } = card, { columns, rows } = value.values || {};
      if (value.receipt?.read_only !== true || purposeOf(value) !== "answer" || value.validation?.state === "conflict" || columns?.length !== 1 || rows?.length !== 1 || timeColumn(columns[0], value) || !numeric(rows[0][0])) continue;
      const unit = value.analysis_options?.column_units?.[columns[0]] || value.column_unit_evidence?.[columns[0]]?.unit;
      const key = String(Number(rows[0][0])) + unit;
      if (!claims.has(key)) continue;
      matches.set(key, [...matches.get(key) || [], card]);
    }
    return new Set([...matches.values()].filter((items) => items.length === 1).flat());
  }
  function cardRows(value) {
    const facts = value.facts || [];
    if (facts.length && facts.some((f) => Object.keys(f.dimensions || {}).length)) {
      return facts.map((f) => {
        const { period, ...dimensions2 } = f.dimensions || {};
        return { period: timeKey(period), labels: Object.values(dimensions2), measures: numeric(f.value) ? [Number(f.value)] : [] };
      });
    }
    const { rows = [], columns = [] } = value.values || {};
    const dates = columns.map((c, i) => timeColumn(c, value) && rows.some((row) => timeKey(row[i])) ? i : -1).filter((i) => i >= 0);
    const dimensions = new Set(rows.flatMap((row) => row.flatMap((v, i) => typeof v === "string" && !numeric(v) && !dates.includes(i) ? [i] : [])));
    return rows.map((row) => ({
      period: dates.length === 1 ? timeKey(row[dates[0]]) : null,
      labels: [...dimensions].map((i) => row[i]),
      measures: row.flatMap((v, i) => !dimensions.has(i) && !dates.includes(i) && numeric(v) ? [Number(v)] : [])
    }));
  }
  function answerClauses(answer) {
    return answer.replaceAll("**", "").split(/[\n。；;]/).filter(Boolean).map((text) => ({
      text,
      numbers: answerNumbers(text),
      periods: new Set([...text.matchAll(/([0-9]{4})\s*(?:[-/]\s*(0?[1-9]|1[0-2])(?!\d)|年(?:\s*(0?[1-9]|1[0-2])\s*月)?|(?=\s*\|))/g)].map((m) => timeKey(m[1] + (m[2] || m[3] ? "-" + (m[2] || m[3]) : ""))).filter(Boolean)),
      months: new Set([...text.matchAll(/(?:^|[^\d])(0?[1-9]|1[0-2])\s*月/g)].map((m) => m[1].padStart(2, "0")))
    }));
  }
  function periodCited(clause, period, singleYear) {
    if (clause.periods.has(period)) return true;
    if (period.length !== 7 || !singleYear || !clause.months.has(period.slice(5))) return false;
    return [...clause.periods].every((p) => p.length === 4 && p === period.slice(0, 4));
  }
  function matchingRows(rows, clauses, { singleYear = false, allowYearless = false } = {}) {
    return rows.map((row) => clauses.some((clause) => row.labels.every((label) => label !== null && label !== void 0 && String(label).trim() && clause.text.includes(String(label))) && (!row.period || periodCited(clause, row.period, singleYear) || allowYearless && row.period.length === 4 && !clause.periods.size && !clause.months.size) && row.measures.some((v) => clause.numbers.some((n) => close(n, v)))));
  }
  function groupedCitation(rows, clauses) {
    const dates = new Set(rows.map((row) => row.period).filter(Boolean));
    const categorized = rows.some((row) => row.labels.length);
    if (!categorized && !dates.size) return null;
    const years = new Set([...dates].map((p) => p.slice(0, 4)));
    const matching = matchingRows(rows, clauses, { singleYear: years.size === 1, allowYearless: dates.size === 1 }).some(Boolean);
    return matching || (!categorized ? null : false);
  }
  function cited(value, answer, numbers, rows, clauses) {
    const title = String(value?.title || "").trim();
    if (!numbers.length && title.length >= 4 && answer.includes(title)) return true;
    const grouped = groupedCitation(rows, clauses);
    if (grouped !== null) return grouped;
    const columns = value?.values?.columns || [], values = value?.values?.rows || [];
    const rate = (c) => /率|比例|占比|百分比|%|percent|ratio|rate/i.test(String(c));
    const time = (c) => timeColumn(c, value) || /年|月|日|期间|period|date|year|month/i.test(String(c));
    if (columns.some(rate)) {
      const counts = values.flatMap((row) => columns.flatMap((c, i) => !rate(c) && !time(c) && typeof row[i] === "number" ? [row[i]] : []));
      if (counts.length && !counts.some((v) => numbers.some((n) => Math.abs(n - v) <= Math.max(0.011, Math.abs(v) * 5e-4)))) return false;
    }
    return measureNumbers(value).some((v) => !(Number.isInteger(v) && v >= 0 && v <= 12) && numbers.some((n) => close(n, v)));
  }
  function support(value, numbers) {
    const hit = /* @__PURE__ */ new Set();
    let miss = 0;
    for (const v of measureNumbers(value)) {
      const found = numbers.map((n, i) => close(n, v) ? i : -1).filter((i) => i >= 0);
      if (found.length) found.forEach((i) => hit.add(i));
      else miss++;
    }
    return { hit, miss };
  }
  function namedMeasures(value, clauses) {
    const columns = value.values?.columns || [], row = value.values?.rows?.[0] || [];
    const hits = /* @__PURE__ */ new Set();
    for (let i = 0; i < columns.length; i++) {
      if (!numeric(row[i]) || timeColumn(columns[i], value)) continue;
      const label = String(columns[i]).replace(/[（(].*?[）)]$/, "").replace(/(?:数量|数)$/, "");
      if (label.length < 2) continue;
      if (clauses.some(({ text }) => {
        const at = text.indexOf(label);
        if (at < 0) return false;
        const phrase = text.slice(at + label.length).split(/[，、；;|]|,(?!\d{3}\b)/, 1)[0].slice(0, 26);
        const number = answerNumbers(phrase)[0];
        return number !== void 0 && close(number, Number(row[i]));
      })) hits.add(label);
    }
    return hits;
  }
  function scopeOf(value) {
    const c = value?.metric_contract || {}, periods = /* @__PURE__ */ new Set([...c.periods || [], ...c.scope?.periods || []]), dims = /* @__PURE__ */ new Set();
    for (const f of value?.facts || []) for (const [k, v] of Object.entries(f?.dimensions || {})) k === "period" ? periods.add(String(v)) : dims.add(k + "=" + v);
    for (const p of value?.period_coverage || []) periods.add(`${p.column}:${p.from}~${p.to}`);
    return JSON.stringify([c.id || null, Boolean(c.detail), [...periods].sort(), c.scope?.filters || [], c.default_filters || [], c.scope?.dimensions || [], [...dims].sort()]);
  }
  function dropCovered(main, numbers, clauses) {
    const profile = new Map(main.map((r) => [r, { ...support(r.value, numbers), named: namedMeasures(r.value, clauses) }]));
    const scalar = (r) => r.value.values?.rows?.length === 1 && r.value.values.rows[0].every(numeric) && !(r.value.values?.columns || []).some(timeColumn);
    const related = (a, b) => {
      const x = a.value, y = b.value;
      if (!scalar(a) || !scalar(b) || scopeOf(x) !== scopeOf(y)) return false;
      if (sourceOf(x) !== sourceOf(y) || (x.result_lineage?.config_fingerprint || null) !== (y.result_lineage?.config_fingerprint || null)) return false;
      if (x.result_lineage && y.result_lineage && x.result_lineage.population_fingerprint !== y.result_lineage.population_fingerprint) return false;
      if (x.query_identity && x.query_identity === y.query_identity) return true;
      return !sourceOf(x) && !sourceOf(y) && !x.result_lineage && !y.result_lineage && Boolean(x.task_id) && x.task_id === y.task_id && Boolean(x.title) && x.title === y.title;
    };
    return main.filter((a) => {
      const pa = profile.get(a);
      return !main.some((b) => {
        if (b.seq <= a.seq || !related(a, b)) return false;
        const pb = profile.get(b);
        if (pa.miss && pb.hit.size > pa.hit.size && [...pa.hit].every((i) => pb.hit.has(i))) return true;
        return pb.named.size > pa.named.size && [...pa.named].every((name) => pb.named.has(name));
      });
    });
  }
  var FORECAST = /预测|预估|预报|外推|forecast/i;
  var HISTORICAL_DELIVERY = /(?:同时|并且?|另外|还要|再)(?:请|帮我)?(?:展示|给出|列出|提供|画|绘制|统计|分析)[^，。；\n]{0,28}(?:历史|历年|往年|过去|实际|原始|逐月|趋势)|(?:历史|历年|往年|过去|实际)[^，。；\n]{0,20}(?:趋势图|图表|明细表)|(?:近|过去)[^，。；\n]{0,7}年[^，。；\n]{0,22}(?:趋势|走势)[^，。；\n]{0,20}(?:和|与|及|并)[^，。；\n]{0,14}预测/;
  var TWO_CHARTS = /(?:两|二|2)\s*(?:张|幅|个|份)\s*(?:[\p{Script=Han}]{0,10}?)图(?:表)?|双图/u;
  var CHART_COMPARISON = /对比|比较|对照|并排|分别(?:展示|给出|绘制|画)|分开(?:展示|画)|(?:都|同时)保留|保留.{0,10}(?:两|二|2)\s*(?:张|幅|个|份)/;
  var CHANGE_CHART_COUNT = /(?:不要|不用|无须|无需|不需要|不必|不做|不留)\s*(?:画|绘制|展示|做|保留|对照|对比|比较)?\s*(?:两|二|2)\s*(?:张|幅|个|份)|(?:改成|改为|只(?:要|留|看|画|给|用|展示)|仅(?:要|留|看|画|给|用|展示))\s*(?:一|1)\s*(?:张|幅|个|份)/;
  var COMPARE_WINDOW = /(?<!\d)((?:19|20)\d{2})(?:\s*年\s*(0?[1-9]|1[0-2])\s*月|[-/](0?[1-9]|1[0-2])(?!\d)|\s*年)?\s*(?:至|到|[-–—~])\s*((?:19|20)\d{2})(?:\s*年\s*(0?[1-9]|1[0-2])\s*月|[-/](0?[1-9]|1[0-2])(?!\d)|\s*年)?/g;
  function requestedChartPair(userText) {
    const question = String(userText || "").normalize("NFKC");
    const clause = question.split(/[。；;！？!?\n]/).find((part) => TWO_CHARTS.test(part) && CHART_COMPARISON.test(part));
    if (!clause || CHANGE_CHART_COUNT.test(question)) return null;
    const periods = [...clause.matchAll(COMPARE_WINDOW)].flatMap(([, year, month, iso, endYear, endMonth, endIso]) => {
      const from = timeKey(year + (month || iso ? "-" + (month || iso) : ""));
      const to = timeKey(endYear + (endMonth || endIso ? "-" + (endMonth || endIso) : ""));
      return from?.length === to?.length && from < to ? [{ from, to }] : [];
    });
    const counts = [...clause.replace(/(?:19|20)\d{2}\s*年\s*(?:\d{1,2}\s*月)?/g, " ").matchAll(/(?<![\d.])(\d+)\s*(个?月|年)(?![度份])/g)];
    if (periods.length === 2 && JSON.stringify(periods[0]) !== JSON.stringify(periods[1])) return { periods, kind: explicitChartType(clause) };
    if (periods.length || counts.length !== 2 || counts[0][2].endsWith("\u6708") !== counts[1][2].endsWith("\u6708") || Number(counts[0][1]) === Number(counts[1][1])) return null;
    const width = counts[0][2].endsWith("\u6708") ? 7 : 4;
    return { periods: counts.map((match) => ({ count: Number(match[1]), width })), kind: explicitChartType(clause) };
  }
  function matchedComparisonCards(cards, rows, userText, forecast) {
    const requested = requestedChartPair(userText);
    if (!requested) return [];
    const matches = requested.periods.map((period) => cards.filter((card) => {
      const value = card.value, actual = value.visualization?.actual;
      if (value.status !== "succeeded" || value.truncated || value.validation?.state === "conflict" || value.receipt?.read_only !== true || purposeOf(value) !== "answer" || forecast.includes(card) || !actual || actual === "table" || value.visualization.fallback || requested.kind && requested.kind !== actual) return false;
      const observed = rows.get(card);
      if (!observed.length || observed.some((row) => !row.period || row.labels.length || !row.measures.length) || new Set(observed.map((row) => row.period)).size !== observed.length) return false;
      const [cover, ...others] = value.period_coverage || [];
      const ordered = observed.map((row) => row.period).sort();
      const from = cover && !others.length && timeColumn(cover.column, value) ? timeKey(cover.from) : ordered[0];
      const to = cover && !others.length && timeColumn(cover.column, value) ? timeKey(cover.to) : ordered.at(-1);
      if (!from || !to || from.length !== to.length || from > ordered[0] || to < ordered.at(-1)) return false;
      if (period.count !== void 0) return Number.isSafeInteger(value.row_count) && value.row_count >= observed.length && (value.row_count === observed.length || Boolean(cover && !others.length && timeColumn(cover.column, value))) && value.row_count === period.count && from.length === period.width;
      return from === period.from && to === period.to;
    }));
    if (!matches.every((candidates) => candidates.length === 1)) return [];
    const [[a], [b]] = matches;
    const source = sourceOf(a.value);
    if (a === b || !source || source !== sourceOf(b.value) || a.reused !== b.reused || (a.value.business_project?.id || null) !== (b.value.business_project?.id || null) || (a.value.business_project?.version ?? null) !== (b.value.business_project?.version ?? null)) return [];
    if ((a.value.result_lineage || b.value.result_lineage) && !sharesRequestAndSource(a, b)) return [];
    return [a, b];
  }
  var YEARLY_RANK = /(?:本年|今年|全年|年度|年内|[0-9]{4}\s*年).{0,75}(?:哪个|哪类|排名|排行|最多|最少|最高|最低|第一|top)/i;
  var TIME_BREAKDOWN = /每(?:个)?月|逐月|按月|月度|各月|当月|分月|各季度|按季度|逐季|分季|同比|环比|对比|比较/;
  var PERIOD_REQUESTS = [
    /按(?:年份|年度|年)(?![底末龄级])|逐年|每(?:个)?年(?:份|度)?|各年|年报/,
    /按季度|逐季|每(?:个)?季度|各季度|季报/,
    /每(?:个)?月|逐月|按月|月度|各月|分月/,
    /按日|逐日|每天|每日/
  ];
  var TOTAL_REQUEST = /全年|年度|全期|本年|整体/;
  var TOTAL_MEASURE = /合计|总计|总数|总量|总额|累计|汇总/;
  var ADD_DELIVERY = /^(?:另|也|还|再|同时|请|给出|提供|列出|展示|统计|算出|计算|并|和|与|及)/;
  var COUNT_COLUMN = /数量|计数|件数|条数|笔数|记录数|案卷数|办理量|[（(]\s*(?:件|条|笔|份|个|家|人)\s*[）)]$|(?:^|_)count(?:$|_)/i;
  function separatePeriodDelivery(text) {
    if (PERIOD_REQUESTS.filter((pattern) => pattern.test(text)).length > 1) return true;
    return text.split(/[，、。；;\n]|(?=(?:以及|同时|并|另外|和|与|及)(?:还|再|给|列|算|统计|全|本|年度|总|合|汇))/).some((clause) => !PERIOD_REQUESTS.some((pattern) => pattern.test(clause)) && (TOTAL_REQUEST.test(clause) && TOTAL_MEASURE.test(clause) || ADD_DELIVERY.test(clause.trim()) && TOTAL_MEASURE.test(clause) || /(?:对比|比较).{0,14}(?:全期|全年|上年|其他期间)/.test(clause)));
  }
  var periodProof = (card) => card?.value.validation?.period_grain;
  function outsideRequest(card) {
    const value = card.value, proof = value.validation?.result_period_scope, ref = value.result_lineage?.request_ref;
    return !card.reused && proof?.state === "outside_request" && proof.result_ref === value.result_ref && Boolean(ref) && proof.request_ref === ref;
  }
  function monthlyPrimary(rows, card) {
    if (card.value.status !== "succeeded" || card.value.truncated) return false;
    const months = new Set(rows.filter((row) => row.period?.length === 7 && row.measures.length).map((row) => row.period));
    const coverage = card.value.answer_contract?.monthly_sql_coverage;
    return months.size >= 2 || Boolean(months.size && coverage?.month_column && coverage.requested_months?.length >= 2);
  }
  function sharesRequestAndSource(a, b) {
    const left = a.value.result_lineage, right = b.value.result_lineage;
    return Boolean(left?.request_ref && left?.source_id && left?.session_id && left?.config_fingerprint && left.source_id === right?.source_id && left.session_id === right.session_id && left.config_fingerprint === right.config_fingerprint && JSON.stringify(left.request_ref) === JSON.stringify(right.request_ref));
  }
  function boundPeriodPrimary(card, byRef, candidates) {
    const auxiliary = periodProof(card);
    if (auxiliary?.state !== "auxiliary" || card.reused || !auxiliary.source_result_ref) return null;
    const primary = byRef.get(auxiliary.source_result_ref), proof = periodProof(primary);
    if (!primary || primary === card || primary.reused || !candidates.has(primary) || !sharesRequestAndSource(card, primary) || primary.value.status !== "succeeded" || primary.value.truncated || proof?.state !== "checked" || proof.actual !== auxiliary.requested) return null;
    return primary;
  }
  function monthlyCheckCard(card, primary, rows) {
    const value = card.value, columns = value.values?.columns || [];
    const shape = rows.get(card);
    return !value.metric_contract?.detail && !value.report && columns.length > 0 && columns.every((column) => COUNT_COLUMN.test(column)) && shape.length === 1 && !shape[0].period && !shape[0].labels.length && shape[0].measures.length === columns.length && primary.some((main) => sharesRequestAndSource(card, main) && (main.value.values?.columns || []).some((column) => COUNT_COLUMN.test(column)));
  }
  function forecastCard(card) {
    if (card.value.status !== "succeeded" || purposeOf(card.value) === "explore") return false;
    return card.currentClaims.some((claim) => {
      const stats = claim.statistics;
      return claim.operation === "matrix_method" && (stats?.kind === "forecast" || claim.method_id === "matrix.42") && Boolean(timeKey(stats?.origin) || stats?.history?.periods?.length || stats?.forecast_periods?.length) && (!stats?.source_result_ref || stats.source_result_ref === card.value.result_ref) && Array.isArray(stats?.predicted) && stats.predicted.length > 0 && stats.predicted.every(Number.isFinite) && Array.isArray(stats.interval_95) && stats.interval_95.length === stats.predicted.length && stats.interval_95.every((interval) => Array.isArray(interval) && interval.length === 2 && interval.every(Number.isFinite));
    });
  }
  function latestOfTask(cards) {
    const latest = /* @__PURE__ */ new Map();
    for (const card of cards) {
      const task = card.value.task_id;
      if (!task) continue;
      const previous = latest.get(task);
      if (!previous || card.querySeq >= previous.querySeq) latest.set(task, card);
    }
    return (card) => !card.value.task_id || latest.get(card.value.task_id) === card;
  }
  function pickAnswerCards(results, answer = "", failed = false, { userText = "", comparisonPreviewRef: comparisonPreviewRef2 } = {}) {
    const shown = results.filter((r) => !NO_CARD_TOOLS.has(r.name));
    const byRef = /* @__PURE__ */ new Map();
    for (const card of shown) {
      const ref = card.value.result_ref;
      const prior = byRef.get(ref);
      const currentClaims = card.name === "analysis_method" && !card.reused && card.value.status === "succeeded" ? [...card.value.claims || [], ...card.value.verified_claims || []] : [];
      byRef.set(ref, {
        ...card,
        querySeq: prior?.querySeq ?? card.seq,
        currentClaims: [...prior?.currentClaims || [], ...currentClaims],
        value: mergeResultReceipt(prior?.value, card.value)
      });
    }
    const all = [...byRef.values()];
    const replaced = replacedResultRefs(all.map((r) => r.value));
    const candidates = all.filter((r) => !isExploratory(r.value) && !outsideRequest(r) && !replaced.has(r.value.result_ref));
    const current = latestOfTask(candidates);
    const numbers = answerNumbers(answer);
    const clauses = answerClauses(answer);
    const rows = new Map(candidates.map((card) => [card, cardRows(card.value)]));
    const forecast = candidates.filter(forecastCard);
    const separate = separatePeriodDelivery(userText);
    const annualRank = YEARLY_RANK.test(userText) && !TIME_BREAKDOWN.test(userText) && !separate;
    const rankingProven = candidates.some((card) => rawRanking(card.value));
    const monthlyBreakdown = (card) => (annualRank || rankingProven) && rows.get(card).some((row) => row.labels.length && row.period?.length === 7);
    const monthly = !separate && PERIOD_REQUESTS[2].test(userText) ? candidates.filter((card) => monthlyPrimary(rows.get(card), card)) : [];
    const scopedCards = new Set(candidates);
    const periodPrimaries = /* @__PURE__ */ new Set(), periodAuxiliaries = /* @__PURE__ */ new Set();
    if (!separate) for (const card of candidates) {
      const primary2 = boundPeriodPrimary(card, byRef, scopedCards);
      if (primary2) {
        periodAuxiliaries.add(card);
        periodPrimaries.add(primary2);
      }
    }
    const relevant = candidates.filter((card) => !monthlyBreakdown(card) && !periodAuxiliaries.has(card) && (periodProof(card) || !monthlyCheckCard(card, monthly, rows)));
    const small = citedSmallScalars(relevant, answer);
    let main = failed ? relevant.filter(current) : dropCovered(relevant.filter((card) => small.has(card) || cited(card.value, answer, numbers, rows.get(card), clauses)), numbers, clauses);
    if (forecast.length) {
      if (FORECAST.test(userText || answer) && !HISTORICAL_DELIVERY.test(userText)) {
        main = main.filter((card) => {
          if (card.value.metric_contract?.detail || !rows.get(card).some((row) => row.period)) return true;
          return !forecast.some((training) => !sourceOf(card.value) || !sourceOf(training.value) || sourceOf(card.value) === sourceOf(training.value));
        });
      }
      for (const card of forecast) if (!main.includes(card)) main.push(card);
    }
    if (!failed) {
      for (const card of candidates) if (card.value.status === "succeeded" && card.value.metric_contract?.detail && !main.includes(card)) main.push(card);
      for (const card of matchedComparisonCards(relevant, rows, userText, forecast)) if (!main.includes(card)) main.push(card);
      const compared = relevant.find((card) => card.value.result_ref === comparisonPreviewRef2 && !card.reused && current(card));
      if (compared && !main.includes(compared)) main.push(compared);
      const lastAnswer = relevant.filter((card) => periodPrimaries.has(card) || monthly.includes(card)).at(-1) ?? relevant.filter(current).at(-1);
      if (lastAnswer && !main.length) main.push(lastAnswer);
    }
    const primary = new Set(uniqueCountShareDeliveries(uniqueSqlProfileDeliveries(main.map((card) => card.value)), userText));
    main = main.filter((card) => primary.has(card.value));
    const fullest = /* @__PURE__ */ new Map();
    for (const card of main) {
      const key = tableIdentity(card.value);
      if (!key) continue;
      const prior = fullest.get(key);
      if (!prior || prior.value.truncated && !card.value.truncated) fullest.set(key, card);
    }
    main = main.filter((card) => {
      const key = tableIdentity(card.value);
      if (key && fullest.get(key) !== card) return false;
      return !(card.value.truncated && main.some((other) => other !== card && !other.value.truncated && sameColumns(card.value, other.value) && prefixOf(card.value, other.value)));
    });
    const process = all.filter((r) => !main.includes(r));
    return { main: main.sort((a, b) => a.seq - b.seq).map((r) => availableFiscalTitle(r.value)), process: process.map((r) => availableFiscalTitle(r.value)) };
  }

  // plugin/packages/natural-data/request-context.js
  var RETRY = /^(?:请)?继续(?:完成)?(?:上一题|上个问题|原问题|未完成的回答|(?:(?:前面|之前|刚才)(?:的)?)?(?:全省统计|统计|回答|查询))?[。！!]?$/;
  var NATIVE = /^继续完成第(\d+)轮未完成的回答。先复用本会话已成功的查询结果和工序回执；除非结果确实缺失或条件改变，不要重放查询。无法确认的解释请明确说明。$/;
  var messageText = (e) => (e?.data?.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
  function continuation(text) {
    const value = String(text || "").trim(), match = NATIVE.exec(value);
    return match ? { turn: Number(match[1]) } : RETRY.test(value) ? { scopeChanged: value.includes("\u5168\u7701") } : null;
  }

  // plugin/packages/natural-data/result-events.js
  var RESULT_TOOLS = /* @__PURE__ */ new Set([
    "data_query",
    "data_report",
    "data_sql_query",
    "data_metric_query",
    "data_derive_aggregation",
    "data_visualize",
    "data_analyze",
    "data_inspect",
    "analysis_compute",
    "analysis_run",
    "analysis_method",
    "data_make_report",
    "data_edit_report"
  ]);
  var DATA_TURN_TOOLS = /* @__PURE__ */ new Set([...RESULT_TOOLS, "data_catalog"]);
  var resultRequestEvents = {
    kind: "natural-data-request",
    match: (event) => event.type === "user/message" && event.data?.source?.kind === "user" ? { id: event.data.id || String(event.seq), role: "start" } : null,
    start: (_context, { event }) => ({ text: messageText(event), id: event.data.id }),
    update: (_context, { event }) => ({ text: messageText(event), id: event.data.id })
  };
  function comparisonPreviewRef(results, text, userText, requestId) {
    const matched = /* @__PURE__ */ new Set();
    for (const query of results) {
      const result = query.value, lineage = result.result_lineage;
      if (query.name !== "data_metric_query" || query.reused || !result.execution_scope || lineage && (lineage.purpose !== "answer" || lineage.request_ref !== requestId || lineage.source_id !== result.receipt?.source_id)) continue;
      const analysis = results.filter((item) => item.seq > query.seq && item.name === "analysis_compute" && !item.reused && item.value.result_ref === result.result_ref && item.value.execution_scope === result.execution_scope && item.value.execution?.operation === "analysis.compute" && Array.isArray(item.value.claims));
      if (!analysis.length) continue;
      const bound = analysis.reduce((value, item) => mergeResultReceipt(value, { claims: item.value.claims }), result);
      const section = groupedComparisonSection(bound, userText || "");
      if (section && text === withDefinitionNotice(formatReceiptDelivery([section]), section)) matched.add(result.result_ref);
    }
    return matched.size === 1 ? matched.values().next().value : null;
  }
  function forecastClaimsByResult(items) {
    const byRef = /* @__PURE__ */ new Map();
    for (const item of items) {
      const value = item.value;
      if (item.name !== "analysis_method" || value?.status !== "succeeded" || !value.result_ref || isExploratory(value)) continue;
      for (const claim of value.claims || []) {
        const stats = claim?.statistics;
        if (claim?.operation !== "matrix_method" || !/^claim:[0-9a-f]{20}$/.test(claim.claim_id) || claim.method_id !== "matrix.42" && !(typeof claim.method_id === "string" && claim.method_id.startsWith("custom.") && stats?.kind === "forecast") || stats?.source_result_ref && stats.source_result_ref !== value.result_ref) continue;
        const ids = byRef.get(value.result_ref) || /* @__PURE__ */ new Set();
        ids.add(claim.claim_id);
        byRef.set(value.result_ref, ids);
      }
    }
    return byRef;
  }
  var MUTATION_TOOLS = /* @__PURE__ */ new Set(["data_visualize", "data_analyze", "data_make_report", "data_edit_report", "analysis_compute", "analysis_method"]);
  function resultEvents(publicUrls2, hooks) {
    const reuse = (state, text) => {
      const retry = continuation(text);
      const prior = retry && !retry.scopeChanged ? retry.turn ? state.prior.find((p) => p.turn === retry.turn) : state.prior.at(-1) : null;
      return {
        ...state,
        userText: prior?.userText || text,
        reused: prior ? (prior.results || []).filter((r) => !isExploratory(r.value)).map((r) => ({ ...r, failure: false, reused: true })) : []
      };
    };
    const definition = {
      kind: "natural-data-results",
      match(event) {
        if (event.type === "assistant/message" || event.type === "turn/end") return { id: String(event.data.turn), role: "start" };
        if (event.type === "tool/call" || event.type === "tool/result" && event.surfaceOp === "append") return { id: String(event.data.turn), role: "update" };
        return null;
      },
      start(context, match, reader) {
        const prev = reader?.previous("natural-data-results")?.state;
        const prior = prev ? [...prev.prior || [], { turn: prev.turn, userText: prev.userText, results: [...prev.reused || [], ...prev.results] }].slice(-8) : [];
        const state = { turn: match.event.data.turn, calls: /* @__PURE__ */ new Map(), results: [], reused: [], prior };
        const user = reader?.previous("natural-data-request")?.state;
        return definition.update({ state: user ? { ...reuse(state, user.text), requestId: user.id } : state }, match);
      },
      update(context, { event }) {
        const state = context.state;
        if (event.type === "turn/end") {
          const failed = ["error", "max-tokens"].includes(event.data.reason?.kind);
          return { ...state, failed, ended: true, results: state.results.map((item) => ({ ...item, failure: failed })) };
        }
        if (event.type === "tool/call") {
          const calls = new Map(state.calls);
          calls.set(String(event.data.callId), event.data.name);
          const preview = state.pendingComparisonPreview;
          if (preview && event.data.name === "ask_user_question" && event.data.callId === preview.callId) {
            return { ...state, calls, pendingComparisonPreview: null, comparisonPreview: { ref: preview.ref, seq: event.seq } };
          }
          return { ...state, calls, pendingComparisonPreview: null };
        }
        if (event.type === "assistant/message") {
          const blocks = event.data.message?.content || [];
          const text = blocks.filter((b) => b.type === "text").map((b) => b.text).join("");
          if (!text.trim()) return state;
          const ask = blocks.find((block) => block.type === "tool-call" && block.name === "ask_user_question");
          if (ask) {
            const ref = ask.id && comparisonPreviewRef(state.results, text, state.userText, state.requestId);
            return {
              ...state,
              answer: text,
              preClarificationAnswer: text,
              pendingComparisonPreview: ref ? { ref, callId: ask.id } : null
            };
          }
          const choiceOnly = /尚未选定，待确认口径后可继续核算。$/.test(text) && !text.includes("\n");
          return {
            ...state,
            answer: choiceOnly && state.preClarificationAnswer ? state.preClarificationAnswer + "\n\n" + text : text,
            pendingComparisonPreview: null
          };
        }
        if (event.type !== "tool/result" || event.data.message.isError) return state;
        const message = event.data.message, name = state.calls.get(String(message.source?.callId));
        if (!RESULT_TOOLS.has(name)) return state;
        let value;
        try {
          value = JSON.parse(message.content.find((x) => x.type === "text")?.text || "{}");
        } catch {
          return state;
        }
        if (!value.result_ref || !value.view_url || value.status !== "succeeded" || !publicUrls2.result(value.view_url)) return state;
        if (MUTATION_TOOLS.has(name)) hooks?.onMutation?.(value, name, event.seq);
        return { ...state, results: [...state.results, { seq: event.seq, name, value }] };
      },
      buildLocationData(context, scope, previous) {
        if (scope !== "turn" || !context.state) return null;
        const state = context.state;
        const attempted = [...state.calls.values()].some((name) => DATA_TURN_TOOLS.has(name));
        if (previous?.ownResults === state.results && previous?.reused === state.reused && previous?.value?.answer === (state.answer || "") && previous?.value?.comparisonPreview === state.comparisonPreview && previous?.value?.userText === (state.userText || "") && previous?.value?.failed === state.failed && previous?.value?.attempted === attempted && previous?.value?.ended === state.ended) return previous;
        return {
          kind: "turn",
          turn: state.turn,
          key: "natural-data-results",
          ownResults: state.results,
          reused: state.reused,
          value: {
            results: [...state.reused, ...state.results],
            answer: state.answer || "",
            comparisonPreview: state.comparisonPreview,
            failed: state.failed,
            userText: state.userText || "",
            attempted,
            ended: state.ended
          }
        };
      }
    };
    return definition;
  }

  // plugin/packages/natural-data/result-view.js
  function mergeViewPatch(current, patch) {
    if (!current || !patch || patch.result_ref !== current.result_ref || current.report) return null;
    const base = current.view_revision ?? 0;
    if (!Number.isSafeInteger(base) || patch.base_view_revision !== base || patch.view_revision !== base + 1 || typeof patch.view_ref !== "string") return null;
    const { chart, requested_chart, view, visualization, view_revision, view_ref } = patch;
    if (!chart || typeof chart.type !== "string" || chart.type !== view || visualization?.actual !== view) return null;
    return { ...current, chart, requested_chart, view, visualization, view_revision, view_ref };
  }
  function resultFrameMessage(previous, next, baseRevision) {
    const { result, tab } = next;
    if (!result) return null;
    if (!previous) return { type: "natural-data:state", result, tab };
    if (previous.result !== result) {
      if (result.result_ref === previous.result.result_ref && baseRevision === (previous.result.view_revision ?? 0)) {
        const { result_ref, chart, requested_chart, view, visualization, view_revision, view_ref } = result;
        const patch = {
          result_ref,
          base_view_revision: baseRevision,
          chart,
          requested_chart,
          view,
          visualization,
          view_revision,
          view_ref
        };
        return { type: "natural-data:view", patch, tab };
      }
      return { type: "natural-data:state", result, tab };
    }
    return previous.tab === tab ? null : { type: "natural-data:tab", result_ref: result.result_ref, tab };
  }
  var MAX_RESULT_FRAME_HEIGHT = 760;
  function capResultFrameHeight(height) {
    return Math.min(Math.round(height), MAX_RESULT_FRAME_HEIGHT);
  }
  function resultFrameHeight(message) {
    if (message?.type !== "natural-data:height" || !Number.isFinite(message.height) || message.height <= 0) return null;
    return capResultFrameHeight(Math.max(72, Math.ceil(message.height)));
  }
  var FOREIGN_TITLE = /[\u0370-\u03FF\u0400-\u052F\u0600-\u06FF\u0E00-\u0E7F\u3040-\u30FF\uAC00-\uD7AF]{2,}/;
  function resultDisplayTitle(title, provenance) {
    if (typeof title !== "string") return title;
    const cleaned = provenance?.synthetic === true ? title.replace(/（合成测试）$/, "") : title;
    return FOREIGN_TITLE.test(cleaned) && !/[\u4e00-\u9fff]/.test(cleaned) ? "\u6570\u636E\u67E5\u8BE2\u7ED3\u679C" : cleaned;
  }

  // plugin/packages/natural-data/calculation-presentation.js
  var REGISTRATION = new RegExp(
    "(?:(?:\u5F53\u524D|\u672C\u6B21|\u672C\u8F6E|\u672C|\u8BE5|\u6B64|\u6240\u7528|\u91C7\u7528\u7684)?(?:\u8BA1\u7B97|\u7EDF\u8BA1|\u6307\u6807)?(?:\u53E3\u5F84|\u5B9A\u4E49|\u8BA1\u7B97\u89C4\u5219))(?:\u76EE\u524D|\u5F53\u524D|\u6682|\u4ECD|\u5C1A|\u4EC5|\u4E3A|\u5C5E\u4E8E|\u5C5E|\u662F|\u5904\u4E8E|\u7CFB|\u672C\u8EAB|\u7531|\u6309|\u7CFB\u7EDF|\u81EA\u52A8|\u4E34\u65F6|\\s){0,16}(?:\u63A8\u5B9A(?:\u53E3\u5F84|\u5B9A\u4E49)?|\u6682\u5B9A(?:\u53E3\u5F84|\u5B9A\u4E49)?|(?:\u5C1A\u672A|\u672A\u7ECF|\u5C1A\u5F85|\u5F85|\u4ECD\u5F85|\u6709\u5F85)(?:\u7ECF)?(?:\u4E1A\u52A1(?:\u65B9)?|\u76F8\u5173(?:\u4E1A\u52A1)?\u4EBA\u5458|\u4EBA\u5DE5)?(?:\u8FDB\u4E00\u6B65)?(?:\u6B63\u5F0F)?(?:\u786E\u8BA4|\u6838\u5BF9|\u6838\u5B9E|\u5BA1\u5B9A))|(?:\u5C5E(?:\u4E8E)?|\u4E3A|\u7CFB|\u6309|\u91C7\u7528|\u4F7F\u7528|\u57FA\u4E8E)(?:\u7CFB\u7EDF|\u81EA\u52A8|\u4E34\u65F6|\u5F53\u524D)?(?:\u63A8\u5B9A|\u6682\u5B9A)(?:\u7684)?(?:\u8BA1\u7B97|\u7EDF\u8BA1)?(?:\u53E3\u5F84|\u5B9A\u4E49|\u8BA1\u7B97\u89C4\u5219)|(?:(?:\u5F53\u524D|\u672C\u6B21|\u672C\u8F6E|\u672C|\u8BE5|\u6B64)?(?:SQL|\u67E5\u8BE2|\u6C47\u603B|\u7EDF\u8BA1|\u8BA1\u7B97|\u7ED3\u679C|\u6570\u503C)[^\u3002\uFF1B;\uFF0C,\\n]{0,16})?(?:\u5C1A\u672A|\u4ECD\u672A|\u6682\u672A|\u6CA1\u6709|\u672A)(?:\u5173\u8054|\u7ED1\u5B9A|\u5BF9\u5E94|\u5339\u914D)(?:\u5230)?(?:\u4EFB\u4F55)?(?:\u5DF2\u786E\u8BA4\u7684|\u6B63\u5F0F|\u4E1A\u52A1|\u5DF2\u53D1\u5E03|\u5DF2\u767B\u8BB0|\u914D\u7F6E)?(?:\u4E1A\u52A1)?(?:\u6307\u6807|\u53E3\u5F84|\u5B9A\u4E49)|(?:\u5C1A\u672A|\u4ECD\u672A|\u6682\u672A|\u6CA1\u6709|\u672A)(?:\u914D\u7F6E|\u767B\u8BB0|\u6CE8\u518C|\u7EB3\u5165|\u5907\u6848)(?:\u4E3A|\u6210|\u5165)?(?:\u6B63\u5F0F|\u5DF2\u53D1\u5E03|\u53D1\u5E03|\u4E1A\u52A1){0,2}(?:\u6307\u6807|\u53E3\u5F84|\u6307\u6807\u4F53\u7CFB|\u6307\u6807\u5E93)|(?:\u6CA1\u6709|\u5C1A\u65E0|\u672A\u627E\u5230)(?:\u5339\u914D\u7684|\u5BF9\u5E94\u7684|\u76F8\u5173\u7684)?(?:\u5DF2\u53D1\u5E03\u7684?|\u5DF2\u767B\u8BB0\u7684?|\u9884\u5148\u914D\u7F6E\u7684?|\u6B63\u5F0F\u7684)(?:\u4E1A\u52A1)?(?:\u6307\u6807|\u53E3\u5F84|\u6307\u6807\u5B9A\u4E49)|(?:\u5C1A\u672A|\u672A\u7ECF|\u5C1A\u5F85|\u5F85|\u4ECD\u5F85|\u6709\u5F85)(?:\u7ECF)?(?:\u4E1A\u52A1(?:\u65B9)?|\u76F8\u5173(?:\u4E1A\u52A1)?\u4EBA\u5458|\u4EBA\u5DE5)(?:\u8FDB\u4E00\u6B65)?(?:\u6B63\u5F0F)?\u786E\u8BA4|(?:\u8BF7(?:[^\u3002\uFF1B;\uFF0C,\\n]{0,16}?)|\u9700|\u987B|\u5C1A\u5F85|\u4ECD\u5F85|\u6709\u5F85|\u5F85)(?:\u4E1A\u52A1(?:\u65B9)?|\u76F8\u5173(?:\u4E1A\u52A1)?\u4EBA\u5458|\u4EBA\u5DE5)?(?:\u8FDB\u4E00\u6B65)?(?:\u6838\u5BF9|\u6838\u5B9E|\u786E\u8BA4|\u5BA1\u5B9A)(?:\u65E0\u8BEF)?(?:\u540E|\u4E4B\u540E)?(?:\u518D)?(?:\u6B63\u5F0F)?(?:\u91C7\u7528|\u62A5\u9001|\u4F5C\u4E3A\u6B63\u5F0F(?:\u62A5\u9001|\u7EDF\u8BA1)?(?:\u503C|\u7ED3\u679C|\u53E3\u5F84)?(?:\u91C7\u7528|\u4F7F\u7528|\u62A5\u9001)?)|(?:(?:\u8BE5|\u6B64|\u672C\u6B21|\u5F53\u524D|\u4E0A\u8FF0)(?:\u6570\u503C|\u7ED3\u679C|\u6570\u636E)|(?:\u6570\u503C|\u7ED3\u679C))(?:\u4ECD|\u5C1A|\u6682)?(?:\u5C1A\u672A|\u672A\u7ECF|\u5C1A\u5F85|\u5F85)(?:\u4E1A\u52A1(?:\u65B9)?|\u4EBA\u5DE5)?(?:\u6B63\u5F0F)?\u786E\u8BA4(?:(?:\u540E|\u4E4B\u540E)?(?:\u518D)?(?:\u6B63\u5F0F)?(?:\u91C7\u7528|\u62A5\u9001|\u4F5C\u4E3A\u6B63\u5F0F(?:\u62A5\u9001|\u7EDF\u8BA1)?(?:\u503C|\u7ED3\u679C|\u53E3\u5F84)?(?:\u91C7\u7528|\u4F7F\u7528|\u62A5\u9001)?))?"
  );
  var DEFINITION_QUALIFIERS = "(?:(?:\u53EF\u5339\u914D|\u53EF\u5BF9\u5E94|\u5339\u914D|\u5BF9\u5E94|\u76F8\u5173|\u7ED3\u6784\u5316|\u9884\u7F6E|\u4E1A\u52A1)(?:\u7684)?){0,3}";
  var PUBLISHED_DEFINITION = "(?:\u5DF2(?:\u7ECF)?(?:\u53D1\u5E03|\u767B\u8BB0)(?:\u7684)?)";
  var DEFINITION_ITEM = DEFINITION_QUALIFIERS + PUBLISHED_DEFINITION + DEFINITION_QUALIFIERS + "(?:\u6307\u6807|\u53E3\u5F84)(?:\u5B9A\u4E49)?";
  var DATA_INVENTORY = "(?:\u4E1A\u52A1\u8868|\u6570\u636E\u8868|\u6388\u6743\u8868|\u7EDF\u8BA1\u8868|\u660E\u7EC6\u8868|\u8868\u5B57\u6BB5|\u8868\u540D(?:\u79F0)?|\u5B57\u6BB5(?:\u540D(?:\u79F0)?)?|\u8BB0\u5F55|\u65E5\u5FD7)";
  var DATA_QUALIFIERS = "(?:(?:\u5F53\u524D|\u5168\u90E8|\u6240\u6709|\u73B0\u6709|\u76F8\u5173|\u5BF9\u5E94|\u5FC5\u8981|\u6240\u9700|\u53EF\u7528|\u53EF\u67E5|\u6388\u6743|\u7EDF\u8BA1|\u5B9E\u9645|\u6709\u6548|\u5386\u53F2|\u539F\u59CB|\u4E1A\u52A1|\u6570\u636E)(?:\u7684)?){0,3}";
  var DATA_ITEM = DATA_QUALIFIERS + DATA_INVENTORY;
  var CONFIG_ITEM = "(?:" + DEFINITION_ITEM + "|" + DEFINITION_QUALIFIERS + "(?:(?:\u6307\u6807|\u53E3\u5F84)(?:\u5B9A\u4E49|\u914D\u7F6E|\u53D1\u5E03\u72B6\u6001|\u767B\u8BB0\u72B6\u6001)|(?:\u5DF2)?(?:\u53D1\u5E03|\u767B\u8BB0|\u9884\u7F6E)(?:\u7684)?(?:\u6307\u6807|\u53E3\u5F84|\u5B9A\u4E49)|(?:\u67E5\u8BE2|\u914D\u7F6E|SQL[ \\t]*)?(?:\u793A\u4F8B|\u6837\u4F8B|\u6A21\u677F))|\u6307\u6807|(?:\u4E1A\u52A1|\u7EDF\u8BA1)?\u53E3\u5F84)(?:\u540D(?:\u79F0)?)?";
  var LIST_ITEM = "(?:" + DATA_ITEM + "|" + CONFIG_ITEM + ")";
  var LIST_MEMBER = "(?:\\*\\*" + LIST_ITEM + "\\*\\*|" + LIST_ITEM + ")";
  var LIST_SEPARATOR = "[ \\t]*(?:\u4EE5\u53CA|\u6216\u8005|\u548C|\u4E0E|\u53CA|\u6216|\u3001|[\uFF0C,][ \\t]*(?:\u4EE5\u53CA|\u6216\u8005|\u548C|\u4E0E|\u53CA|\u6216)?)[ \\t]*";
  var DEFINITION_ATTRIBUTE = "(?:\u540D\u79F0|\u53E3\u5F84|\u5B9A\u4E49)";
  var DEFINITION_MATCH = DEFINITION_ATTRIBUTE + "(?:(?:\u6216|\u4E0E|\u548C|\u53CA)" + DEFINITION_ATTRIBUTE + ")?[ \\t]*(?:\u4E0E|\u548C|\u540C|\u8DDF)[^\uFF0C,\u3002\uFF1B;\uFF01!\uFF1F?\\n]{1,64}?\u76F8?(?:\u5339\u914D|\u5BF9\u5E94|\u4E00\u81F4)\u7684[ \\t]*";
  var DEFINITION_MISSING = "(?:(?:\u4E5F|\u53E6\u5916|\u540C\u65F6|\u5E76|\u5C1A|\u4ECD|\u6682(?:\u65F6)?|\u867D\u7136|\u5C3D\u7BA1)[ \\t]*){0,3}(?:\u6CA1\u6709|\u65E0|(?:\u6CA1|\u672A)(?:\u80FD)?(?:\u67E5\u8BE2\u5230|\u67E5\u627E\u5230|\u68C0\u7D22\u5230|\u5339\u914D\u5230|\u627E\u5230|\u67E5\u5230)|\u672A\u547D\u4E2D)(?:" + DEFINITION_MATCH + ")?" + DEFINITION_ITEM;
  var PUBLICATION_ONLY = new RegExp("^(?:[-*#]+\\s*)?(?:\u5F53\u524D|\u672C\u6B21|\u672C\u8F6E)?(?:(?:\u6388\u6743)?(?:\u76EE\u5F55|\u6307\u6807\u5E93|\u4E1A\u52A1\u53E3\u5F84)(?:\u4E2D|\u5185)\\s*)?(?:" + DEFINITION_MISSING + "|(?:\u76EE\u5F55\u548C)?\u5DF2\u53D1\u5E03\u6307\u6807\u4E2D\u6CA1\u6709(?:\u4EFB\u4F55)?\u5E26[^\u3002\uFF1B;\\n]{1,60}\u5B57\u6837\u7684(?:\u4E1A\u52A1)?\u6307\u6807(?:\u6216\u53E3\u5F84)?)[\uFF0C,\u3002\uFF1B;\uFF01!]*$");
  var DATA_LIST_MEMBER = new RegExp("^" + DATA_ITEM + "$");
  var INVENTORY_QUALIFIER = "(?<qualifier>(?:(?:\u4E0E|\u548C|\u540C|\u8DDF|\u9488\u5BF9)[^\uFF0C,\u3002\uFF1B;\uFF01!\uFF1F?\u3001\\n]{1,64}?)?(?:\u76F8\u5173|\u5BF9\u5E94|\u5339\u914D|\u6709\u5173)\u7684)?";
  var INVENTORY_LIST = new RegExp(INVENTORY_QUALIFIER + LIST_MEMBER + "(?:" + LIST_SEPARATOR + LIST_MEMBER + "){1,12}(?=[ \\t]*(?:[\uFF0C,\u3002\uFF1B;\uFF01!\uFF1A:]|[\uFF08(][^\uFF09)\\n]{0,120}(?:\u5B57\u6BB5|\u5217\u540D|\u6570\u636E\u9879)|(?:\u4E0E|\u548C)(?:\u5176|\u4E4B|\u8BE5\u5BF9\u8C61|\u6240\u95EE\u5BF9\u8C61)(?:\u76F8?\u5BF9\u5E94|\u5339\u914D|\u76F8\u5173)|$))", "g");
  var INVENTORY_SEPARATOR = new RegExp("(" + LIST_SEPARATOR + ")");
  var APPENDED_PUBLICATION = new RegExp("(?:[\uFF0C,]\\s*)?[ \\t]*(?:\u4E14|\u5E76\u4E14|\u800C\u4E14|\u540C\u65F6)\\s*(?:\\*\\*)?" + DEFINITION_MISSING + "(?:\\*\\*)?(?=[ \\t]*[\uFF0C,\u3002\uFF1B;\uFF01!]|[ \\t]*$)", "g");
  var REGISTRY_LOOKUP_STATUS = new RegExp(
    "(?:\u5728)?(?:\u5F53\u524D|\u76EE\u524D|\u73B0\u6709|\u672C\u9879\u76EE)?(?:\u7684)?(?:\u5DF2)?(?:\u6388\u6743)?(?:\u76EE\u5F55|\u6307\u6807\u5E93|\u914D\u7F6E\u6E05\u5355|\u4E1A\u52A1\u914D\u7F6E)(?:\u4E2D|\u5185|\u91CC)?\\s*(?:\u4E5F|\u8FD8|\u4ECD)?(?:\u5C1A\u672A|\u6682\u672A|\u6CA1\u6709|\u672A)(?:\u53D1\u5E03|\u767B\u8BB0|\u5F55\u5165|\u5B9A\u4E49|\u914D\u7F6E)\\s*(?:\\*\\*)?(?:_{2,}|[^_\uFF0C,\u3002\uFF1B;\uFF01\uFF1F?\uFF1A:\\n]{1,44}?)(?:\\*\\*)?\\s*(?:\u7684)?(?:\u76F8\u5173|\u5BF9\u5E94|\u5339\u914D|\u6B63\u5F0F|\u5DF2\u53D1\u5E03|\u4E1A\u52A1){0,3}(?:\u7684)?(?:\u4E1A\u52A1)?(?:\u53E3\u5F84|\u6307\u6807)(?:\u5B9A\u4E49)?\\s*(?:[\uFF0C,]\\s*(?:\u4E5F|\u8FD8|\u540C\u65F6|\u4F46(?:\u662F)?|\u4E0D\u8FC7|\u5E76\u4E14?)?\\s*|[\u3002\uFF1B;\uFF01!]|$)",
    "g"
  );
  var SYNTHETIC_MEASUREMENT_SUFFIX = /([（(])(?:模拟|合成)([日周月季年](?:度)?(?:发生量|累计量|观测量|观测值|统计值))([）)])$/;
  function publicMetricName(result) {
    const metric = result?.metric_contract, contract = result?.answer_contract;
    if (result?.status !== "succeeded" || !result.result_ref || result.receipt?.read_only !== true || result.source_provenance?.synthetic !== true || contract?.source?.synthetic !== true || contract.result_ref !== result.result_ref || !metric?.id || metric.id !== contract.metric_id || [result.purpose, result.result_lineage?.purpose].some((purpose) => purpose === "explore" || purpose === "probe") || typeof metric.name !== "string") return null;
    const label = metric.name.replace(SYNTHETIC_MEASUREMENT_SUFFIX, "$1$2$3");
    return label === metric.name ? null : label;
  }
  function publicMetricLabel(text, result) {
    const label = publicMetricName(result);
    return label && typeof text === "string" ? text.replaceAll(result.metric_contract.name, () => label) : text;
  }

  // plugin/packages/natural-data/client-entry.js
  var publicUrls = createPublicUrls(globalThis);
  window.__ModuleLoader__.load({ id: "@natural-work/ask-data", factory: (require2) => {
    const React = require2("react");
    const h = React.createElement;
    const UI = require2("@deepseek-ai/dsh-client-ui-primitives");
    const { Button, Switch, Checkbox, Input, Menu, DisclosureRow, SegmentedControl, IconDataOutlineMedium, IconChevronDownOutlineMedium } = UI;
    let lifetime = new AbortController();
    let openAdministration = null;
    let openNativeSession = null;
    async function fetch(url, options = {}) {
      const owner = lifetime;
      owner.signal.throwIfAborted();
      const request = new AbortController();
      const abort = () => request.abort();
      owner.signal.addEventListener("abort", abort, { once: true });
      options.signal?.addEventListener("abort", abort, { once: true });
      if (options.signal?.aborted) abort();
      try {
        const response = await globalThis.fetch(url, { ...options, signal: request.signal });
        for (const method of ["json", "text", "blob", "arrayBuffer"]) {
          const read = response[method].bind(response);
          response[method] = async () => {
            try {
              const value = await read();
              owner.signal.throwIfAborted();
              return value;
            } finally {
              release();
            }
          };
        }
        return response;
      } catch (error) {
        release();
        throw error;
      }
      function release() {
        owner.signal.removeEventListener("abort", abort);
        options.signal?.removeEventListener("abort", abort);
      }
    }
    const inject = ["slots", "uiConversation", "sessions", "layout"];
    class ViewController {
      constructor(io) {
        this.io = io;
        this.current = null;
        this.pending = false;
        this.generation = 0;
      }
      accept(value, patch) {
        if (this.current && value.result_ref === this.current.result_ref && (value.view_revision || 0) < (this.current.view_revision || 0)) return false;
        this.current = value;
        this.io.accept(value, patch);
        return true;
      }
      async select(kind) {
        if (this.pending || !this.current) return false;
        this.pending = true;
        const generation = ++this.generation, resultRef = this.current.result_ref;
        const active = () => generation === this.generation && this.current?.result_ref === resultRef;
        let committed = false;
        this.io.busy(true);
        this.io.error("");
        try {
          const patch = await this.io.update(kind, this.current.view_revision);
          committed = true;
          if (!active()) return false;
          const updated = mergeViewPatch(this.current, patch);
          const value = updated || await this.io.load();
          if (!active() || value.result_ref !== resultRef || !this.accept(value, updated ? patch : void 0)) return false;
          this.io.selected?.();
          return true;
        } catch (error) {
          if (active()) {
            this.io.error(error.message);
            if (committed || error.status === 409) try {
              const value = await this.io.load();
              if (active() && value.result_ref === resultRef) this.accept(value);
            } catch {
            }
          }
          return false;
        } finally {
          if (generation === this.generation) {
            this.pending = false;
            this.io.busy(false);
          }
        }
      }
      dispose() {
        this.generation++;
        this.pending = false;
      }
    }
    const RESULT_CACHE_LIMIT = 128;
    const resultCache = /* @__PURE__ */ new Map();
    const resultCacheEtags = /* @__PURE__ */ new WeakMap();
    const resultMutationMarks = /* @__PURE__ */ new Map();
    const CACHE_INVALIDATE_MUTATIONS = /* @__PURE__ */ new Set(["business_update", "policy_update", "connection_update", "bind", "make_report", "edit_report", "excel_import"]);
    function queryParam(query, key) {
      if (!query) return "";
      const q = String(query);
      if (typeof URLSearchParams === "function") return new URLSearchParams(q).get(key) || "";
      return new URL(q.startsWith("?") ? q : "?" + q, "http://localhost").searchParams.get(key) || "";
    }
    function resultCacheKey(query) {
      if (!query) return "";
      const ref = queryParam(query, "result_ref");
      if (!ref) return String(query);
      return `${queryParam(query, "project")}::${ref}`;
    }
    function cloneResultSnapshot(value) {
      if (value === null || typeof value !== "object") return value;
      if (typeof structuredClone === "function") return structuredClone(value);
      return JSON.parse(JSON.stringify(value));
    }
    function cacheResultEntry(cache, key, value, etag = null) {
      if (!cache || !key || value === void 0) return;
      if (cache.has(key)) cache.delete(key);
      else while (cache.size >= RESULT_CACHE_LIMIT) {
        const oldest = cache.keys().next().value;
        if (oldest === void 0) break;
        cache.delete(oldest);
      }
      const snapshot = cloneResultSnapshot(value);
      cache.set(key, snapshot);
      if (etag) resultCacheEtags.set(snapshot, etag);
    }
    function invalidateResultRef(ref) {
      if (!ref) return;
      const suffix = `::${ref}`;
      for (const key of [...resultCache.keys()]) {
        if (key === ref || key.endsWith(suffix)) resultCache.delete(key);
      }
      resultCache.delete(resultCacheKey(ref));
    }
    function frameHeightStorageKey(query) {
      if (!query) return "";
      const ref = queryParam(query, "result_ref");
      return ref ? `nw:fh:${queryParam(query, "project")}::${ref}:chart` : "";
    }
    function readFrameHeight(query) {
      const key = frameHeightStorageKey(query);
      if (!key || typeof sessionStorage === "undefined") return 280;
      try {
        const raw = Number(sessionStorage.getItem(key));
        return Number.isFinite(raw) && raw >= 40 && raw <= 1200 ? capResultFrameHeight(Math.round(raw)) : 280;
      } catch {
        return 280;
      }
    }
    function writeFrameHeight(query, height) {
      const key = frameHeightStorageKey(query);
      if (!key || typeof sessionStorage === "undefined") return;
      const n = Math.round(Number(height));
      if (!Number.isFinite(n) || n < 40 || n > 1200) return;
      try {
        sessionStorage.setItem(key, String(n));
      } catch {
      }
    }
    function createFrameGate(limit = 2) {
      let active = 0;
      const queue = [];
      const pump = () => {
        while (active < limit && queue.length) {
          const next = queue.shift();
          if (next.aborted) continue;
          active++;
          next.grant();
        }
      };
      return {
        acquire(signal) {
          if (signal?.aborted) return Promise.reject(Object.assign(Error("Aborted"), { name: "AbortError" }));
          return new Promise((resolve, reject) => {
            let settled = false, released = false, onAbort = null;
            const cleanup = () => {
              if (onAbort && signal) signal.removeEventListener("abort", onAbort);
            };
            const release = () => {
              if (released) return;
              released = true;
              cleanup();
              active = Math.max(0, active - 1);
              pump();
            };
            const item = { aborted: false, grant: () => {
              settled = true;
              cleanup();
              resolve(release);
            } };
            onAbort = () => {
              item.aborted = true;
              if (!settled) {
                settled = true;
                cleanup();
                reject(Object.assign(Error("Aborted"), { name: "AbortError" }));
              }
            };
            if (signal) signal.addEventListener("abort", onAbort, { once: true });
            queue.push(item);
            pump();
          });
        },
        stats: () => ({ active, queued: queue.length })
      };
    }
    const defaultFrameGate = createFrameGate(2);
    async function loadResult(query, { fetcher = fetch, wait = (ms) => new Promise((r) => setTimeout(r, ms)), signal, bypassCache = false, cache = fetcher === fetch ? resultCache : null } = {}) {
      const cacheKey = resultCacheKey(query);
      const cached = !bypassCache && cacheKey ? cache?.get(cacheKey) : null;
      const etag = cached ? resultCacheEtags.get(cached) : null;
      for (let attempt = 0; attempt < 3; attempt++) {
        if (signal?.aborted) throw Object.assign(Error("\u5DF2\u53D6\u6D88\u8BFB\u53D6"), { name: "AbortError" });
        try {
          const response = await fetcher(
            publicUrls.route("/natural-data/api/result?") + query,
            { signal, cache: "no-store", ...etag ? { headers: { "If-None-Match": etag } } : {} }
          );
          if (response.status === 304 && cached) {
            await response.text();
            cache.delete(cacheKey);
            cache.set(cacheKey, cached);
            return cloneResultSnapshot(cached);
          }
          if (response.ok) {
            const data = await response.json();
            if (cache && cacheKey) cacheResultEntry(cache, cacheKey, data, response.headers?.get("etag"));
            return data;
          }
          if (cache && cacheKey && [401, 403, 404].includes(response.status)) cache.delete(cacheKey);
          const messages = { 401: "\u767B\u5F55\u5DF2\u8FC7\u671F\uFF0C\u8BF7\u5237\u65B0\u9875\u9762\u540E\u91CD\u65B0\u6253\u5F00\u7ED3\u679C\u3002", 403: "\u5F53\u524D\u65E0\u6CD5\u8BBF\u95EE\u6B64\u7ED3\u679C\uFF0C\u8BF7\u68C0\u67E5\u9879\u76EE\u7684\u6570\u636E\u6743\u9650\u3002", 404: "\u672A\u627E\u5230\u5DF2\u4FDD\u5B58\u7684\u7ED3\u679C\uFF0C\u8BF7\u68C0\u67E5\u7ED3\u679C\u662F\u5426\u5DF2\u79FB\u9664\u3002" };
          throw Object.assign(Error(messages[response.status] || "\u6682\u65F6\u65E0\u6CD5\u8BFB\u53D6\u5DF2\u4FDD\u5B58\u7684\u7ED3\u679C\uFF0C\u8BF7\u91CD\u65B0\u52A0\u8F7D\u3002"), { status: response.status });
        } catch (error) {
          if (signal?.aborted || error.name === "AbortError") throw error;
          const retryable = !error.status || [408, 429, 500, 502, 503, 504].includes(error.status);
          if (!retryable || attempt === 2) throw error.status ? error : Error("\u8FDE\u63A5\u6682\u65F6\u4E2D\u65AD\uFF0C\u8BF7\u91CD\u65B0\u52A0\u8F7D\u5DF2\u4FDD\u5B58\u7684\u7ED3\u679C\u3002");
          await wait(300 * (attempt + 1));
        }
      }
    }
    ViewController.loadResult = loadResult;
    ViewController.resultCache = resultCache;
    ViewController.clearResultCache = () => {
      resultCache.clear();
      resultMutationMarks.clear();
    };
    ViewController.invalidateResultCache = invalidateResultRef;
    ViewController.createFrameGate = createFrameGate;
    ViewController.readFrameHeight = readFrameHeight;
    ViewController.writeFrameHeight = writeFrameHeight;
    const css = `
.nw-settings{display:flex;flex-direction:column;width:100%;color:var(--dsw-alias-label-primary)}
.nw-setting-row{display:flex;justify-content:space-between;align-items:center;gap:24px;padding:16px 0;border-bottom:.5px solid var(--dsw-alias-border-l2)}
.nw-setting-copy{min-width:0;flex:1}.nw-setting-title{font-size:14px;line-height:20px;font-weight:400}
.nw-setting-description,.nw-setting-status{color:var(--dsw-alias-label-secondary);margin-top:4px;font-size:12px;line-height:18px;overflow-wrap:anywhere}
.nw-setting-row>button,.nw-setting-row>[role=switch]{flex-shrink:0}
.nw-setting-section{padding:16px 0;border-bottom:.5px solid var(--dsw-alias-border-l2)}
.nw-setting-details{padding:16px 0 8px 24px;display:grid;gap:16px;font-size:12px;line-height:18px}
.nw-setting-details ul{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.nw-setting-details code{font-size:11px;color:var(--dsw-alias-label-secondary);overflow-wrap:anywhere}
.nw-setting-status{margin:12px 0 0;min-height:18px}.nw-setting-status[data-error=true]{color:var(--dsw-alias-state-error-primary)}
.nw-settings>:last-child{border-bottom:none}
.nw-settings-tabs{padding:8px 0 16px}
.nw-source{border-bottom:.5px solid var(--dsw-alias-border-l2);padding:16px 0}
.nw-source-header{display:flex;align-items:center;gap:16px}.nw-source-header>div{flex:1;min-width:0}
.nw-source-meta{padding-left:24px;margin-top:4px}
.nw-source-body{margin:12px 0 0 24px;padding-left:16px;border-left:.5px solid var(--dsw-alias-border-l2)}
.nw-source-body .nw-setting-section{border:0}.nw-source-body .nw-setting-row{padding-top:8px}
.nw-settings-panel{display:flex;flex-direction:column;gap:16px;padding:8px 0 16px;min-width:0}.nw-settings-panel:focus{outline:none}
.nw-menu-anchor{display:inline-flex;max-width:100%;flex-shrink:0}
.nw-settings-menu{box-sizing:border-box;width:var(--nw-menu-width,300px)!important;max-width:var(--nw-menu-width,300px)!important;max-height:var(--nw-menu-height,260px)!important;overflow-y:auto!important;overflow-x:hidden!important}
.nw-settings-menu [role=menuitem]{white-space:normal;overflow-wrap:anywhere}
.nw-actions{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end;align-items:center;min-width:0}
.nw-settings .nw-setting-row{align-items:flex-start;flex-wrap:wrap;row-gap:12px}.nw-settings .nw-setting-copy{flex:1 1 220px}.nw-settings .nw-setting-row>.nw-actions{flex:0 1 auto;max-width:100%}
.nw-settings .nw-setting-row>.nw-menu-anchor{flex-shrink:1;min-width:0;max-width:100%}.nw-settings .nw-menu-anchor>button{max-width:100%;white-space:normal;overflow-wrap:anywhere}
.nw-settings .nw-actions>button{flex-shrink:0;max-width:100%;white-space:normal;height:auto;min-height:28px}
.nw-field{display:grid;gap:8px;min-width:0;font-size:12px;line-height:18px}.nw-field>span{color:var(--dsw-alias-label-secondary)}
.nw-settings .nw-field{margin:0}.nw-settings .nw-setting-description{margin-bottom:0}
.nw-member-form{display:grid;gap:16px;padding:8px 0}.nw-member-form .nw-actions{justify-content:flex-start}
.nw-settings-tabs{max-width:100%;overflow-x:auto}.nw-settings-tabs:not(:first-child){padding-top:20px}
.nw-field input{width:100%;min-width:0;box-sizing:border-box}.nw-business-entry{display:grid;gap:10px;padding:12px 0;border-bottom:.5px solid var(--dsw-alias-border-l2)}
.nw-danger-zone{margin-top:28px;padding-top:20px;border-top:1px solid var(--dsw-alias-border-l2)}
.nw-danger-zone .nw-setting-title{color:var(--dsw-alias-state-error-primary);font-weight:500}.nw-danger-button{color:var(--dsw-alias-state-error-primary)!important;border-color:var(--dsw-alias-state-error-primary)!important}
.nw-connection-form{display:grid;gap:14px;padding:16px 0}.nw-connection-fields{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}.nw-connection-fields>.nw-field-wide{grid-column:1/-1}
.nw-connection-form .nw-field{margin:0}.nw-connection-form .nw-actions{justify-content:flex-start}.nw-connection-form input{font:inherit}
@media(max-width:600px){.nw-connection-fields{grid-template-columns:minmax(0,1fr)}}
.nw-table-options{display:grid;gap:10px;padding-top:12px}.nw-settings-panel .nw-setting-row>div:first-child{overflow-wrap:anywhere}
@media(max-width:600px){.nw-settings>.nw-setting-row{flex-wrap:wrap}.nw-settings>.nw-setting-row>.nw-actions{max-width:100%}}
.nw-data-tool{border:.5px solid var(--dsw-alias-border-l2);border-radius:12px;padding:12px;margin:6px 0;color:var(--dsw-alias-label-primary)}
/* alpha.2 widens Markdown tables past the chat column. Scope the override to
   a conversation flow containing our result slot; retain the native wrapper. */
[data-slot="conversation.chat.flow"]:has([data-natural-data-answer]) .md-table-wide{box-sizing:border-box;width:100%;max-width:100%;margin-left:0;padding-left:0;max-height:min(420px,60vh);overflow:auto}
/* A twelve-month table must stay readable without swallowing the page; the
   header stays put while the rows scroll. */
[data-slot="conversation.chat.flow"]:has([data-natural-data-answer]) .md-table-wide>table thead th{position:sticky;top:0;z-index:1;background:var(--dsw-alias-bg-base);box-shadow:inset 0 -1px var(--dsw-alias-border-l2)}
[data-natural-data-answer]{min-width:0;max-width:100%}
.nw-data-tool-header{display:flex;align-items:center;gap:8px;font-size:13px;line-height:20px}
.nw-data-tool iframe{border:0;width:100%;height:280px;margin-top:8px;display:block;color-scheme:inherit;border-radius:8px}
.nw-data-tool-stage{position:relative;margin-top:8px;border-radius:8px;overflow:hidden}
.nw-data-tool .nw-data-tool-stage iframe{margin-top:0;width:100%;height:100%;display:block;border:0;color-scheme:inherit;border-radius:8px;opacity:0;pointer-events:none}
.nw-data-tool .nw-data-tool-stage iframe.nw-frame-ready{opacity:1;pointer-events:auto;transition:opacity 120ms ease}
.nw-skeleton{position:absolute;inset:0;padding:14px;display:flex;flex-direction:column;gap:12px;background:var(--dsw-alias-bg-layer-2,rgba(128,128,128,.04));border-radius:8px;pointer-events:none;box-sizing:border-box}
.nw-skel-line{border-radius:6px;background:linear-gradient(90deg,rgba(128,128,128,.08) 25%,rgba(128,128,128,.16) 50%,rgba(128,128,128,.08) 75%);background-size:200% 100%;animation:nw-shimmer 1.4s ease-in-out infinite}
.nw-skel-bars{flex:1;display:flex;align-items:flex-end;gap:14px;padding:12px 8px 4px}
.nw-skel-bars>span{flex:1;border-radius:6px 6px 0 0}
.nw-skel-rows{flex:1;display:grid;gap:10px;align-content:start;padding-top:4px}
.nw-skel-grid{flex:1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.nw-skel-circle{flex:1;display:flex;align-items:center;justify-content:center;gap:20px}
.nw-skel-circle-ring{width:132px;height:132px;border-radius:50%;flex-shrink:0}
.nw-sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@keyframes nw-shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
@media(prefers-reduced-motion:reduce){.nw-data-tool .nw-data-tool-stage iframe.nw-frame-ready{transition:none}.nw-skel-line{animation:none}}
.nw-process-results{margin:6px 0;color:var(--dsw-alias-label-secondary);font-size:13px}.nw-process-list{padding-left:8px}
@media(max-width:600px){.nw-setting-row{gap:12px}.nw-setting-details{padding-left:0}}
`;
    function SettingRow({ title, description, children }) {
      return h("div", { className: "nw-setting-row" }, h("div", { className: "nw-setting-copy" }, h("div", { className: "nw-setting-title" }, title), description && h("div", { className: "nw-setting-description" }, description)), children);
    }
    function Detail({ title, children, initialOpen = false }) {
      const [open, setOpen] = React.useState(initialOpen);
      return h("div", { className: "nw-setting-section" }, h(DisclosureRow, { title, open, expandable: true, expandOnRowClick: true, icon: h(IconDataOutlineMedium, { size: 16 }), onToggle: () => setOpen((v) => !v) }, h("div", { className: "nw-setting-details" }, children)));
    }
    async function settingsRequest(op, body, query) {
      const owner = lifetime;
      if (op === "business_update") body = { ...body, response_view: "manager" };
      for (let attempt = 0; ; attempt++) {
        owner.signal.throwIfAborted();
        const url = publicUrls.route("/natural-data/api/") + op + (query ? "?" + new URLSearchParams(query) : "");
        const r = await fetch(url, body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {});
        const raw = await r.text();
        if (op === "config" && [404, 503].includes(r.status) && attempt < 19) {
          await new Promise((resolve, reject) => {
            const abort = () => {
              clearTimeout(timer);
              reject(new DOMException("\u63D2\u4EF6\u5DF2\u505C\u7528", "AbortError"));
            };
            const timer = setTimeout(() => {
              owner.signal.removeEventListener("abort", abort);
              resolve();
            }, 250);
            owner.signal.addEventListener("abort", abort, { once: true });
            if (owner.signal.aborted) abort();
          });
          continue;
        }
        let v;
        try {
          v = raw ? JSON.parse(raw) : null;
        } catch {
          throw Error("\u670D\u52A1\u54CD\u5E94\u683C\u5F0F\u9519\u8BEF\uFF0C\u8BF7\u91CD\u65B0\u8BFB\u53D6\u914D\u7F6E\u3002");
        }
        if (!r.ok || v === null) {
          const error = Error(v?.error || "\u6570\u636E\u5F15\u64CE\u5C1A\u672A\u5C31\u7EEA\uFF0C\u8BF7\u7A0D\u540E\u91CD\u65B0\u8BFB\u53D6\u914D\u7F6E\u3002");
          error.status = r.status;
          throw error;
        }
        if (CACHE_INVALIDATE_MUTATIONS.has(op)) resultCache.clear();
        return v;
      }
    }
    function settingsMenuPlacement(node) {
      const a = node.getBoundingClientRect(), bounds = { top: 0, bottom: innerHeight, left: 0, right: innerWidth };
      for (let parent = node.parentElement; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent), r = parent.getBoundingClientRect();
        if (/auto|scroll|hidden|clip/.test(style.overflowX)) {
          bounds.left = Math.max(bounds.left, r.left);
          bounds.right = Math.min(bounds.right, r.right);
        }
        if (/auto|scroll|hidden|clip/.test(style.overflowY)) {
          bounds.top = Math.max(bounds.top, r.top);
          bounds.bottom = Math.min(bounds.bottom, r.bottom);
        }
      }
      const below = bounds.bottom - a.bottom - 8, above = a.top - bounds.top - 8, side = below >= Math.min(220, above) ? "bottom" : "top";
      const start = bounds.right - a.left - 8, end = a.right - bounds.left - 8, align = start >= Math.min(300, end) ? "start" : "end";
      return { side, align, height: Math.max(32, Math.min(300, side === "bottom" ? below : above)), width: Math.max(32, Math.min(300, align === "start" ? start : end)) };
    }
    function SettingsMenu({ anchor, open, ...props }) {
      const root = React.useRef(null), [placement, setPlacement] = React.useState({ side: "bottom", align: "start", height: 260, width: 300 });
      React.useLayoutEffect(() => {
        if (!open) return;
        const measure = () => {
          if (root.current) setPlacement(settingsMenuPlacement(root.current.querySelector("button") || root.current));
        };
        measure();
        window.addEventListener("resize", measure);
        window.addEventListener("scroll", measure, true);
        return () => {
          window.removeEventListener("resize", measure);
          window.removeEventListener("scroll", measure, true);
        };
      }, [open]);
      return h("span", { ref: root, className: "nw-menu-anchor", onKeyDownCapture: (e) => {
        if (open && e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          props.onClose();
          root.current?.querySelector("button")?.focus();
        }
      }, style: { "--nw-menu-height": placement.height + "px", "--nw-menu-width": placement.width + "px" } }, h(Menu, { ...props, open, anchor, align: placement.align, side: placement.side, listClassName: "nw-settings-menu" }));
    }
    function DeleteAction({ name, description, disabled, onDelete, affected = [] }) {
      const [confirm, setConfirm] = React.useState(false), [detach, setDetach] = React.useState(false);
      return h("div", { className: "nw-danger-zone" }, h(SettingRow, { title: "\u5220\u9664" + name, description }, confirm ? h("div", { className: "nw-actions" }, h(Button, { variant: "outline", className: "nw-danger-button", size: "sm", disabled: disabled || affected.length > 0 && !detach, onClick: () => onDelete(detach) }, "\u786E\u8BA4\u5220\u9664" + name), h(Button, { variant: "ghost", size: "sm", disabled, onClick: () => {
        setConfirm(false);
        setDetach(false);
      } }, "\u53D6\u6D88\u5220\u9664")) : h(Button, { variant: "outline", className: "nw-danger-button", size: "sm", disabled, onClick: () => setConfirm(true) }, "\u5220\u9664" + name)), confirm && affected.length > 0 && h(Checkbox, { label: "\u540C\u65F6\u79FB\u9664\u4EE5\u4E0B\u9879\u76EE\u7684\u6570\u636E\u6E90\u5F15\u7528\uFF0C\u7ACB\u5373\u64A4\u9500\u67E5\u8BE2\u6388\u6743\uFF1A" + affected.join("\u3001"), checked: detach, disabled, onChange: setDetach }));
    }
    function ProjectSources({ sources, draft, setDraft, editable, busy, onTest, onManage }) {
      const connected = sources.filter((s) => draft.source_ids.includes(s.id)), available = sources.filter((s) => !draft.source_ids.includes(s.id));
      const row = (s) => h(ProjectSource, { key: s.id, source: s, draft, setDraft, editable, busy, onTest, onManage });
      return h(
        React.Fragment,
        null,
        h("p", { className: "nw-setting-description" }, "\u8349\u7A3F\u4FDD\u5B58\u5E76\u53D1\u5E03\u540E\u751F\u6548\u3002\u79FB\u9664\u6570\u636E\u6E90\u4F1A\u540C\u65F6\u79FB\u9664\u8349\u7A3F\u4E2D\u5173\u8054\u7684\u67E5\u8BE2\u793A\u4F8B\u548C\u7ED3\u6784\u5316\u6307\u6807\uFF1B\u53D1\u5E03\u540E\u64A4\u9500\u67E5\u8BE2\u6388\u6743\uFF0C\u5386\u53F2\u7ED3\u679C\u4FDD\u7559\u3002"),
        connected.map(row),
        !connected.length && h("p", { className: "nw-setting-description" }, "\u5C1A\u672A\u63A5\u5165\u6570\u636E\u6E90\u3002"),
        editable && available.length > 0 && h(Detail, { title: "\u63A5\u5165\u5176\u4ED6\u6570\u636E\u6E90\uFF08" + available.length + "\uFF09" }, available.map(row))
      );
    }
    function ProjectSource({ source: s, draft, setDraft, editable, busy, onTest, onManage }) {
      const [open, setOpen] = React.useState(false), enabled = draft.source_ids.includes(s.id);
      function bind(checked) {
        setDraft((d) => ({ ...d, source_ids: checked ? [...d.source_ids, s.id] : d.source_ids.filter((id) => id !== s.id), tables: checked ? { ...d.tables, [s.id]: s.tables.map((t) => t.name) } : Object.fromEntries(Object.entries(d.tables).filter(([id]) => id !== s.id)), examples: checked ? d.examples : d.examples.filter((r) => r.source_id !== s.id), terms: checked ? d.terms : d.terms.filter((r) => r.config?.kind !== "semantic-metric" || r.config.source_id !== s.id) }));
      }
      function table2(name, checked) {
        setDraft((d) => ({ ...d, tables: { ...d.tables, [s.id]: checked ? [...d.tables[s.id], name] : d.tables[s.id].filter((n) => n !== name) } }));
      }
      return h(
        "div",
        { className: "nw-source" },
        h(
          "div",
          { className: "nw-source-header" },
          h(
            "div",
            null,
            h(DisclosureRow, { title: s.name, open, expandable: true, expandOnRowClick: true, icon: h(IconDataOutlineMedium, { size: 16 }), onToggle: () => setOpen((v) => !v) }),
            h("div", { className: "nw-setting-description nw-source-meta" }, `${enabled ? "\u5DF2\u63A5\u5165" : "\u672A\u63A5\u5165"}${s.enabled === false ? " \xB7 \u8FDE\u63A5\u5DF2\u505C\u7528" : ""} \xB7 ${s.dialect} \xB7 ${enabled ? (draft.tables[s.id] || []).length : 0}/${s.table_count} \u5F20\u8868${s.synthetic ? " \xB7 \u6A21\u62DF\u6570\u636E" : ""}`)
          ),
          h("div", { className: "nw-actions" }, onManage && h(Button, { variant: "outline", size: "sm", disabled: busy, onClick: () => onManage(s.id) }, "\u7BA1\u7406\u8FDE\u63A5"), h(Switch, { label: "\u5728\u4E1A\u52A1\u9879\u76EE\u4E2D\u63A5\u5165" + s.name, checked: enabled, disabled: busy || !editable, onChange: bind }))
        ),
        open && h(
          "div",
          { className: "nw-source-body" },
          enabled && editable && h(SettingRow, { title: "\u8FDE\u63A5\u68C0\u67E5", description: "\u5148\u4FDD\u5B58\u8349\u7A3F\uFF0C\u518D\u6D4B\u8BD5\u8FDE\u63A5\u3002" }, h(Button, { variant: "outline", size: "sm", disabled: busy, onClick: () => onTest(s.id) }, "\u6D4B\u8BD5\u8FDE\u63A5")),
          h("div", { className: "nw-setting-description" }, enabled ? "\u4EC5\u6240\u9009\u8868\u53EF\u7528\u4E8E\u8BE5\u9879\u76EE\u7684\u67E5\u8BE2\u3002" : "\u63A5\u5165\u6570\u636E\u6E90\u540E\u53EF\u4EE5\u9009\u62E9\u6388\u6743\u8868\u3002"),
          enabled && h("div", { className: "nw-table-options" }, s.tables.map((t) => h(Checkbox, { key: t.name, label: (t.label ? t.label + " \xB7 " : "") + t.name, checked: (draft.tables[s.id] || []).includes(t.name), disabled: busy || !editable, onChange: (checked) => table2(t.name, checked) })), (draft.tables[s.id] || []).filter((name) => !s.tables.some((t) => t.name === name)).map((name) => h(Checkbox, { key: name, label: name + "\uFF08\u6E90\u5E93\u4E2D\u5DF2\u4E0D\u5B58\u5728\uFF0C\u8BF7\u53D6\u6D88\u6388\u6743\uFF09", checked: true, disabled: busy || !editable, onChange: (checked) => table2(name, checked) })))
        )
      );
    }
    const { OnboardingPanel, MetricBuilder, TemplateTransfer, PackImport } = createProductSettings({ React, h, Input, Button, Checkbox, SettingRow, Detail, SettingsMenu, IconChevronDownOutlineMedium, request: settingsRequest });
    const businessGroups = [["terms", "\u4E1A\u52A1\u672F\u8BED\u4E0E\u6307\u6807\u53E3\u5F84", ["name", "\u540D\u79F0"], ["definition", "\u5B9A\u4E49\u3001\u5355\u4F4D\u3001\u8BA1\u7B97\u53CA\u65F6\u95F4\u89C4\u5219"]], ["examples", "\u67E5\u8BE2\u793A\u4F8B", ["question", "\u4E1A\u52A1\u95EE\u9898"], ["logical_sql", "\u53EA\u8BFB SQL"]], ["skills", "\u5206\u6790\u65B9\u6CD5\u8BF4\u660E", ["name", "\u540D\u79F0"], ["description", "\u9002\u7528\u8303\u56F4\u4E0E\u4E1A\u52A1\u89C4\u5219"]], ["layers", "\u7A7A\u95F4\u56FE\u5C42\u8BF4\u660E", ["name", "\u540D\u79F0"], ["description", "\u56FE\u5C42\u6765\u6E90\u53CA\u9002\u7528\u8303\u56F4"]]];
    function BusinessEditor({ project, draft, setDraft, editable, busy, sources }) {
      const [editing, setEditing] = React.useState(null);
      function change(group, id, field, value) {
        setDraft((d) => ({ ...d, [group]: d[group].map((row) => row.id === id ? { ...row, [field]: value } : row) }));
      }
      return h(
        React.Fragment,
        null,
        h("p", { className: "nw-setting-description" }, "\u5C55\u5F00\u4E0B\u65B9\u5206\u7C7B\u53EF\u6DFB\u52A0\u3001\u7F16\u8F91\u548C\u79FB\u9664\u6761\u76EE\u3002\u914D\u7F6E\u5F52\u5C5E\u5F53\u524D\u4E1A\u52A1\u9879\u76EE\u3002\u4FDD\u5B58\u8349\u7A3F\u4E0D\u4F1A\u6539\u53D8\u6B63\u5728\u751F\u6548\u7684\u53E3\u5F84\uFF1B\u53D1\u5E03\u540E\uFF0C\u65B0\u6267\u884C\u8BFB\u53D6\u65B0\u7248\u672C\u3002\u5206\u6790\u6280\u80FD\u53EF\u7ED1\u5B9A\u63D2\u4EF6\u5185\u7684\u53EA\u8BFB\u65B9\u6CD5\uFF1B\u6307\u6807\u53EF\u914D\u7F6E\u7ED3\u6784\u5316\u8BA1\u7B97\u53E3\u5F84\u3002\u7A7A\u95F4\u529F\u80FD\u672C\u671F\u672A\u542F\u7528\u3002"),
        editable && h(Button, { variant: "outline", size: "sm", disabled: busy || !draft.source_ids.length, onClick: () => setEditing({}) }, "\u7528\u8868\u5355\u914D\u7F6E\u6307\u6807"),
        editing && h(MetricBuilder, { key: editing.id || "new", project, draft, setDraft, sources, disabled: busy, initial: editing.id ? editing : null, onClose: () => setEditing(null) }),
        businessGroups.map(([group, label, ...fields]) => h(
          Detail,
          { key: group, title: label + "\uFF08" + draft[group].length + "\uFF09" },
          draft[group].map((row) => h(
            "div",
            { key: row.id, className: "nw-business-entry" },
            fields.map(([field, title]) => editable ? h("label", { key: field, className: "nw-field" }, h("span", null, title), h(Input, { value: row[field] || "", "aria-label": label + " " + title, disabled: busy, onChange: (e) => change(group, row.id, field, e.target.value) })) : h("div", { key: field }, h("div", { className: "nw-setting-title" }, row[field]))),
            group === "terms" && row.config?.kind === "semantic-metric" && editable && h(Button, { variant: "outline", size: "sm", disabled: busy, onClick: () => setEditing(row) }, "\u7528\u8868\u5355\u7F16\u8F91\u6307\u6807"),
            ["terms", "skills"].includes(group) && editable && h(ContractEditor, { row, group, disabled: busy, onChange: (config, config_draft) => setDraft((d) => ({ ...d, [group]: d[group].map((r) => {
              if (r.id !== row.id) return r;
              const next = { ...r };
              if (config === void 0) delete next.config;
              else next.config = config;
              if (config_draft === void 0) delete next.config_draft;
              else next.config_draft = config_draft;
              return next;
            }) })) }),
            group === "examples" && h(ExampleSource, { row, sources: sources.filter((s) => draft.source_ids.includes(s.id)), disabled: busy || !editable, onChange: (id) => change(group, row.id, "source_id", id) }),
            editable && h(Button, { variant: "ghost", size: "sm", disabled: busy, onClick: () => setDraft((d) => ({ ...d, [group]: d[group].filter((item) => item.id !== row.id) })) }, "\u79FB\u9664\u6761\u76EE")
          )),
          !draft[group].length && h("p", { className: "nw-setting-description" }, "\u5C1A\u672A\u914D\u7F6E"),
          editable && h(Button, { variant: "outline", size: "sm", disabled: busy, onClick: () => setDraft((d) => ({ ...d, [group]: [...d[group], { id: crypto.randomUUID(), ...Object.fromEntries(fields.map(([key]) => [key, ""])), ...group === "examples" ? { source_id: d.source_ids[0] || "" } : {} }] })) }, "\u6DFB\u52A0" + label)
        ))
      );
    }
    function ContractEditor({ row, group, onChange, disabled }) {
      const [raw, setRaw] = React.useState(() => row.config ? JSON.stringify(row.config) : ""), [error, setError] = React.useState("");
      function edit(text) {
        setRaw(text);
        try {
          const value = text.trim() ? JSON.parse(text) : void 0;
          if (value !== void 0 && (!value || typeof value !== "object" || Array.isArray(value))) throw Error("\u9700\u8981JSON\u5BF9\u8C61");
          onChange(value, void 0);
          setError("");
        } catch {
          onChange(row.config, text);
          setError("\u7ED3\u6784\u5316\u914D\u7F6E\u5C1A\u672A\u5B8C\u6210\uFF0C\u8865\u5168\u540E\u624D\u80FD\u4FDD\u5B58\u3002");
        }
      }
      return h(
        Detail,
        { title: group === "terms" ? "\u9AD8\u7EA7\u6307\u6807\u914D\u7F6E\uFF08JSON\uFF09" : "\u53EF\u6267\u884C\u5206\u6790\u6280\u80FD" },
        h("p", { className: "nw-setting-description" }, group === "terms" ? "\u666E\u901A\u6307\u6807\u53EF\u4F7F\u7528\u4E0A\u65B9\u8868\u5355\u3002\u9AD8\u7EA7\u914D\u7F6E\u652F\u6301\u5FEB\u7167\u3001\u7B5B\u9009\u53CA\u552F\u4E00\u952E\u5173\u8054\uFF1B\u7A7A\u914D\u7F6E\u4EC5\u4E3A\u672F\u8BED\u8BF4\u660E\u3002\u4FDD\u5B58\u5E76\u53D1\u5E03\u540E\u624D\u53EF\u6267\u884C\u3002" : "\u4F7F\u7528\u5DF2\u5B89\u88C5\u7684matrix\u65B9\u6CD5\u3001\u5B57\u6BB5\u4E0E\u53C2\u6570\u7EC4\u5408\u6210\u9879\u76EE\u6280\u80FD\u3002\u53EA\u8FD0\u884C\u58F0\u660E\u5F0F\u53EA\u8BFB\u7B97\u5B50\u3002"),
        h(Input, { "aria-label": group === "terms" ? "\u7ED3\u6784\u5316\u6307\u6807\u53E3\u5F84 JSON" : "\u5206\u6790\u6280\u80FD\u914D\u7F6E JSON", value: raw, disabled, onChange: (e) => edit(e.target.value), placeholder: group === "terms" ? '{"kind":"semantic-metric", ...}' : '{"kind":"analysis-skill", ...}' }),
        error && h("p", { role: "alert" }, error)
      );
    }
    function ExampleSource({ row, sources, disabled, onChange }) {
      const [open, setOpen] = React.useState(false);
      return h(SettingsMenu, { open, onClose: () => setOpen(false), selectedId: row.source_id, items: sources.map((s) => ({ id: s.id, label: s.name })), onSelect: (id) => {
        onChange(id);
        setOpen(false);
      }, anchor: h(Button, { variant: "outline", size: "sm", disabled, onClick: () => setOpen((v) => !v), "aria-label": "\u67E5\u8BE2\u793A\u4F8B\u6570\u636E\u6E90" }, sources.find((s) => s.id === row.source_id)?.name || "\u9009\u62E9\u6570\u636E\u6E90", h(IconChevronDownOutlineMedium, { size: 16 })) });
    }
    function CapabilitySettings({ project, editable, systemAdmin }) {
      const [policy, setPolicy] = React.useState(null), [scope, setScope] = React.useState("project"), [open, setOpen] = React.useState(""), [busy, setBusy] = React.useState(false), [message, setMessage] = React.useState(""), [failed, setFailed] = React.useState(false);
      const labels = { enabled: "\u542F\u7528", history: "\u4EC5\u67E5\u770B\u5386\u53F2", disabled: "\u7981\u7528\u8BBF\u95EE" };
      async function load() {
        const r = await fetch(publicUrls.route("/natural-data/api/policy?") + new URLSearchParams({ project }));
        const v = await r.json();
        if (!r.ok) throw Error(v.error || "\u80FD\u529B\u8BBE\u7F6E\u8BFB\u53D6\u5931\u8D25");
        setPolicy(v);
      }
      React.useEffect(() => {
        load().catch((e) => {
          setFailed(true);
          setMessage(e.message);
        });
      }, [project]);
      async function change(changes, skills) {
        setOpen("");
        setBusy(true);
        setFailed(false);
        setMessage("");
        try {
          const v = await settingsRequest("policy_update", { project, business_project: project, scope, changes, skills, expected_revision: policy.revision });
          setPolicy(v);
          setMessage("\u80FD\u529B\u8BBE\u7F6E\u5DF2\u4FDD\u5B58\uFF1B\u8BE5\u9879\u76EE\u6240\u6709\u5DE5\u4F5C\u533A\u4E2D\u7684\u4F1A\u8BDD\u5747\u6309\u65B0\u72B6\u6001\u6267\u884C\u3002");
        } catch (e) {
          setFailed(true);
          setMessage(e.message);
          try {
            await load();
          } catch {
          }
        } finally {
          setBusy(false);
        }
      }
      if (!policy) return h("p", { role: failed ? "alert" : "status", className: "nw-setting-status" }, message || "\u6B63\u5728\u8BFB\u53D6\u80FD\u529B\u8BBE\u7F6E\u2026");
      return h(
        React.Fragment,
        null,
        h(SettingRow, { title: "\u914D\u7F6E\u8303\u56F4", description: "\u5168\u5C40\u9650\u5236\u4F18\u5148\u4E8E\u4E1A\u52A1\u9879\u76EE\uFF1B\u505C\u6B62\u95EE\u6570\u65F6\u53D6\u6D88\u5728\u9014\u6570\u636E\u5E93\u67E5\u8BE2\u3002" }, h(SettingsMenu, { open: open === "scope", onClose: () => setOpen(""), selectedId: scope, items: [{ id: "project", label: "\u5F53\u524D\u4E1A\u52A1\u9879\u76EE" }, { id: "global", label: "\u5168\u90E8\u4E1A\u52A1\u9879\u76EE", disabled: !systemAdmin }], onSelect: (v) => {
          setScope(v);
          setOpen("");
        }, anchor: h(Button, { variant: "ghost", size: "sm", disabled: busy || !editable, onClick: () => setOpen("scope"), "aria-label": "\u80FD\u529B\u914D\u7F6E\u8303\u56F4" }, scope === "global" ? "\u5168\u90E8\u4E1A\u52A1\u9879\u76EE" : "\u5F53\u524D\u4E1A\u52A1\u9879\u76EE", h(IconChevronDownOutlineMedium, { size: 16 })) })),
        policy.capabilities.map((cap) => {
          const configured = (scope === "global" ? policy.global : policy.project)[cap.id] || cap.default || "enabled";
          return h(SettingRow, { key: cap.id, title: cap.title, description: `\u5F53\u524D\u6709\u6548\u72B6\u6001\uFF1A${labels[cap.state]}${cap.requires.length ? " \xB7 \u4F9D\u8D56 " + cap.requires.join("\u3001") : ""}` }, h(SettingsMenu, { open: open === cap.id, onClose: () => setOpen(""), selectedId: configured, items: Object.entries(labels).map(([id, label]) => ({ id, label })), onSelect: (state) => change({ [cap.id]: state }), anchor: h(Button, { variant: "outline", size: "sm", disabled: busy || !editable, onClick: () => setOpen(cap.id), "aria-label": cap.title + "\u80FD\u529B\u72B6\u6001" }, labels[configured], h(IconChevronDownOutlineMedium, { size: 16 })) }));
        }),
        message && h("p", { role: failed ? "alert" : "status", "data-error": failed, className: "nw-setting-status" }, message)
      );
    }
    function ProjectSkills({ project, editable }) {
      const [catalog, setCatalog] = React.useState(null), [busy, setBusy] = React.useState(false), [error, setError] = React.useState("");
      async function load() {
        const r = await fetch(publicUrls.route("/natural-data/api/project_skills?") + new URLSearchParams({ business_project: project }));
        const v = await r.json();
        if (!r.ok) throw Error(v.error);
        setCatalog(v);
      }
      React.useEffect(() => {
        load().catch((e) => setError(e.message));
      }, [project]);
      async function toggle(skill, value) {
        setBusy(true);
        setError("");
        try {
          await settingsRequest("policy_update", { project, business_project: project, scope: "project", changes: {}, skills: { [skill.id]: value }, expected_revision: catalog.revision });
          await load();
        } catch (e) {
          setError(e.message);
          try {
            await load();
          } catch {
          }
        } finally {
          setBusy(false);
        }
      }
      return h(
        Detail,
        { title: "\u5DF2\u5B89\u88C5\u7684\u5206\u6790\u6280\u80FD" },
        h("p", { className: "nw-setting-description" }, "\u6280\u80FD\u6765\u81EA\u5DF2\u5B89\u88C5\u80FD\u529B\u63D2\u4EF6\u3002\u901A\u7528\u5206\u6790\u53EF\u590D\u7528\uFF1B\u4E1A\u52A1\u6280\u80FD\u6309\u5F53\u524D\u9879\u76EE\u7684\u6307\u6807\u5408\u7EA6\u5224\u65AD\u9002\u7528\u6027\u3002\u7B97\u6CD5\u4EE3\u7801\u4E0E\u7248\u672C\u7531\u63D2\u4EF6\u7EF4\u62A4\u3002"),
        (catalog?.skills || []).map((skill) => h(SettingRow, { key: skill.id, title: skill.title, description: (skill.category || "\u6269\u5C55\u6280\u80FD") + " \xB7 v" + skill.version + " \xB7 " + skill.requires + (skill.applicable ? "" : " \xB7 \u5F53\u524D\u9879\u76EE\u7F3A\u5C11\u6240\u9700\u6307\u6807\uFF0C\u65E0\u6CD5\u542F\u7528") }, h(Switch, { label: skill.title + "\u6280\u80FD", checked: skill.enabled && skill.applicable, disabled: busy || !editable || !skill.applicable, onChange: (value) => toggle(skill, value) }))),
        error && h("p", { role: "alert", className: "nw-setting-status", "data-error": true }, error)
      );
    }
    function ProjectMetadata({ project, busy, disabled, onSave }) {
      const [name, setName] = React.useState(project.name), [description, setDescription] = React.useState(project.description || "");
      return h(Detail, { title: "\u9879\u76EE\u57FA\u672C\u4FE1\u606F" }, h("label", { className: "nw-field" }, h("span", null, "\u9879\u76EE\u540D\u79F0"), h(Input, { value: name, "aria-label": "\u9879\u76EE\u540D\u79F0", disabled: busy || disabled, onChange: (e) => setName(e.target.value) })), h("label", { className: "nw-field" }, h("span", null, "\u9879\u76EE\u8BF4\u660E"), h(Input, { value: description, "aria-label": "\u9879\u76EE\u8BF4\u660E", disabled: busy || disabled, onChange: (e) => setDescription(e.target.value) })), h(Button, { variant: "outline", size: "sm", disabled: busy || disabled || !name.trim() || name === project.name && description === (project.description || ""), onClick: () => onSave({ name: name.trim(), description }) }, "\u4FDD\u5B58\u9879\u76EE\u4FE1\u606F"), disabled && h("p", { className: "nw-setting-description" }, "\u8BF7\u5148\u4FDD\u5B58\u6216\u653E\u5F03\u4E0A\u65B9\u8349\u7A3F\uFF0C\u518D\u4FEE\u6539\u9879\u76EE\u4FE1\u606F\u3002"));
    }
    function ProjectMembers({ project, busy, disabled, onUpdate }) {
      const [user, setUser] = React.useState(""), [role, setRole] = React.useState("viewer"), [open, setOpen] = React.useState(false);
      const labels = { viewer: "\u53EF\u54A8\u8BE2", editor: "\u53EF\u7F16\u8F91\u4E0E\u53D1\u5E03", admin: "\u9879\u76EE\u7BA1\u7406\u5458" };
      return h(
        React.Fragment,
        null,
        h("p", { className: "nw-setting-description" }, "\u4F7F\u7528\u8D26\u6237\u7CFB\u7EDF\u63D0\u4F9B\u7684\u53EF\u4FE1\u8EAB\u4EFD\u6807\u8BC6\u6388\u6743\u3002\u5F53\u524D\u5BBF\u4E3B\u8BC6\u522B\u672C\u673A\u8D26\u6237\uFF1B\u6DFB\u52A0\u6807\u8BC6\u4E0D\u4F1A\u521B\u5EFA\u767B\u5F55\u8D26\u6237\u3002"),
        Object.entries(project.members || {}).map(([id, r]) => h(SettingRow, { key: id, title: id, description: labels[r] }, h("div", { className: "nw-actions" }, h(Button, { variant: "outline", size: "sm", disabled: busy || disabled, onClick: () => {
          setUser(id);
          setRole(r);
        } }, "\u7F16\u8F91\u89D2\u8272"), h(Button, { variant: "ghost", size: "sm", disabled: busy || disabled || r === "admin" && Object.values(project.members).filter((role2) => role2 === "admin").length === 1, onClick: () => onUpdate({ user: id, role: "remove" }) }, "\u79FB\u9664\u6388\u6743")))),
        h(
          "div",
          { className: "nw-member-form" },
          h("label", { className: "nw-field" }, h("span", null, "\u4EBA\u5458\u8EAB\u4EFD\u6807\u8BC6"), h(Input, { value: user, disabled: busy || disabled, placeholder: "\u4F8B\u5982 local:\u7528\u6237\u540D", onChange: (e) => setUser(e.target.value), "aria-label": "\u4EBA\u5458\u8EAB\u4EFD\u6807\u8BC6" })),
          h("div", { className: "nw-actions" }, h(SettingsMenu, { open, onClose: () => setOpen(false), selectedId: role, items: Object.entries(labels).map(([id, label]) => ({ id, label })), onSelect: (id) => {
            setRole(id);
            setOpen(false);
          }, anchor: h(Button, { variant: "outline", size: "sm", disabled: busy || disabled, onClick: () => setOpen((v) => !v), "aria-label": "\u6210\u5458\u89D2\u8272" }, labels[role], h(IconChevronDownOutlineMedium, { size: 16 })) }), h(Button, { variant: "outline", size: "sm", disabled: busy || disabled || !user.trim(), onClick: () => onUpdate({ user: user.trim(), role }) }, "\u4FDD\u5B58\u4EBA\u5458\u6388\u6743"))
        )
      );
    }
    function DatabaseConnections({ preferred, onChanged, onDirtyChange }) {
      const [catalog, setCatalog] = React.useState(null), [selected, setSelected] = React.useState(preferred || ""), [form, setForm] = React.useState(null), [open, setOpen] = React.useState(""), [busy, setBusy] = React.useState(false), [message, setMessage] = React.useState(""), [failed, setFailed] = React.useState(false), [search, setSearch] = React.useState("");
      React.useEffect(() => {
        if (preferred) {
          setSelected(preferred);
          setForm(null);
          setMessage("");
        }
      }, [preferred]);
      React.useEffect(() => {
        onDirtyChange?.(Boolean(form));
      }, [Boolean(form), onDirtyChange]);
      function adopt(value) {
        setCatalog(value);
        setSelected([value.selected_source, selected].find((id) => value.connections.some((s) => s.id === id)) || "");
      }
      async function load() {
        adopt(await settingsRequest("connections"));
      }
      React.useEffect(() => {
        load().catch((e) => {
          setFailed(true);
          setMessage(e.message);
        });
      }, []);
      const source = catalog?.connections.find((s) => s.id === selected), types = catalog?.types || [];
      function edit(value) {
        if (value) setSelected(value.id);
        setFailed(false);
        setMessage("");
        setForm(value ? { name: value.name, description: value.description || "", dialect: value.dialect, ...value.connection, password: "", clear_password: false, synthetic: Boolean(value.synthetic), confirm_target_change: false } : { isNew: true, name: "", description: "", dialect: "postgresql", host: "", port: 5432, database: "", schema: "public", username: "", password: "", sslmode: "verify-full", ssl_ca: "", path: "", synthetic: false, clear_password: false, confirm_target_change: false });
      }
      function change(key, value) {
        setForm((f) => ({ ...f, [key]: value, confirm_target_change: false }));
        setMessage("");
      }
      const targetChanged = Boolean(form && source && ["dialect", ...form.dialect === "sqlite" ? ["path"] : ["host", "port", "database", "schema"]].some((key) => String(form[key] ?? "") !== String((key === "dialect" ? source.dialect : source.connection[key]) ?? "")));
      async function perform(operation, args, success) {
        setBusy(true);
        setFailed(false);
        setMessage("");
        try {
          const value = await settingsRequest(operation, args);
          if (operation === "connection_update") {
            adopt(value);
            setForm(null);
            if (args.action === "delete") setSelected("");
            await onChanged();
          }
          setMessage(value.message || success);
        } catch (e) {
          setFailed(true);
          setMessage(e.message);
          if (e.status === 409) try {
            await load();
          } catch {
          }
        } finally {
          setBusy(false);
        }
      }
      function payload() {
        const { isNew, password_set, tls_warning, clear_password, confirm_target_change, ...values } = form;
        values.port = Number(values.port);
        values.password_action = !isNew && source?.connection.password_set && !values.password && !clear_password ? "keep" : "replace";
        if (clear_password) values.password = "";
        return { source_id: isNew ? void 0 : source?.id, connection: values, confirm_target_change };
      }
      function field(key, label, options = {}) {
        return h("label", { className: "nw-field" + (options.wide ? " nw-field-wide" : "") }, h("span", null, label), h(Input, { type: options.type || "text", value: form[key] ?? "", "aria-label": label, disabled: busy || options.disabled, autoComplete: key === "password" ? "new-password" : "off", spellCheck: false, placeholder: options.placeholder, onChange: (e) => change(key, e.target.value), ...options.type === "number" ? { min: 1, max: 65535 } : {} }));
      }
      function picker(key, label, items, onSelect) {
        return h("div", { className: "nw-field-wide" }, h(SettingRow, { title: label }, h(SettingsMenu, { open: open === key, onClose: () => setOpen(""), selectedId: form[key], items, onSelect: (id) => {
          onSelect(id);
          setOpen("");
        }, anchor: h(Button, { variant: "outline", size: "sm", disabled: busy, "aria-label": label, onClick: () => setOpen(key) }, items.find((i) => i.id === form[key])?.label || form[key], h(IconChevronDownOutlineMedium, { size: 16 })) })));
      }
      if (!catalog) return h("p", { role: failed ? "alert" : "status", className: "nw-setting-status" }, message || "\u6B63\u5728\u8BFB\u53D6\u6570\u636E\u5E93\u8FDE\u63A5\u2026");
      return h(
        "div",
        { className: "nw-connections" },
        !form && !source && h(
          React.Fragment,
          null,
          h(SettingRow, { title: "\u5DF2\u4FDD\u5B58\u6570\u636E\u5E93\uFF08" + catalog.connections.length + "\uFF09", description: "\u96C6\u4E2D\u7BA1\u7406\u5DF2\u4FDD\u5B58\u7684\u6570\u636E\u5E93\u3002\u70B9\u51FB\u7BA1\u7406\u67E5\u770B\u8FDE\u63A5\u8BE6\u60C5\uFF0C\u7F16\u8F91\u3001\u6D4B\u8BD5\u6216\u5728\u8BE6\u60C5\u5E95\u90E8\u5220\u9664\u3002" }, h(Button, { variant: "outline", size: "sm", disabled: busy, onClick: () => edit(null) }, "\u65B0\u589E\u6570\u636E\u5E93\u8FDE\u63A5")),
          h(Input, { "aria-label": "\u641C\u7D22\u6570\u636E\u5E93\u8FDE\u63A5", placeholder: "\u6309\u540D\u79F0\u641C\u7D22\u5DF2\u4FDD\u5B58\u6570\u636E\u5E93", value: search, onChange: (e) => setSearch(e.target.value) }),
          catalog.connections.filter((s) => !search || s.name.toLowerCase().includes(search.toLowerCase())).map((s) => h(SettingRow, { key: s.id, title: s.name, description: (s.enabled ? "\u5DF2\u542F\u7528" : "\u5DF2\u505C\u7528") + " \xB7 " + s.dialect + " \xB7 " + s.table_count + " \u5F20\u8868" + (s.synthetic ? " \xB7 \u6D4B\u8BD5\u6216\u6A21\u62DF\u6570\u636E" : "") + " \xB7 " + (s.used_by.length ? "\u5173\u8054 " + s.used_by.length + " \u4E2A\u9879\u76EE" : "\u672A\u5173\u8054\u9879\u76EE") }, h("div", { className: "nw-actions" }, h(Button, { variant: "outline", size: "sm", disabled: busy, onClick: () => {
            setSelected(s.id);
            setMessage("");
          } }, "\u7BA1\u7406"), h(Button, { variant: "ghost", size: "sm", disabled: busy, onClick: () => edit(s) }, "\u7F16\u8F91")))),
          !catalog.connections.length && h("p", { className: "nw-setting-description" }, "\u8FD8\u6CA1\u6709\u4FDD\u5B58\u7684\u6570\u636E\u5E93\uFF0C\u8BF7\u5148\u65B0\u589E\u6570\u636E\u5E93\u8FDE\u63A5\u3002"),
          catalog.connections.length > 0 && !catalog.connections.some((s) => !search || s.name.toLowerCase().includes(search.toLowerCase())) && h("p", { className: "nw-setting-description" }, "\u6CA1\u6709\u5339\u914D\u7684\u6570\u636E\u5E93\u3002")
        ),
        source && !form && h(SettingRow, { title: source.name, description: (source.enabled ? "\u5DF2\u542F\u7528" : "\u5DF2\u505C\u7528") + " \xB7 " + source.dialect + (source.synthetic ? " \xB7 \u6D4B\u8BD5\u6216\u6A21\u62DF\u6570\u636E" : "") }, h(Button, { variant: "ghost", size: "sm", disabled: busy, onClick: () => {
          setSelected("");
          setMessage("");
        } }, "\u8FD4\u56DE\u5DF2\u4FDD\u5B58\u6570\u636E\u5E93")),
        source && !form && h(
          React.Fragment,
          null,
          h(SettingRow, { title: source.dialect === "sqlite" ? "\u6570\u636E\u5E93\u6587\u4EF6" : "\u8FDE\u63A5\u5730\u5740", description: source.dialect === "sqlite" ? source.connection.path : `${source.connection.host || "\u672A\u914D\u7F6E"}:${source.connection.port || ""} / ${source.connection.database || ""} \xB7 ${source.connection.username || "\u672A\u914D\u7F6E\u8D26\u53F7"}` }, h(Button, { variant: "outline", size: "sm", disabled: busy, onClick: () => edit(source) }, "\u7F16\u8F91\u8FDE\u63A5")),
          h(SettingRow, { title: "\u5173\u8054\u4E1A\u52A1\u9879\u76EE", description: source.used_by.length ? source.used_by.join("\u3001") + "\u3002\u5220\u9664\u65F6\u53EF\u4E00\u5E76\u79FB\u9664\u9879\u76EE\u5F15\u7528\uFF0C\u64CD\u4F5C\u5C06\u5728\u786E\u8BA4\u540E\u7ACB\u5373\u751F\u6548\u3002" : "\u5C1A\u672A\u88AB\u4E1A\u52A1\u9879\u76EE\u63A5\u5165\u3002" }),
          h(SettingRow, { title: "\u8FDE\u63A5\u72B6\u6001", description: "\u505C\u7528\u540E\u505C\u6B62\u65B0\u67E5\u8BE2\u5E76\u53D6\u6D88\u5728\u9014\u67E5\u8BE2\uFF1B\u4FDD\u7559\u9879\u76EE\u914D\u7F6E\u548C\u5386\u53F2\u7ED3\u679C\u3002" }, h(Switch, { label: "\u542F\u7528\u6570\u636E\u5E93\u8FDE\u63A5", checked: source.enabled, disabled: busy, onChange: (enabled) => perform("connection_update", { action: "state", source_id: source.id, enabled, expected_revision: catalog.revision }, enabled ? "\u8FDE\u63A5\u5DF2\u542F\u7528\u3002" : "\u8FDE\u63A5\u5DF2\u505C\u7528\u3002") })),
          source.schema_changes && Object.values(source.schema_changes).some((v) => v.length) && h(SettingRow, { title: "\u6700\u8FD1\u7ED3\u6784\u53D8\u5316", description: "\u65B0\u589E\u8868 " + (source.schema_changes.added_tables || []).length + " \xB7 \u79FB\u9664\u8868 " + (source.schema_changes.removed_tables || []).length + " \xB7 \u5B57\u6BB5\u53D8\u5316\u8868 " + (source.schema_changes.changed_tables || []).length + "\u3002\u8BF7\u6838\u5BF9\u9879\u76EE\u6388\u6743\u548C\u6307\u6807\u53E3\u5F84\u3002" }),
          h(SettingRow, { title: "\u8FDE\u63A5\u68C0\u67E5\u4E0E\u8868\u7ED3\u6784", description: `\u5DF2\u767B\u8BB0 ${source.table_count} \u5F20\u8868\u6216\u89C6\u56FE${source.last_checked ? " \xB7 \u6700\u8FD1\u68C0\u67E5 " + new Date(source.last_checked * 1e3).toLocaleString() : ""}\u3002\u5237\u65B0\u7ED3\u6784\u4E0D\u4F1A\u81EA\u52A8\u589E\u52A0\u9879\u76EE\u6388\u6743\u8868\u3002` }, h("div", { className: "nw-actions" }, h(Button, { variant: "outline", size: "sm", disabled: busy, onClick: () => perform("connection_test", { source_id: source.id }, "\u8FDE\u63A5\u6210\u529F\u3002") }, "\u6D4B\u8BD5\u8FDE\u63A5"), h(Button, { variant: "outline", size: "sm", disabled: busy, onClick: () => perform("connection_update", { action: "refresh", source_id: source.id, expected_revision: catalog.revision }, "\u8868\u7ED3\u6784\u5DF2\u66F4\u65B0\uFF0C\u8BF7\u68C0\u67E5\u9879\u76EE\u7684\u6570\u636E\u8303\u56F4\u3002") }, "\u5237\u65B0\u8868\u7ED3\u6784"))),
          h(Detail, { title: "\u67E5\u770B\u8868\u4E0E\u5B57\u6BB5\uFF08" + source.table_count + "\uFF09" }, source.tables.map((t) => h("div", { key: t.name }, h("div", { className: "nw-setting-title" }, t.label ? t.label + " \xB7 " + t.name : t.name), h("div", { className: "nw-setting-description" }, t.columns.map((c) => c.name + " " + c.type).join(" \xB7 "))))),
          h("p", { className: "nw-setting-description" }, source.managed ? "\u6B64\u8FDE\u63A5\u7531\u81EA\u7136 Work \u72EC\u7ACB\u7EF4\u62A4\uFF0C\u540C\u6B65\u539F\u7CFB\u7EDF\u4E0D\u4F1A\u8986\u76D6\u672C\u5730\u4FEE\u6539\u3002" : "\u6B64\u8FDE\u63A5\u6765\u81EA\u5DF2\u6709\u914D\u7F6E\u3002\u9996\u6B21\u4FDD\u5B58\u540E\u8F6C\u4E3A\u81EA\u7136 Work \u72EC\u7ACB\u7EF4\u62A4\u3002")
        ),
        form && h(
          "form",
          { className: "nw-connection-form", onSubmit: (e) => {
            e.preventDefault();
            perform("connection_update", { action: "save", ...payload(), expected_revision: catalog.revision }, "\u6570\u636E\u5E93\u8FDE\u63A5\u5DF2\u4FDD\u5B58\uFF0C\u8868\u7ED3\u6784\u5DF2\u8BFB\u53D6\uFF1B\u8BF7\u5728\u4E1A\u52A1\u9879\u76EE\u4E2D\u914D\u7F6E\u6388\u6743\u8303\u56F4\u3002");
          } },
          h(SettingRow, { title: form.isNew ? "\u65B0\u589E\u6570\u636E\u5E93\u8FDE\u63A5" : "\u7F16\u8F91\u6570\u636E\u5E93\u8FDE\u63A5", description: "\u5207\u6362\u9876\u90E8\u5206\u7C7B\u4F1A\u4FDD\u7559\u672C\u6B21\u672A\u4FDD\u5B58\u7684\u8F93\u5165\u3002" }, h(Button, { type: "button", variant: "ghost", size: "sm", disabled: busy, onClick: () => {
            setForm(null);
            setMessage("");
          } }, "\u53D6\u6D88\u7F16\u8F91")),
          h(
            "div",
            { className: "nw-connection-fields" },
            field("name", "\u8FDE\u63A5\u540D\u79F0", { wide: true }),
            picker("dialect", "\u6570\u636E\u5E93\u7C7B\u578B", types.map((t) => ({ id: t.id, label: t.label + (t.available ? "" : "\uFF08\u9A71\u52A8\u672A\u5B89\u88C5\uFF09"), disabled: !t.available })), (id) => setForm((f) => ({ ...f, dialect: id, port: types.find((t) => t.id === id)?.port || 5432, schema: id === "mysql" ? f.database : "public", password: "", confirm_target_change: false }))),
            form.dialect === "sqlite" ? field("path", "SQLite \u6587\u4EF6\u8DEF\u5F84", { wide: true, placeholder: "\u5DF2\u6709\u6570\u636E\u5E93\u7684\u7EDD\u5BF9\u8DEF\u5F84" }) : h(
              React.Fragment,
              null,
              field("host", "\u670D\u52A1\u5668\u5730\u5740"),
              field("port", "\u7AEF\u53E3", { type: "number" }),
              field("database", form.dialect === "oracle" ? "Oracle\u670D\u52A1\u540D" : form.dialect === "dameng" ? "\u8FBE\u68A6\u5B9E\u4F8B\u8BF4\u660E" : "\u6570\u636E\u5E93\u540D\u79F0"),
              field("schema", "Schema", { placeholder: form.dialect === "mysql" ? "\u4E0E\u6570\u636E\u5E93\u540D\u79F0\u4E00\u81F4" : "public" }),
              field("username", "\u7528\u6237\u540D"),
              field("password", "\u5BC6\u7801", { type: "password", disabled: form.clear_password, placeholder: !form.isNew && source?.connection.password_set ? "\u7559\u7A7A\u4FDD\u7559\u5DF2\u4FDD\u5B58\u5BC6\u7801" : "\u8F93\u5165\u6570\u636E\u5E93\u5BC6\u7801" }),
              !form.isNew && source?.connection.password_set && h(Checkbox, { label: "\u6E05\u7A7A\u5DF2\u4FDD\u5B58\u5BC6\u7801", checked: form.clear_password, disabled: busy, onChange: (value) => change("clear_password", value) }),
              ["postgresql", "postgis", "tbase", "kingbase", "mysql"].includes(form.dialect) ? h(React.Fragment, null, picker("sslmode", "SSL \u6A21\u5F0F", [["verify-full", "\u6821\u9A8C\u8BC1\u4E66\u548C\u4E3B\u673A\u540D\uFF08\u63A8\u8350\uFF09"], ["verify-ca", "\u4EC5\u6821\u9A8C CA"], ["require", "\u4EC5\u52A0\u5BC6\uFF0C\u4E0D\u6821\u9A8C\u8BC1\u4E66"], ["prefer", "\u5141\u8BB8\u56DE\u9000\u660E\u6587"], ["disable", "\u4E0D\u4F7F\u7528 SSL"]].filter(([id]) => form.dialect !== "mysql" || id !== "prefer").map(([id, label]) => ({ id, label })), (id) => change("sslmode", id)), field("ssl_ca", "SSL CA \u8BC1\u4E66\u8DEF\u5F84\uFF08\u53EF\u9009\uFF09", { wide: true, placeholder: "MySQL \u7559\u7A7A\u4F7F\u7528\u7CFB\u7EDF CA\uFF1BPostgreSQL \u4F7F\u7528\u9A71\u52A8\u9ED8\u8BA4 CA" }), form.sslmode !== "verify-full" && h("p", { role: "alert", className: "nw-setting-description" }, "\u5F53\u524D\u6A21\u5F0F\u672A\u540C\u65F6\u5F3A\u5236\u52A0\u5BC6\u3001\u6821\u9A8C\u8BC1\u4E66\u548C\u4E3B\u673A\u540D\uFF1B\u4EC5\u5728\u5DF2\u6838\u5B9E\u7684\u53D7\u63A7\u73AF\u5883\u4E2D\u4F7F\u7528\u3002")) : h("p", { className: "nw-setting-description" }, "\u5382\u5546\u539F\u751F\u9A71\u52A8\u7684 TLS/\u94B1\u5305\u4E0E\u672C\u673A\u8FD0\u884C\u5E93\u9700\u6309\u90E8\u7F72\u8BF4\u660E\u51C6\u5907\u5E76\u5355\u72EC\u9A8C\u8BC1\u3002")
            ),
            field("description", "\u8FDE\u63A5\u8BF4\u660E", { wide: true })
          ),
          h(Checkbox, { label: "\u6B64\u8FDE\u63A5\u4E3A\u6D4B\u8BD5\u6216\u6A21\u62DF\u6570\u636E", checked: form.synthetic, disabled: busy, onChange: (value) => change("synthetic", value) }),
          !form.isNew && targetChanged && source?.used_by.length > 0 && h(Checkbox, { label: "\u786E\u8BA4\u5C06\u5173\u8054\u4E1A\u52A1\u9879\u76EE\u5207\u6362\u5230\u65B0\u6570\u636E\u5E93\u76EE\u6807\uFF0C\u5E76\u91CD\u65B0\u6838\u5BF9\u6388\u6743\u548C\u53E3\u5F84", checked: form.confirm_target_change, disabled: busy, onChange: (value) => setForm((f) => ({ ...f, confirm_target_change: value })) }),
          h("p", { className: "nw-setting-description" }, "\u4FDD\u5B58\u8FDE\u63A5\u53C2\u6570\u65F6\u6D4B\u8BD5\u5E76\u8BFB\u53D6\u8868\u7ED3\u6784\uFF0C\u4E0D\u5199\u5165\u6E90\u6570\u636E\u5E93\u3002\u4EC5\u4FEE\u6539\u540D\u79F0\u4E0E\u8BF4\u660E\u53EF\u79BB\u7EBF\u4FDD\u5B58\uFF0C\u5176\u4ED6\u5B57\u6BB5\u4E0D\u968F\u6B64\u64CD\u4F5C\u4FDD\u5B58\u3002\u5BC6\u7801\u7559\u7A7A\u4FDD\u7559\u539F\u5BC6\u7801\u3002"),
          h("div", { className: "nw-actions" }, !form.isNew && h(Button, { type: "button", variant: "outline", size: "sm", disabled: busy || !form.name.trim(), onClick: () => perform("connection_update", { action: "metadata", source_id: source.id, name: form.name, description: form.description, expected_revision: catalog.revision }, "\u540D\u79F0\u4E0E\u8BF4\u660E\u5DF2\u4FDD\u5B58\uFF0C\u8FDE\u63A5\u53C2\u6570\u672A\u66F4\u6539\u3002") }, "\u4EC5\u4FDD\u5B58\u540D\u79F0\u4E0E\u8BF4\u660E"), h(Button, { type: "button", variant: "outline", size: "sm", disabled: busy, onClick: () => perform("connection_test", payload(), "\u8FDE\u63A5\u6210\u529F\u3002") }, "\u6D4B\u8BD5\u8FDE\u63A5"), h(Button, { type: "submit", variant: "outline", size: "sm", disabled: busy || !form.isNew && targetChanged && source?.used_by.length > 0 && !form.confirm_target_change }, busy ? "\u6B63\u5728\u5904\u7406\u2026" : "\u4FDD\u5B58\u5E76\u8BFB\u53D6\u8868\u7ED3\u6784"), h(Button, { type: "button", variant: "ghost", size: "sm", disabled: busy, onClick: () => {
            setForm(null);
            setMessage("");
          } }, "\u53D6\u6D88\u7F16\u8F91"))
        ),
        message && h("p", { role: failed ? "alert" : "status", "data-error": failed, className: "nw-setting-status" }, message),
        !form && h(Button, { variant: "ghost", size: "sm", disabled: busy, onClick: () => load().catch((e) => {
          setFailed(true);
          setMessage(e.message);
        }) }, "\u91CD\u65B0\u8BFB\u53D6\u8FDE\u63A5\u5217\u8868"),
        source && !form && h(DeleteAction, { key: source.id + ":" + catalog.revision + ":" + catalog.business_revision, name: "\u8FDE\u63A5", disabled: busy, affected: source.used_by, description: "\u5220\u9664\u201C" + source.name + "\u201D\u7684\u8FDE\u63A5\u914D\u7F6E\u53CA\u51ED\u636E\uFF0C\u4E0D\u5220\u9664\u6E90\u6570\u636E\u5E93\u6587\u4EF6\u6216\u8868\u3002\u4FDD\u7559\u5386\u53F2\u8BB0\u5F55\uFF0C\u7ED3\u679C\u8BBF\u95EE\u4ECD\u53D7\u9879\u76EE\u6388\u6743\u7EA6\u675F\uFF1B\u5220\u9664\u540E\u9700\u91CD\u65B0\u6DFB\u52A0\u8FDE\u63A5\u3002", onDelete: (detach) => perform("connection_update", { action: "delete", source_id: source.id, confirm_delete: true, confirm_detach: detach, expected_business_revision: catalog.business_revision, expected_revision: catalog.revision }, "\u6570\u636E\u5E93\u8FDE\u63A5\u5DF2\u5220\u9664\uFF0C\u6E90\u6570\u636E\u5E93\u672A\u5220\u9664\uFF0C\u76F8\u5173\u67E5\u8BE2\u6388\u6743\u5DF2\u64A4\u9500\u3002") })
      );
    }
    const { DictionaryEditor } = createAdministration({ React, h, Input, Button, SettingsMenu, SettingRow, Detail, DataSettings, request: settingsRequest, IconChevronDownOutlineMedium });
    const { Console } = createConsole({ React, h, UI, openSession: (id) => {
      if (!openNativeSession) throw Error("\u539F\u751F\u4F1A\u8BDD\u5165\u53E3\u5C1A\u672A\u5C31\u7EEA");
      openNativeSession(id);
    }, request: settingsRequest, getJson: async (op, params) => {
      const r = await fetch(publicUrls.route("/natural-data/api/" + op + "?") + new URLSearchParams(params));
      const v = await r.json();
      if (!r.ok) throw Error(v.error || "\u8BFB\u53D6\u5931\u8D25");
      return v;
    }, SettingsMenu, MetricBuilder, PackImport, TemplateTransfer, CapabilitySettings, DatabaseConnections, OnboardingPanel });
    function DataSettings({ management = false, initialView = "projects", onDirtyChange } = {}) {
      const [cfg, setCfg] = React.useState(null), [message, setMessage] = React.useState(""), [failed, setFailed] = React.useState(false), [busy, setBusy] = React.useState(false), [project, setProject] = React.useState(""), [menu, setMenu] = React.useState(false), [section, setSection] = React.useState("sources"), [draft, setDraft] = React.useState(null), [dirty, setDirty] = React.useState(false), [name, setName] = React.useState("");
      const sectionId = React.useId(), viewId = React.useId();
      const [connectionId, setConnectionId] = React.useState("");
      const [view, setView] = React.useState(initialView), [connectionDirty, setConnectionDirty] = React.useState(false);
      React.useEffect(() => {
        onDirtyChange?.(dirty || connectionDirty);
      }, [dirty, connectionDirty, onDirtyChange]);
      function adopt(v, selected) {
        setCfg(v);
        const id = selected || project || v.default_project || v.projects[0]?.id || "";
        const p = v.projects.find((p2) => p2.id === id) || v.projects[0];
        setProject(p?.id || "");
        setDraft(p ? structuredClone(p.draft || p.versions.find((v2) => v2.version === p.published_version)?.config) : null);
        setDirty(false);
      }
      React.useEffect(() => {
        let active = true;
        settingsRequest("config").then((v) => {
          if (active) adopt(v, v.default_project);
        }).catch((e) => {
          if (active && e.name !== "AbortError") {
            setFailed(true);
            setMessage(e.message);
          }
        });
        return () => {
          active = false;
        };
      }, []);
      function edit(fn) {
        setDraft((d) => typeof fn === "function" ? fn(d) : fn);
        setDirty(true);
        setMessage("");
      }
      const packMessage = (v) => {
        const r = v?.pack_report || {}, i = r.imported || {}, sk = r.skipped || [];
        return "\u5DF2\u5BFC\u5165\u8349\u7A3F\uFF1A\u5B57\u6BB5\u4E0E\u53D6\u503C " + (i.mappings || 0) + " \u6761\u3001\u53E3\u5F84\u8BF4\u660E " + (i.terms || 0) + " \u6761\u3001\u7ED3\u6784\u5316\u6307\u6807 " + (i.metrics || 0) + " \u4E2A\u3001\u67E5\u8BE2\u793A\u4F8B " + (i.examples || 0) + " \u4E2A" + (sk.length ? "\uFF1B\u8DF3\u8FC7 " + sk.length + " \u9879\uFF08" + sk.slice(0, 3).map((x) => x.name + "\uFF1A" + x.reason).join("\uFF1B") + (sk.length > 3 ? "\u2026" : "") + "\uFF09" : "") + ((r.kept || []).length ? "\uFF1B\u4FDD\u7559\u5DF2\u6539\u6761\u76EE " + r.kept.length + " \u9879" : "") + "\u3002\u8BF7\u6838\u5BF9\u540E\u6821\u9A8C\u53D1\u5E03\u3002";
      };
      async function action(fn, success) {
        setBusy(true);
        setFailed(false);
        setMessage("");
        try {
          const value = await fn();
          adopt(await settingsRequest("config"), value?.selected_project);
          setMessage(typeof success === "function" ? success(value) : success);
        } catch (e) {
          setFailed(true);
          setMessage(e.message);
        } finally {
          setBusy(false);
        }
      }
      function update(kind, args = {}, success = "\u9879\u76EE\u914D\u7F6E\u5DF2\u4FDD\u5B58\u3002") {
        return action(() => settingsRequest("business_update", { action: kind, business_project: project, expected_revision: cfg.revision, ...args }), success);
      }
      if (!cfg && failed && /^数据引擎未启动/.test(message)) return h("div", null, h(SettingRow, { title: "\u8FD0\u884C\u73AF\u5883\u68C0\u67E5\uFF1A\u6570\u636E\u5F15\u64CE\u672A\u542F\u52A8", description: message }), h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: () => action(() => settingsRequest("config"), "\u914D\u7F6E\u5DF2\u8BFB\u53D6\u3002") }, "\u91CD\u65B0\u68C0\u67E5"));
      if (!cfg) return h("div", null, h("p", { role: failed ? "alert" : "status", className: "nw-setting-status" }, message || "\u6B63\u5728\u8BFB\u53D6\u914D\u7F6E\u2026"), failed && h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: () => action(() => settingsRequest("config"), "\u914D\u7F6E\u5DF2\u8BFB\u53D6\u3002") }, "\u91CD\u65B0\u8BFB\u53D6\u914D\u7F6E"));
      const current = cfg.projects.find((p) => p.id === project), editable = ["admin", "editor"].includes(current?.role), admin = current?.role === "admin";
      function choose(id) {
        if (dirty) {
          setFailed(true);
          setMessage("\u8BF7\u5148\u4FDD\u5B58\u6216\u653E\u5F03\u4FEE\u6539\uFF0C\u518D\u5207\u6362\u9879\u76EE\u3002");
          return;
        }
        const p = cfg.projects.find((p2) => p2.id === id);
        setProject(id);
        setDraft(structuredClone(p.draft || p.versions.find((v) => v.version === p.published_version)?.config));
        setDirty(false);
        setMenu(false);
        setMessage("");
      }
      return h(
        "div",
        { className: "nw-settings" },
        !management && openAdministration && h(SettingRow, { title: "\u914D\u7F6E\u7BA1\u7406\u540E\u53F0", description: "\u5728\u5B8C\u6574\u7BA1\u7406\u9875\u9762\u7EF4\u62A4\u6570\u636E\u5E93\u548C\u4E1A\u52A1\u53E3\u5F84\u3002" }, h(Button, { variant: "outline", size: "sm", disabled: busy || dirty, onClick: () => openAdministration?.() }, "\u6253\u5F00\u914D\u7F6E\u7BA1\u7406\u540E\u53F0")),
        !management && cfg.system_admin && h("div", { className: "nw-settings-tabs" }, h(SegmentedControl, { id: viewId, value: view, label: "\u6570\u636E\u6E90\u4E0E\u95EE\u6570\u7BA1\u7406", options: [{ value: "projects", label: "\u4E1A\u52A1\u9879\u76EE" }, { value: "connections", label: "\u6570\u636E\u5E93" }], onChange: setView })),
        cfg.system_admin && h("div", { hidden: view !== "connections", role: "tabpanel", id: viewId + "-connections-panel", "aria-labelledby": management ? void 0 : viewId + "-connections" }, h(DatabaseConnections, { preferred: connectionId, onDirtyChange: setConnectionDirty, onChanged: async () => {
          const value = await settingsRequest("config");
          if (dirty) {
            setFailed(true);
            setMessage("\u6570\u636E\u5E93\u914D\u7F6E\u5DF2\u66F4\u65B0\uFF1B\u9879\u76EE\u672A\u4FDD\u5B58\u8349\u7A3F\u4ECD\u4FDD\u7559\uFF0C\u8BF7\u91CD\u65B0\u6838\u5BF9\u6570\u636E\u8303\u56F4\u3002");
          } else adopt(value);
        } })),
        view === "projects" && h(OnboardingPanel, { cfg, current, busy, dirty, onNavigate: (target) => {
          if (target === "connections" && cfg.system_admin) setView("connections");
          else {
            setView("projects");
            setSection(target === "connections" ? "sources" : target);
          }
        } }),
        h(
          "div",
          { hidden: view !== "projects", role: "tabpanel", id: viewId + "-projects-panel", "aria-labelledby": cfg.system_admin ? viewId + "-projects" : void 0 },
          h(SettingRow, { title: "\u4E1A\u52A1\u9879\u76EE", description: "\u914D\u7F6E\u53EF\u67E5\u8BE2\u7684\u6570\u636E\u5E93\u3001\u8868\u548C\u4E1A\u52A1\u53E3\u5F84\u3002\u65B0\u5BF9\u8BDD\u4F7F\u7528\u5DE5\u4F5C\u533A\u5DF2\u5173\u8054\u7684\u9879\u76EE\uFF0C\u672A\u5173\u8054\u65F6\u4F7F\u7528\u9ED8\u8BA4\u9879\u76EE\u3002" }, h(SettingsMenu, { open: menu, onClose: () => setMenu(false), selectedId: project, items: cfg.projects.map((p) => ({ id: p.id, label: p.name })), onSelect: choose, anchor: h(Button, { variant: "ghost", size: "sm", disabled: busy || dirty || !cfg.projects.length, "aria-label": "\u95EE\u6570\u4E1A\u52A1\u9879\u76EE", "aria-haspopup": "menu", "aria-expanded": menu, onClick: () => setMenu((v) => !v) }, current?.name || "\u6682\u65E0\u6388\u6743\u9879\u76EE", h(IconChevronDownOutlineMedium, { size: 16 })) })),
          message && h("p", { role: failed ? "alert" : "status", "data-error": failed, className: "nw-setting-status" }, message),
          current && draft && h(
            React.Fragment,
            null,
            h(SettingRow, { title: current.published_version ? "\u751F\u6548\u7248\u672C v" + current.published_version : "\u5C1A\u672A\u53D1\u5E03", description: (dirty ? "\u6709\u672A\u4FDD\u5B58\u7684\u8349\u7A3F\uFF0C\u8BF7\u5148\u4FDD\u5B58\u540E\u53D1\u5E03\u6216\u5207\u6362\u9879\u76EE\u3002" : editable ? "\u5C55\u5F00\u4E1A\u52A1\u53E3\u5F84\u53EF\u76F4\u63A5\u7F16\u8F91\uFF1B\u4FEE\u6539\u540E\u4FDD\u5B58\u8349\u7A3F\uFF0C\u518D\u6821\u9A8C\u53D1\u5E03\u3002" : "\u4F60\u53EF\u4EE5\u54A8\u8BE2\u548C\u67E5\u770B\u5DF2\u53D1\u5E03\u914D\u7F6E\u3002") + " \u5F53\u524D\u8EAB\u4EFD\uFF1A" + cfg.principal }, h(
              "div",
              { className: "nw-actions" },
              h(Button, { variant: "outline", size: "sm", disabled: busy || dirty || !current.published_version || cfg.default_project === project, onClick: () => update("default", {}, "\u5DF2\u8BBE\u4E3A\u65B0\u4F1A\u8BDD\u7684\u9ED8\u8BA4\u4E1A\u52A1\u9879\u76EE\u3002") }, cfg.default_project === project ? "\u65B0\u4F1A\u8BDD\u9ED8\u8BA4\u9879\u76EE" : "\u8BBE\u4E3A\u9ED8\u8BA4\u9879\u76EE"),
              editable && h(Button, { variant: "outline", size: "sm", disabled: busy || !dirty, onClick: () => update("draft", { draft }, "\u8349\u7A3F\u5DF2\u4FDD\u5B58\uFF0C\u751F\u6548\u7248\u672C\u672A\u6539\u53D8\u3002") }, "\u4FDD\u5B58\u8349\u7A3F"),
              editable && h(Button, { variant: "outline", size: "sm", disabled: busy, onClick: async () => {
                setBusy(true);
                setFailed(false);
                try {
                  const v = await settingsRequest("admin_check", { business_project: project, expected_revision: cfg.revision, draft });
                  setFailed(!v.valid);
                  setMessage(v.valid ? v.message : v.errors.map((x) => x.message).join("\uFF1B"));
                } catch (e) {
                  setFailed(true);
                  setMessage(e.message);
                } finally {
                  setBusy(false);
                }
              } }, "\u6821\u9A8C\u5F53\u524D\u914D\u7F6E"),
              editable && h(Button, { variant: "outline", size: "sm", disabled: busy || dirty, onClick: () => update("publish", {}, "\u9879\u76EE\u5DF2\u6821\u9A8C\u5E76\u53D1\u5E03\u3002") }, "\u6821\u9A8C\u5E76\u53D1\u5E03"),
              dirty && h(Button, { variant: "ghost", size: "sm", disabled: busy, onClick: () => action(() => settingsRequest("config"), "\u5DF2\u653E\u5F03\u4FEE\u6539\u5E76\u91CD\u65B0\u8BFB\u53D6\u9879\u76EE\u914D\u7F6E\u3002") }, "\u653E\u5F03\u4FEE\u6539")
            )),
            h("div", { className: "nw-settings-tabs" }, h(SegmentedControl, { id: sectionId, value: section, label: "\u95EE\u6570\u8BBE\u7F6E\u5206\u7C7B", options: [{ value: "sources", label: "\u6570\u636E\u8303\u56F4" }, { value: "mappings", label: "\u4E1A\u52A1\u6620\u5C04" }, { value: "business", label: "\u4E1A\u52A1\u53E3\u5F84" }, { value: "capabilities", label: "\u80FD\u529B" }, ...admin ? [{ value: "members", label: "\u6210\u5458" }] : [], { value: "versions", label: "\u53D1\u5E03\u8BB0\u5F55" }], onChange: setSection })),
            h(
              "div",
              { className: "nw-settings-panel", role: "tabpanel", id: sectionId + "-" + section + "-panel", "aria-labelledby": sectionId + "-" + section },
              section === "sources" ? h(ProjectSources, { sources: cfg.sources, draft, setDraft: edit, editable: admin, busy, onManage: cfg.system_admin ? (sid) => {
                setConnectionId(sid);
                setView("connections");
              } : void 0, onTest: (sid) => dirty ? (setFailed(true), setMessage("\u8BF7\u5148\u4FDD\u5B58\u8349\u7A3F\u3002")) : action(() => settingsRequest("test", { business_project: project, source_id: sid }), (value) => value.message || "\u8FDE\u63A5\u6D4B\u8BD5\u5B8C\u6210\u3002") }) : section === "mappings" ? h(DictionaryEditor, { draft, setDraft: edit, sources: cfg.sources, editable, busy }) : section === "business" ? h(React.Fragment, null, h(BusinessEditor, { project, draft, setDraft: edit, editable, busy, sources: cfg.sources }), h(ProjectSkills, { key: project, project, editable: admin }), editable && h(PackImport, { key: project + ":packs", project: current, busy, dirty, onImport: (pack, source_id) => update("import_pack", { pack, source_id }, packMessage) }), editable && h(TemplateTransfer, { key: project + ":template", project: current, sources: cfg.sources, busy, dirty, onImport: (template, source_bindings) => update("import_template", { template, source_bindings }, "\u6A21\u677F\u5DF2\u6821\u9A8C\u5E76\u5BFC\u5165\u8349\u7A3F\uFF0C\u8BF7\u6838\u5BF9\u540E\u6821\u9A8C\u53D1\u5E03\u3002") })) : section === "capabilities" ? h(CapabilitySettings, { key: project, project, editable: admin, systemAdmin: cfg.system_admin }) : section === "members" && admin ? h(ProjectMembers, { project: current, busy, disabled: dirty, onUpdate: (args) => update("member", args, "\u4EBA\u5458\u6388\u6743\u5DF2\u4FDD\u5B58\u3002") }) : section === "versions" ? h(React.Fragment, null, h("p", { className: "nw-setting-description" }, "\u53EA\u4FDD\u7559\u6700\u8FD1 5 \u4E2A\u53D1\u5E03\u7248\u672C\uFF0C\u66F4\u65E9\u7684\u4F1A\u81EA\u52A8\u6E05\u7406\u3002\u56DE\u6EDA\u4F1A\u521B\u5EFA\u4E00\u4E2A\u65B0\u7248\u672C\uFF0C\u5DF2\u6709\u7ED3\u679C\u4FDD\u7559\u539F\u59CB\u7248\u672C\u8BB0\u5F55\u3002"), [...current.versions].reverse().map((v) => h(SettingRow, { key: v.version, title: "v" + v.version + (v.version === current.published_version ? " \xB7 \u5F53\u524D\u751F\u6548" : ""), description: v.note + " \xB7 " + new Date(v.at * 1e3).toLocaleString() }, editable && h(Button, { variant: "outline", size: "sm", title: !admin && ["source_ids", "tables"].some((field) => JSON.stringify(v.config[field]) !== JSON.stringify(current.draft[field])) ? "\u8BE5\u7248\u672C\u6D89\u53CA\u6570\u636E\u8303\u56F4\u53D8\u66F4\uFF0C\u9700\u8981\u9879\u76EE\u7BA1\u7406\u5458\u6062\u590D\u3002" : void 0, disabled: busy || dirty || v.version === current.published_version || !admin && ["source_ids", "tables"].some((field) => JSON.stringify(v.config[field]) !== JSON.stringify(current.draft[field])), onClick: () => update("rollback", { version: v.version }, "\u5DF2\u6062\u590D\u5386\u53F2\u914D\u7F6E\u5E76\u53D1\u5E03\u65B0\u7248\u672C\u3002") }, "\u6062\u590D\u6B64\u7248\u672C")))) : null
            )
          ),
          admin && h(ProjectMetadata, { key: project + ":" + current.name, project: current, busy, disabled: dirty, onSave: (args) => update("metadata", args, "\u9879\u76EE\u4FE1\u606F\u5DF2\u4FDD\u5B58\u3002") }),
          h(Button, { variant: "ghost", size: "sm", disabled: busy || dirty, onClick: () => action(() => settingsRequest("config"), "\u9879\u76EE\u914D\u7F6E\u5DF2\u91CD\u65B0\u8BFB\u53D6\u3002") }, "\u91CD\u65B0\u8BFB\u53D6\u9879\u76EE\u914D\u7F6E"),
          (cfg.archived_projects || []).length > 0 && h(Detail, { title: "\u5DF2\u5220\u9664\u9879\u76EE\uFF08" + cfg.archived_projects.length + "\uFF09" }, cfg.archived_projects.map((p) => h(SettingRow, { key: p.id, title: p.name, description: "\u6062\u590D\u540E\u4E3A\u672A\u53D1\u5E03\u8349\u7A3F\uFF0C\u4E0D\u81EA\u52A8\u6062\u590D\u9ED8\u8BA4\u9879\u76EE\u6216\u67E5\u8BE2\u6743\u9650\u3002" }, h(Button, { variant: "outline", size: "sm", disabled: busy || dirty, onClick: () => update("restore", { business_project: p.id }, "\u9879\u76EE\u5DF2\u6062\u590D\u4E3A\u8349\u7A3F\uFF0C\u8BF7\u6838\u5BF9\u6570\u636E\u8303\u56F4\u5E76\u53D1\u5E03\u3002") }, "\u6062\u590D\u4E3A\u8349\u7A3F")))),
          cfg.system_admin && h(
            Detail,
            { title: "\u9879\u76EE\u4E0E\u6765\u6E90\u7BA1\u7406", initialOpen: !cfg.projects.length },
            h("label", { className: "nw-field" }, h("span", null, "\u65B0\u4E1A\u52A1\u9879\u76EE\u540D\u79F0"), h(Input, { value: name, disabled: busy || dirty, "aria-label": "\u65B0\u4E1A\u52A1\u9879\u76EE\u540D\u79F0", onChange: (e) => setName(e.target.value) })),
            h(Button, { variant: "outline", size: "sm", disabled: busy || dirty || !name.trim(), onClick: () => update("create", { name: name.trim() }, "\u4E1A\u52A1\u9879\u76EE\u5DF2\u521B\u5EFA\uFF0C\u8BF7\u914D\u7F6E\u6570\u636E\u8303\u56F4\u5E76\u53D1\u5E03\u3002") }, "\u521B\u5EFA\u4E1A\u52A1\u9879\u76EE"),
            cfg.legacy_sync_available !== false && h(SettingRow, { title: "\u540C\u6B65\u6765\u6E90\u914D\u7F6E", description: "\u66F4\u65B0\u539F\u7CFB\u7EDF\u4ECD\u7BA1\u7406\u7684\u6765\u6E90\u76EE\u5F55\uFF1B\u4FDD\u7559\u672C\u5730\u8FDE\u63A5\u4FEE\u6539\u53CA\u6240\u6709\u9879\u76EE\u6388\u6743\u548C\u53E3\u5F84\u3002" }, h(Button, { variant: "outline", size: "sm", disabled: busy || dirty, onClick: () => action(() => settingsRequest("sync", {}), "\u6765\u6E90\u5DF2\u540C\u6B65\uFF0C\u672C\u5730\u8FDE\u63A5\u548C\u9879\u76EE\u8303\u56F4\u4FDD\u6301\u4E0D\u53D8\u3002") }, "\u540C\u6B65\u667A\u80FD\u95EE\u6570"))
          ),
          admin && h(DeleteAction, { key: project, name: "\u9879\u76EE", disabled: busy || dirty, description: "\u5220\u9664\u201C" + current.name + "\u201D\u540E\u505C\u6B62\u65B0\u67E5\u8BE2\u3001\u6E05\u9664\u9ED8\u8BA4\u9009\u62E9\u5E76\u91CA\u653E\u8FDE\u63A5\u5F15\u7528\u3002\u4FDD\u7559\u5386\u53F2\u8BB0\u5F55\u5E76\u64A4\u9500\u7ED3\u679C\u8BBF\u95EE\uFF0C\u53EF\u4ECE\u5DF2\u5220\u9664\u9879\u76EE\u6062\u590D\u4E3A\u8349\u7A3F\uFF1B\u6062\u590D\u540E\u9700\u91CD\u65B0\u6821\u9A8C\u53D1\u5E03\u3002", onDelete: () => update("delete", { confirm_delete: true }, "\u9879\u76EE\u5DF2\u5220\u9664\uFF0C\u5386\u53F2\u7ED3\u679C\u4FDD\u7559\u3002") })
        )
      );
    }
    const chartNames = { table: "\u8868\u683C", kpi: "\u6307\u6807\u5361", bar: "\u6761\u5F62\u56FE", column: "\u67F1\u72B6\u56FE", "grouped-bar": "\u5206\u7EC4\u67F1\u72B6\u56FE", "stacked-bar": "\u5806\u53E0\u67F1\u72B6\u56FE", "percent-stacked-bar": "\u767E\u5206\u6BD4\u5806\u53E0\u56FE", line: "\u6298\u7EBF\u56FE", area: "\u9762\u79EF\u56FE", pie: "\u997C\u56FE", donut: "\u73AF\u5F62\u56FE", scatter: "\u6563\u70B9\u56FE", bubble: "\u6C14\u6CE1\u56FE", heatmap: "\u77E9\u9635\u70ED\u529B\u56FE", treemap: "\u6811\u56FE", histogram: "\u76F4\u65B9\u56FE", radar: "\u96F7\u8FBE\u56FE", funnel: "\u6F0F\u6597\u56FE", gauge: "\u4EEA\u8868\u76D8", waterfall: "\u7011\u5E03\u56FE", boxplot: "\u7BB1\u7EBF\u56FE", "alert-table": "\u9884\u8B66\u8868", "pivot-table": "\u591A\u7EF4\u900F\u89C6\u8868" };
    const aggregationNames = { sum: "\u5408\u8BA1", count: "\u8BA1\u6570", distinct: "\u53BB\u91CD\u8BA1\u6570", mean: "\u7B97\u672F\u5E73\u5747", min: "\u6700\u5C0F\u503C", max: "\u6700\u5927\u503C", weighted_mean: "\u52A0\u6743\u5E73\u5747" };
    function ReportEditor({ meta, query, onSaved, onClose }) {
      const defaultCell = { id: crypto.randomUUID(), result_ref: meta.result_ref, title: meta.title, row_dimensions: (meta.columns || []).filter((c) => !meta.rows?.some((r) => typeof r[c] === "number")).slice(0, 2), column_dimensions: [], measures: [{ column: meta.columns?.includes("value") ? "value" : meta.columns?.find((c) => meta.rows?.some((r) => typeof r[c] === "number")) || "", aggregation: meta.metric_contract?.ratio ? "mean" : "sum", weight_column: null }], filters: [], chart_type: "pivot-table", sort_by: null, descending: false, row_limit: 500 };
      const [spec, setSpec] = React.useState(() => structuredClone(meta.report?.spec || { title: meta.title + " \xB7 \u591A\u7EF4\u62A5\u8868", columns: 2, cells: [defaultCell] })), [busy, setBusy] = React.useState(false), [error, setError] = React.useState(""), [available, setAvailable] = React.useState([]), [open, setOpen] = React.useState("");
      const project = new URLSearchParams(query).get("project");
      React.useEffect(() => {
        fetch(publicUrls.route("/natural-data/api/results?") + new URLSearchParams({ project })).then(async (r) => {
          if (!r.ok) {
            const v = await r.json().catch(() => null);
            throw Error(v?.error || "HTTP " + r.status);
          }
          return r.json();
        }).then(setAvailable).catch((e) => {
          console.error("\u667A\u80FD\u95EE\u6570\uFF1A\u8BFB\u53D6\u7ED3\u679C\u5217\u8868\u5931\u8D25", e);
          setError("\u8BFB\u53D6\u53EF\u7528\u7ED3\u679C\u5931\u8D25\uFF1A" + e.message);
        });
      }, [project]);
      function cell(id, patch) {
        setSpec((s) => ({ ...s, cells: s.cells.map((c) => c.id === id ? { ...c, ...patch } : c) }));
      }
      function choice(id, value, items, change) {
        const label = (v) => chartNames[v] || aggregationNames[v] || v;
        return h(SettingsMenu, { open: open === id, onClose: () => setOpen(""), selectedId: value, items: items.map((v) => ({ id: v, label: label(v) })), onSelect: (v) => {
          setOpen("");
          change(v);
        }, anchor: h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: () => setOpen(id) }, label(value), h(IconChevronDownOutlineMedium, { size: 16 })) });
      }
      async function save() {
        setBusy(true);
        setError("");
        try {
          const body = { project, spec, ...meta.report ? { result_ref: meta.result_ref, expected_revision: meta.report.report_revision } : { attach_to_result_ref: meta.result_ref, expected_attachment_revision: meta.report_attachment_revision || 0 } };
          const r = await fetch(publicUrls.route("/natural-data/api/") + (meta.report ? "edit_report" : "make_report"), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
          const v = await r.json();
          if (!r.ok) throw Error(v.error || "\u62A5\u8868\u4FDD\u5B58\u5931\u8D25");
          onSaved({ ...v, view_url: publicUrls.route("/natural-data/view?") + new URLSearchParams({ project, result_ref: v.result_ref }) });
        } catch (e) {
          setError(e.message);
        } finally {
          setBusy(false);
        }
      }
      return h(
        Detail,
        { title: "\u7F16\u8F91\u591A\u7EF4\u62A5\u8868", initialOpen: true },
        h("label", { className: "nw-field" }, h("span", null, "\u62A5\u8868\u6807\u9898"), h(Input, { value: spec.title, disabled: busy, "aria-label": "\u62A5\u8868\u6807\u9898", onChange: (e) => setSpec((s) => ({ ...s, title: e.target.value })) })),
        h(SettingRow, { title: "\u6BCF\u884C\u5355\u5143\u6570" }, choice("layout", String(spec.columns), ["1", "2", "3", "4"], (v) => setSpec((s) => ({ ...s, columns: Number(v) })))),
        spec.cells.map((c) => h(
          Detail,
          { key: c.id, title: c.title },
          h("label", { className: "nw-field" }, h("span", null, "\u5355\u5143\u6807\u9898"), h(Input, { value: c.title, disabled: busy, "aria-label": "\u5355\u5143\u6807\u9898", onChange: (e) => cell(c.id, { title: e.target.value }) })),
          h(SettingRow, { title: "\u6570\u636E\u7ED3\u679C" }, h(SettingsMenu, { open: open === c.id + "source", onClose: () => setOpen(""), selectedId: c.result_ref, items: available.filter((r) => r.status === "succeeded").map((r) => ({ id: r.result_ref, label: r.title })), onSelect: (v) => {
            cell(c.id, { result_ref: v });
            setOpen("");
          }, anchor: h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: () => setOpen(c.id + "source") }, available.find((r) => r.result_ref === c.result_ref)?.title || "\u5F53\u524D\u67E5\u8BE2\u7ED3\u679C") })),
          [["row_dimensions", "\u884C\u7EF4\u5EA6"], ["column_dimensions", "\u5217\u7EF4\u5EA6"]].map(([key, label]) => h("label", { key, className: "nw-field" }, h("span", null, label + "\uFF08\u9017\u53F7\u5206\u9694\u5B57\u6BB5\uFF09"), h(Input, { value: c[key].join(","), disabled: busy, "aria-label": label, onChange: (e) => cell(c.id, { [key]: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) }) }))),
          c.measures.map((m, j) => h("div", { key: j, className: "nw-actions" }, h(Input, { value: m.column, disabled: busy, "aria-label": "\u5EA6\u91CF\u5B57\u6BB5", onChange: (e) => cell(c.id, { measures: c.measures.map((v, k) => k === j ? { ...v, column: e.target.value } : v) }) }), choice(c.id + "agg" + j, m.aggregation, ["sum", "count", "distinct", "mean", "min", "max", "weighted_mean"], (v) => cell(c.id, { measures: c.measures.map((x, k) => k === j ? { ...x, aggregation: v } : x) })), m.aggregation === "weighted_mean" && h(Input, { value: m.weight_column || "", disabled: busy, "aria-label": "\u6743\u91CD\u5B57\u6BB5", onChange: (e) => cell(c.id, { measures: c.measures.map((x, k) => k === j ? { ...x, weight_column: e.target.value } : x) }) }), h(Button, { size: "sm", variant: "ghost", disabled: busy || c.measures.length === 1, onClick: () => cell(c.id, { measures: c.measures.filter((x, k) => k !== j) }) }, "\u79FB\u9664\u5EA6\u91CF"))),
          h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: () => cell(c.id, { measures: [...c.measures, { column: "value", aggregation: "mean", weight_column: null }] }) }, "\u6DFB\u52A0\u5EA6\u91CF"),
          h(SettingRow, { title: "\u5C55\u793A\u56FE\u5F62" }, choice(c.id + "chart", c.chart_type, ["pivot-table", "table", "column", "bar", "line", "pie", "heatmap"], (v) => cell(c.id, { chart_type: v }))),
          c.filters.map((f, j) => h("div", { key: j, className: "nw-actions" }, h(Input, { value: f.dimension, disabled: busy, "aria-label": "\u7B5B\u9009\u5B57\u6BB5", onChange: (e) => cell(c.id, { filters: c.filters.map((x, k) => k === j ? { ...x, dimension: e.target.value } : x) }) }), choice(c.id + "filter" + j, f.op, ["eq", "ne", "gt", "gte", "lt", "lte", "in"], (v) => cell(c.id, { filters: c.filters.map((x, k) => k === j ? { ...x, op: v } : x) })), h(Input, { value: f.values.join(","), disabled: busy, "aria-label": "\u7B5B\u9009\u503C", onChange: (e) => cell(c.id, { filters: c.filters.map((x, k) => k === j ? { ...x, values: e.target.value.split(",").map((v) => v.trim()) } : x) }) }), h(Button, { size: "sm", variant: "ghost", disabled: busy, onClick: () => cell(c.id, { filters: c.filters.filter((x, k) => k !== j) }) }, "\u79FB\u9664\u7B5B\u9009"))),
          h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: () => cell(c.id, { filters: [...c.filters, { dimension: "period", op: "eq", values: [""] }] }) }, "\u6DFB\u52A0\u7B5B\u9009"),
          h("label", { className: "nw-field" }, h("span", null, "\u6392\u5E8F\u5B57\u6BB5"), h(Input, { value: c.sort_by || "", disabled: busy, "aria-label": "\u6392\u5E8F\u5B57\u6BB5", onChange: (e) => cell(c.id, { sort_by: e.target.value || null }) })),
          h(Checkbox, { label: "\u964D\u5E8F", checked: c.descending, disabled: busy, onChange: (v) => cell(c.id, { descending: v }) }),
          h(Button, { size: "sm", variant: "ghost", disabled: busy || spec.cells.length === 1, onClick: () => setSpec((s) => ({ ...s, cells: s.cells.filter((x) => x.id !== c.id) })) }, "\u79FB\u9664\u5355\u5143")
        )),
        h("div", { className: "nw-actions" }, h(Button, { size: "sm", variant: "outline", disabled: busy || spec.cells.length >= 12, onClick: () => setSpec((s) => ({ ...s, cells: [...s.cells, { ...defaultCell, id: crypto.randomUUID() }] })) }, "\u6DFB\u52A0\u62A5\u8868\u5355\u5143"), h(Button, { size: "sm", disabled: busy, onClick: save }, busy ? "\u6B63\u5728\u4FDD\u5B58\u2026" : "\u4FDD\u5B58\u62A5\u8868"), h(Button, { size: "sm", variant: "ghost", disabled: busy, onClick: onClose }, "\u53D6\u6D88")),
        error && h("p", { role: "alert" }, error)
      );
    }
    function useInView(ref, rootMargin = "400px 0px", settleMs = 60) {
      const [visible, setVisible] = React.useState(() => typeof IntersectionObserver !== "function");
      React.useEffect(() => {
        if (visible || typeof IntersectionObserver !== "function") return;
        const node = ref.current;
        if (!node) return;
        let timer = null;
        const clear = () => {
          if (timer !== null) {
            clearTimeout(timer);
            timer = null;
          }
        };
        const observer = new IntersectionObserver((entries) => {
          if (entries.some((e) => e.isIntersecting)) {
            if (timer === null) timer = setTimeout(() => {
              timer = null;
              setVisible(true);
              observer.disconnect();
            }, settleMs);
          } else clear();
        }, { rootMargin });
        observer.observe(node);
        return () => {
          clear();
          observer.disconnect();
        };
      }, [visible]);
      return visible;
    }
    function skeletonShape(value, meta, kind) {
      if (meta?.report || value?.report) return "report";
      const type = kind || meta?.chart?.type || value?.visualization?.actual || "chart";
      if (type === "table" || type === "pivot-table") return "table";
      if (type === "number" || type === "kpi") return "kpi";
      if (["pie", "donut", "gauge", "radar"].includes(type)) return "pie";
      return "chart";
    }
    function renderSkeleton(shape) {
      if (shape === "report") return h(
        "div",
        { className: "nw-skeleton", "aria-hidden": "true" },
        h("div", { className: "nw-skel-line", style: { width: "38%", height: 16 } }),
        h("div", { className: "nw-skel-grid" }, h("div", { className: "nw-skel-line", style: { height: "100%" } }), h("div", { className: "nw-skel-line", style: { height: "100%" } }))
      );
      if (shape === "table") return h(
        "div",
        { className: "nw-skeleton", "aria-hidden": "true" },
        h("div", { className: "nw-skel-line", style: { width: "100%", height: 28 } }),
        h("div", { className: "nw-skel-rows" }, [0, 1, 2, 3].map((i) => h("div", { key: i, className: "nw-skel-line", style: { width: 94 - i * 8 + "%", height: 20 } })))
      );
      if (shape === "kpi") return h(
        "div",
        { className: "nw-skeleton", "aria-hidden": "true" },
        h("div", { className: "nw-skel-grid" }, [0, 1, 2, 3].map((i) => h("div", { key: i, className: "nw-skel-line", style: { height: 88 } })))
      );
      if (shape === "pie") return h(
        "div",
        { className: "nw-skeleton", "aria-hidden": "true" },
        h(
          "div",
          { className: "nw-skel-circle" },
          h("div", { className: "nw-skel-line nw-skel-circle-ring" }),
          h("div", { style: { flex: 1, display: "grid", gap: 10 } }, [0, 1, 2].map((i) => h("div", { key: i, className: "nw-skel-line", style: { width: 85 - i * 14 + "%", height: 14 } })))
        )
      );
      return h(
        "div",
        { className: "nw-skeleton", "aria-hidden": "true" },
        h("div", { className: "nw-skel-line", style: { width: "36%", height: 16 } }),
        h("div", { className: "nw-skel-bars" }, [42, 78, 56, 90, 64].map((pct, i) => h("span", { key: i, className: "nw-skel-line", style: { height: pct + "%" } })))
      );
    }
    function ToolCard(props) {
      const result = props.phase === "result" ? props.block : null;
      let value;
      try {
        const content = result?.content || result?.result?.content;
        value = JSON.parse(content?.find((x) => x.type === "text")?.text || "{}");
      } catch {
      }
      const [meta, setMeta] = React.useState(null), [tab, setTab] = React.useState("chart"), [menu, setMenu] = React.useState(false), [busy, setBusy] = React.useState(false), [error, setError] = React.useState(""), [revision, setRevision] = React.useState(0);
      const [editing, setEditing] = React.useState(false), [replacement, setReplacement] = React.useState(null), [loading, setLoading] = React.useState(false);
      const [frameAllowed, setFrameAllowed] = React.useState(false), [frameReady, setFrameReady] = React.useState(false);
      const [renderedChart, setRenderedChart] = React.useState(null), [chartIntent, setChartIntent] = React.useState(null);
      const cardRef = React.useRef(null), frame = React.useRef(null), latest = React.useRef(null), sent = React.useRef(null);
      const controller = React.useRef(null), restoreScroll = React.useRef(null), viewChange = React.useRef(null), gateRelease = React.useRef(null);
      const switchIntent = React.useRef(null);
      const visible = useInView(cardRef, "400px 0px", 60);
      const controlId = React.useId();
      const forecastIds = [...props.forecastClaimIds || []].sort().join(",");
      const frameResult = React.useMemo(() => meta && { ...meta, forecast_claim_ids: forecastIds ? forecastIds.split(",") : [] }, [meta, forecastIds]);
      const view = publicUrls.result(value?.view_url);
      const query = view ? new URL(view, location.origin).searchParams.toString() : "";
      const [frameHeight, setFrameHeight] = React.useState(() => readFrameHeight(query));
      const mutationSig = [value?.execution?.id || "", value?.view_revision ?? "", value?.analysis_ref || "", value?.visualization?.actual || "", value?.report ? "1" : "", forecastIds].join("|");
      const prevLoadState = React.useRef({ view, mutationSig, revision });
      const releaseGate = () => {
        if (gateRelease.current) {
          const fn = gateRelease.current;
          gateRelease.current = null;
          fn();
        }
      };
      React.useEffect(() => {
        setFrameReady(false);
        setRenderedChart(null);
        setChartIntent(null);
        setFrameAllowed(false);
        setFrameHeight(readFrameHeight(query));
        return () => {
          switchIntent.current = null;
        };
      }, [view, query]);
      const captureScroll = () => {
        const positions = [];
        for (let node = frame.current?.parentElement; node; node = node.parentElement) if (node.scrollHeight > node.clientHeight) positions.push([node, node.scrollTop, node.scrollLeft]);
        return () => {
          for (const [node, top, left] of positions) {
            node.scrollTop = top;
            node.scrollLeft = left;
          }
        };
      };
      const io = {
        accept: (v, patch) => {
          viewChange.current = patch ? { result: v, patch } : null;
          if (patch && v && query) cacheResultEntry(resultCache, resultCacheKey(query), v);
          setMeta(v);
        },
        busy: setBusy,
        error: setError,
        load: () => loadResult(query, { bypassCache: true }),
        update: async (kind, expected_revision) => {
          const response = await fetch(publicUrls.route("/natural-data/api/visualize"), {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ ...Object.fromEntries(new URLSearchParams(query)), kind, expected_revision })
          });
          let payload;
          try {
            payload = await response.json();
          } catch (error2) {
            if (error2.name === "AbortError") throw error2;
          }
          if (!response.ok) throw Object.assign(Error(payload?.error || "\u5207\u6362\u56FE\u8868\u5931\u8D25"), { status: response.status });
          return payload?.view_patch;
        }
      };
      if (!controller.current) controller.current = new ViewController(io);
      controller.current.io = io;
      React.useEffect(() => () => {
        releaseGate();
        controller.current?.dispose();
      }, []);
      React.useEffect(() => {
        const restore = restoreScroll.current;
        if (!restore) return;
        restore();
        const id = requestAnimationFrame(() => {
          restore();
          if (restoreScroll.current === restore) restoreScroll.current = null;
        });
        return () => cancelAnimationFrame(id);
      }, [meta, tab, frameHeight]);
      React.useEffect(() => {
        if (!visible || !view) return;
        const prev = prevLoadState.current;
        const sameViewMutated = prev.view === view && prev.mutationSig !== mutationSig;
        const retried = revision !== prev.revision;
        prevLoadState.current = { view, mutationSig, revision };
        const abort = new AbortController();
        let live = true;
        setLoading(true);
        setError("");
        loadResult(query, { signal: abort.signal, bypassCache: sameViewMutated || retried }).then((v) => {
          if (!live) return;
          setError("");
          if (v.report_target) {
            releaseGate();
            setReplacement({ status: "succeeded", result_ref: v.report_target, view_url: publicUrls.route("/natural-data/view?") + new URLSearchParams({ project: new URLSearchParams(query).get("project"), result_ref: v.report_target }) });
          } else controller.current.accept(v);
        }).catch((e) => {
          if (live && e.name !== "AbortError") {
            releaseGate();
            setError(e.message);
          }
        }).finally(() => {
          if (live) setLoading(false);
        });
        return () => {
          live = false;
          abort.abort();
        };
      }, [visible, view, revision, mutationSig]);
      React.useEffect(() => {
        if (!visible || !view || replacement || error && !meta) return;
        const abort = new AbortController();
        let live = true, safetyTimer = null;
        defaultFrameGate.acquire(abort.signal).then((release) => {
          if (!live) {
            release();
            return;
          }
          gateRelease.current = release;
          setFrameAllowed(true);
          safetyTimer = setTimeout(() => releaseGate(), 8e3);
        }).catch((e) => {
          if (e?.name !== "AbortError") releaseGate();
        });
        return () => {
          live = false;
          if (safetyTimer !== null) clearTimeout(safetyTimer);
          abort.abort();
          releaseGate();
        };
      }, [visible, view, replacement, Boolean(error && !meta), revision]);
      latest.current = { tab, result: frameResult, meta, forecastIds, query };
      const send = (force = false) => {
        const target = frame.current?.contentWindow, next = latest.current;
        if (!target || !next?.result) return;
        const change = viewChange.current;
        const previous = !force && sent.current?.target === target ? sent.current.state : null;
        const sameClaims = previous?.forecastIds === next.forecastIds;
        const message = resultFrameMessage(previous, next, sameClaims && change?.result === next.meta ? change.patch.base_view_revision : void 0);
        if (message) target.postMessage(message, location.origin);
        sent.current = { target, state: next };
      };
      React.useEffect(() => {
        send();
      }, [frameResult, tab]);
      React.useEffect(() => {
        const receive = (event) => {
          if (event.origin !== location.origin || event.source !== frame.current?.contentWindow) return;
          if (event.data?.type === "natural-data:ready") send(true);
          const current = latest.current;
          const message = event.data;
          const height = resultFrameHeight(message);
          if (height !== null && current?.meta && message.result_ref === current.meta.result_ref && message.view_revision === (current.meta.view_revision ?? 0) && message.tab === current.tab) {
            setFrameHeight(height);
            if (message.rendered !== true) return;
            if (message.tab === "chart") {
              setRenderedChart(message.result_ref + ":" + message.view_revision);
              writeFrameHeight(current.query, height);
            }
            setFrameReady(true);
            releaseGate();
          }
        };
        window.addEventListener("message", receive);
        return () => window.removeEventListener("message", receive);
      }, [view]);
      async function choose(kind) {
        setMenu(false);
        const previous = switchIntent.current;
        if (kind === (previous?.kind ?? controller.current.current?.chart?.type)) return;
        const intent = { kind, view };
        switchIntent.current = intent;
        restoreScroll.current = captureScroll();
        setTab("chart");
        setChartIntent(intent);
        if (previous?.view === view) return;
        for (let next = intent; next?.view === view; next = switchIntent.current) {
          if (controller.current.current?.chart?.type !== next.kind) await controller.current.select(next.kind);
          if (switchIntent.current === next) {
            switchIntent.current = null;
            setChartIntent(null);
            break;
          }
        }
      }
      if (replacement) return h(ToolCard, { key: replacement.result_ref, phase: "result", block: { content: [{ type: "text", text: JSON.stringify(replacement) }] } });
      const actual = meta?.chart?.type || "table", choices = [.../* @__PURE__ */ new Set([actual, ...meta?.chart?.allowedAlternatives || [], "table"])];
      const pendingKind = chartIntent?.view === view ? chartIntent.kind : null;
      const selectedKind = pendingKind || actual;
      const chartIsReady = meta && renderedChart === meta.result_ref + ":" + (meta.view_revision ?? 0);
      const showSkeleton = !frameReady || tab === "chart" && (Boolean(pendingKind) || !chartIsReady);
      const rawTitle = meta?.title || value?.title;
      const label = publicMetricLabel(rawTitle, meta);
      const title = resultDisplayTitle(
        label === rawTitle ? publicMetricLabel(rawTitle, value) : label,
        meta?.source_provenance || value?.source_provenance
      ) || "\u95EE\u6570\u7ED3\u679C";
      const shape = skeletonShape(value, meta, tab === "chart" ? selectedKind : void 0);
      const showStage = view && !(error && !meta);
      return h(
        "div",
        { ref: cardRef, className: "nw-data-tool" },
        h("div", { className: "nw-data-tool-header" }, h(IconDataOutlineMedium, { size: 16 }), h("span", null, title)),
        meta && h("div", { className: "nw-setting-description" }, `${meta.row_count || 0} \u884C${meta.truncated ? " \xB7 \u5DF2\u622A\u65AD\uFF0C\u4EC5\u5C55\u793A\u5F53\u524D\u8FD4\u56DE\u90E8\u5206" : ""}`),
        meta && h(
          "div",
          { style: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 12 } },
          h(SegmentedControl, { id: controlId, value: tab, label: "\u95EE\u6570\u7ED3\u679C\u5C55\u793A", options: [{ value: "chart", label: "\u56FE\u8868" }, { value: "data", label: "\u6570\u636E" }, { value: "receipt", label: "\u67E5\u8BE2\u4F9D\u636E" }], onChange: (next) => {
            restoreScroll.current = captureScroll();
            setTab(next);
          } }),
          !meta.report && h(Menu, { open: menu, onClose: () => setMenu(false), selectedId: selectedKind, items: choices.map((id) => ({ id, label: (chartNames[id] || id) + (id !== selectedKind && id === meta.chart?.recommendedType ? "\uFF08\u63A8\u8350\uFF09" : "") })), onSelect: choose, anchor: h(Button, { size: "sm", "aria-label": "\u56FE\u8868\u7C7B\u578B", "aria-haspopup": "menu", "aria-expanded": menu, onClick: () => setMenu((v) => !v) }, chartNames[selectedKind] || selectedKind, h(IconChevronDownOutlineMedium, { size: 16 })) })
        ),
        loading && !meta && h("span", { role: "status", className: "nw-sr-only" }, "\u6B63\u5728\u8BFB\u53D6\u5DF2\u4FDD\u5B58\u7684\u7ED3\u679C\u2026"),
        meta && tab === "chart" && showSkeleton && h("span", { role: "status", className: "nw-sr-only" }, "\u6B63\u5728\u52A0\u8F7D" + (chartNames[selectedKind] || "\u56FE\u8868") + "\u2026"),
        error && h("div", null, h("p", { role: "alert", className: "nw-setting-status", "data-error": true }, error), h(Button, { size: "sm", variant: "outline", disabled: loading || busy, onClick: () => setRevision((v) => v + 1) }, "\u91CD\u65B0\u52A0\u8F7D")),
        showStage && h(
          "div",
          { role: "tabpanel", id: controlId + "-" + tab + "-panel", "aria-labelledby": controlId + "-" + tab, "aria-busy": showSkeleton || void 0, className: "nw-data-tool-stage", style: { height: frameHeight } },
          showSkeleton && renderSkeleton(shape),
          visible && frameAllowed && h("iframe", { ref: frame, className: showSkeleton ? "" : "nw-frame-ready", tabIndex: showSkeleton ? -1 : void 0, "aria-hidden": showSkeleton || void 0, src: view + "&embedded=1", onLoad: () => send(), title })
        ),
        !view && h(Button, { variant: "ghost", size: "sm", onClick: props.inspect }, "\u67E5\u770B\u8BE6\u60C5")
      );
    }
    const resultDefinition = resultEvents(publicUrls, {
      onMutation: (v, _name, seq) => {
        const ref = v?.result_ref;
        if (!ref) return;
        const stamp = `${seq || ""}:${v.view_revision ?? ""}:${v.analysis_ref || ""}:${v.report ? 1 : 0}`;
        if (resultMutationMarks.get(ref) === stamp) return;
        resultMutationMarks.set(ref, stamp);
        invalidateResultRef(ref);
      }
    });
    function ContinueAnswer({ resume }) {
      const [busy, setBusy] = React.useState(false), [error, setError] = React.useState("");
      return h("div", { className: "nw-setting-status" }, h("p", null, "\u5DF2\u751F\u6210\u7684\u67E5\u8BE2\u7ED3\u679C\u5DF2\u4FDD\u7559\uFF1B\u540E\u7EED\u6587\u5B57\u6574\u7406\u6216\u5206\u6790\u672A\u5B8C\u6210\uFF0C\u4E0D\u5F71\u54CD\u4E0B\u65B9\u5DF2\u6210\u529F\u7684\u7ED3\u679C\u3002\u53EF\u57FA\u4E8E\u5DF2\u6709\u7ED3\u679C\u7EE7\u7EED\u3002"), h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: async () => {
        setBusy(true);
        setError("");
        try {
          await resume();
        } catch {
          setError("\u672A\u80FD\u7EE7\u7EED\uFF0C\u8BF7\u7A0D\u540E\u91CD\u8BD5\u3002");
        } finally {
          setBusy(false);
        }
      } }, busy ? "\u6B63\u5728\u7EE7\u7EED\u2026" : "\u7EE7\u7EED\u5B8C\u6210\u56DE\u7B54"), error && h("p", { role: "alert" }, error));
    }
    function AnswerQuality({ sessionId, turn, seq, hasCards }) {
      const [quality, setQuality] = React.useState(null);
      React.useEffect(() => {
        if (!sessionId) return;
        const controller = new AbortController();
        setQuality(null);
        fetch(publicUrls.route("/natural-data/api/answer-quality?") + new URLSearchParams({ session: sessionId, turn: String(turn) }), { signal: controller.signal }).then((response) => response.ok ? response.json() : null).then((value) => {
          if (!controller.signal.aborted) setQuality(value);
        }).catch((error) => {
          if (!controller.signal.aborted) console.warn("\u667A\u80FD\u95EE\u6570\uFF1A\u8BFB\u53D6\u56DE\u7B54\u6838\u9A8C\u72B6\u6001\u5931\u8D25", error);
        });
        return () => controller.abort();
      }, [sessionId, turn, seq]);
      if (quality?.status !== "partial") return null;
      return h("p", { role: "status", className: "nw-setting-status" }, quality.has_result ?? hasCards ? "\u90E8\u5206\u5B8C\u6210 \xB7 \u5DF2\u4FDD\u7559\u67E5\u8BE2\u7ED3\u679C" : "\u672C\u8F6E\u672A\u53D6\u5F97\u53EF\u6838\u5B9E\u7684\u67E5\u8BE2\u7ED3\u679C");
    }
    function ProcessResults({ items, forecastByRef }) {
      const [open, setOpen] = React.useState(false);
      return h(
        "div",
        { className: "nw-process-results" },
        h(DisclosureRow, { title: "\u67E5\u8BE2\u8FC7\u7A0B\uFF08" + items.length + " \u6761\uFF09", open, expandable: true, expandOnRowClick: true, icon: h(IconDataOutlineMedium, { size: 16 }), onToggle: () => setOpen((v) => !v) }),
        open && h("div", { className: "nw-process-list" }, items.map((value) => h(ToolCard, { key: value.result_ref, phase: "result", forecastClaimIds: forecastByRef.get(value.result_ref), block: { content: [{ type: "text", text: JSON.stringify(value) }] } })))
      );
    }
    function AnswerResults({ turn, seq, resume, sessionId }) {
      const state = turn.data.get("natural-data-results");
      const items = (Array.isArray(state) ? state : state?.results || []).filter((item) => item.seq <= seq || item.failure);
      const quality = state?.ended && state.attempted ? h(AnswerQuality, { sessionId, turn: turn.turn, seq, hasCards: items.some((item) => !isExploratory(item.value)) }) : null;
      if (!items.length) return quality;
      const failed = !!state?.failed || items.some((item) => item.failure);
      const comparison = state?.comparisonPreview;
      const { main, process } = pickAnswerCards(items, state?.answer || "", failed, {
        userText: state?.userText || "",
        comparisonPreviewRef: comparison?.seq <= seq ? comparison.ref : void 0
      });
      const forecastByRef = forecastClaimsByResult(items);
      if (!main.length && !process.length) return quality;
      return h("section", { "aria-label": "\u672C\u8F6E\u95EE\u6570\u56FE\u8868", "data-natural-data-answer": true }, main.map((value) => h(ToolCard, { key: value.result_ref, phase: "result", forecastClaimIds: forecastByRef.get(value.result_ref), block: { content: [{ type: "text", text: JSON.stringify(value) }] } })), process.length > 0 && h(ProcessResults, { items: process, forecastByRef }), failed && resume && h(ContinueAnswer, { resume: () => resume(turn.turn) }), quality);
    }
    function apply(ctx) {
      openNativeSession = (id) => ctx.uiWorkspace.openSession(id);
      ctx.effect(() => () => {
        openNativeSession = null;
      });
      lifetime = new AbortController();
      const owner = lifetime;
      const step = (name, fn) => {
        try {
          fn();
        } catch (error) {
          console.error("\u667A\u80FD\u95EE\u6570\u754C\u9762\u521D\u59CB\u5316\u5931\u8D25\uFF1A" + name, error);
        }
      };
      if (typeof ctx.layout?.selectPanel !== "function") step("\u8BBE\u7F6E\u5165\u53E3", () => ctx.slots.inject("settings.section", () => ctx.slots.register({ name: "settings.section", id: "natural-data", label: () => "\u667A\u80FD\u95EE\u6570", order: 45 }, DataSettings)));
      step("\u751F\u547D\u5468\u671F", () => ctx.effect(() => () => {
        owner.abort();
        resultCache.clear();
        resultMutationMarks.clear();
      }));
      step("\u6837\u5F0F", () => ctx.effect(() => {
        const style = document.createElement("style");
        style.dataset.naturalWorkUi = "";
        style.textContent = css + administrationCss + consoleCss;
        document.head.appendChild(style);
        return () => style.remove();
      }));
      if (typeof ctx.layout?.selectPanel === "function") step("\u914D\u7F6E\u7BA1\u7406\u5165\u53E3", () => {
        const panel = "natural-data-administration";
        openAdministration = () => ctx.layout.selectPanel(panel);
        ctx.effect(() => () => {
          openAdministration = null;
        });
        const NO_SNAPSHOT = () => null;
        const MainConsole = (slotProps = {}) => {
          const useWorkspaces = typeof slotProps.useWorkspaces === "function" ? slotProps.useWorkspaces : NO_SNAPSHOT;
          const useSessions = typeof slotProps.useSessions === "function" ? slotProps.useSessions : NO_SNAPSHOT;
          const wsSnap = useWorkspaces((s) => s);
          const sessSnap = useSessions((s) => s);
          const workspaces = Array.isArray(wsSnap?.items) ? wsSnap.items.map((w) => ({ id: w.workspaceId, path: w.path, title: w.title })) : [];
          const mainSession = sessSnap?.byId ? Object.values(sessSnap.byId).find((s) => (s?.retainedBy?.mainView ?? 0) > 0) : null;
          const matchedWorkspace = mainSession && Array.isArray(wsSnap?.items) ? wsSnap.items.find((w) => Array.isArray(w.sessionIds) && w.sessionIds.includes(mainSession.id)) : null;
          const currentWorkspace = matchedWorkspace?.path || mainSession?.cwd || "";
          return h(Console, { onBack: () => ctx.layout.selectPanel(null), currentWorkspace, workspaces });
        };
        const register = () => {
          ctx.slots.inject("main", () => ctx.slots.register({ name: "main", key: panel }, MainConsole));
          ctx.slots.inject("sidebar.panellist", () => ctx.slots.register({ name: "sidebar.panellist", id: panel, label: () => "\u914D\u7F6E\u7BA1\u7406", order: 95 }, ({ size }) => h(IconDataOutlineMedium, { size })));
        };
        const delays = [1e3, 3e3, 1e4];
        const load = (attempt) => settingsRequest("config", void 0, { view: "manager" }).then((value) => {
          if (owner.signal.aborted || !value.system_admin && !value.projects.some((p) => ["admin", "editor"].includes(p.role))) return;
          register();
        }).catch((error) => {
          if (owner.signal.aborted || error?.name === "AbortError") return;
          if (attempt < delays.length) {
            console.warn("\u667A\u80FD\u95EE\u6570\uFF1A\u8BFB\u53D6\u914D\u7F6E\u5931\u8D25\uFF0C\u7A0D\u540E\u91CD\u8BD5", error);
            setTimeout(() => {
              if (!owner.signal.aborted) load(attempt + 1);
            }, delays[attempt]);
            return;
          }
          console.error("\u667A\u80FD\u95EE\u6570\uFF1A\u8BFB\u53D6\u914D\u7F6E\u5931\u8D25\uFF0C\u914D\u7F6E\u7BA1\u7406\u9875\u4F1A\u663E\u793A\u539F\u56E0", error);
          register();
        });
        load(0);
      });
      step("\u7ED3\u679C\u4E8B\u4EF6", () => {
        ctx.uiConversation.events.register(resultRequestEvents);
        ctx.uiConversation.events.register(resultDefinition);
      });
      step("\u7ED3\u679C\u5361", () => ctx.slots.inject("conversation.chat.turnTail", () => ctx.slots.register({ name: "conversation.chat.turnTail", id: "natural-data-results", order: 20, inject: (sessionId) => ({ sessionId, resume: (turn) => ctx.sessions.scope(sessionId).get("conversation").send("\u7EE7\u7EED\u5B8C\u6210\u7B2C" + turn + "\u8F6E\u672A\u5B8C\u6210\u7684\u56DE\u7B54\u3002\u5148\u590D\u7528\u672C\u4F1A\u8BDD\u5DF2\u6210\u529F\u7684\u67E5\u8BE2\u7ED3\u679C\u548C\u5DE5\u5E8F\u56DE\u6267\uFF1B\u9664\u975E\u7ED3\u679C\u786E\u5B9E\u7F3A\u5931\u6216\u6761\u4EF6\u6539\u53D8\uFF0C\u4E0D\u8981\u91CD\u653E\u67E5\u8BE2\u3002\u65E0\u6CD5\u786E\u8BA4\u7684\u89E3\u91CA\u8BF7\u660E\u786E\u8BF4\u660E\u3002") }) }, AnswerResults)));
      step("\u5DE5\u5177\u5361", () => {
        for (const op of ["query", "report", "analyze", "visualize"]) ctx.slots.inject("tool.call.toolview", () => ctx.slots.register({ name: "tool.call.toolview", key: "data_" + op }, ToolCard));
      });
    }
    return { inject, apply, ViewController };
  } });
})();
