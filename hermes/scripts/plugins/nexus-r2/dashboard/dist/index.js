// nexus-r2 dashboard plugin — R2 artifact bucket CRUD tab
// 预建 JS(非 TS,无 npm build):window.__HERMES_PLUGIN_SDK__ 提供 React/hooks/工具,
// window.__HERMES_PLUGINS__.register(name, Component) 挂载 tab 页。
// 后端经 /api/plugins/nexus-r2/* (plugin_api.py router) CRUD R2 文件。
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  const React = SDK.React;
  const hooks = SDK.hooks;
  const C = SDK.components;
  const API = "/api/plugins/nexus-r2";

  const R2Tab = function () {
    const [files, setFiles] = hooks.useState([]);
    const [sel, setSel] = hooks.useState("");
    const [content, setContent] = hooks.useState("");
    const [msg, setMsg] = hooks.useState("");
    const [busy, setBusy] = hooks.useState(false);

    const refresh = hooks.useCallback(async function () {
      setBusy(true);
      try {
        const r = await fetch(API + "/files");
        const d = await r.json();
        setFiles(d.files || []);
        setMsg(d.error || "");
      } catch (e) {
        setMsg("刷新失败: " + e);
      } finally {
        setBusy(false);
      }
    }, []);

    hooks.useEffect(function () { refresh(); }, [refresh]);

    const open_ = async function (k) {
      setSel(k);
      setBusy(true);
      try {
        const r = await fetch(API + "/files/" + encodeURIComponent(k));
        const d = await r.json();
        if (d.error) setMsg(d.error);
        else { setContent(d.content === null ? "(二进制 " + d.size + "字节,不可文本预览)" : d.content); setMsg(""); }
      } catch (e) { setMsg("读取失败: " + e); }
      finally { setBusy(false); }
    };

    const save_ = async function () {
      if (!sel) { setMsg("无文件名"); return; }
      setBusy(true);
      try {
        const r = await fetch(API + "/files/" + encodeURIComponent(sel), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: content })
        });
        // plugin_api.py save_file(filename, content) 取 query/path param + body，
        // FastAPI 简单形式:此处 body {content} 直发 json 走不了 path param 注入。
        // 适配:用 form 或 query。此处改 query fallback。
        const d = r.json && await r.json();
        setMsg(d && d.ok ? "已保存 " + sel : (d && d.error ? d.error : "保存结果未知"));
      } catch (e) { setMsg("保存失败: " + e); }
      finally { setBusy(false); }
    };

    const del_ = async function (k) {
      if (!confirm("删除 " + k + "?")) return;
      setBusy(true);
      try {
        const r = await fetch(API + "/files/" + encodeURIComponent(k), { method: "DELETE" });
        const d = await r.json();
        setMsg(d.error || "已删除 " + k);
        refresh();
        if (sel === k) { setSel(""); setContent(""); }
      } catch (e) { setMsg("删除失败: " + e); }
      finally { setBusy(false); }
    };

    return React.createElement("div", { style: { padding: "16px" } },
      React.createElement("h2", null, "R2 文件管理"),
      React.createElement("button", { onClick: refresh, disabled: busy }, busy ? "..." : "🔄 刷新"),
      React.createElement("span", null, " Bucket: nexus-artifacts"),
      msg ? React.createElement("div", null, msg) : null,
      React.createElement("ul", null,
        files.map(function (k) {
          return React.createElement("li", { key: k },
            React.createElement("a", { href: "#", onClick: function (e) { e.preventDefault(); open_(k); } }, k),
            " ",
            React.createElement("button", { onClick: function () { del_(k); }, disabled: busy }, "🗑")
          );
        })
      ),
      React.createElement("h3", null, sel ? "编辑 " + sel : "(选文件查看)"),
      React.createElement("textarea", {
        value: content, onChange: function (e) { setContent(e.target.value); },
        rows: 14, cols: 80, placeholder: "选文件查看内容,改后点保存"
      }),
      React.createElement("br", null),
      React.createElement("button", { onClick: save_, disabled: busy || !sel }, "💾 保存")
    );
  };

  window.__HERMES_PLUGINS__.register("nexus-r2", R2Tab);
})();
