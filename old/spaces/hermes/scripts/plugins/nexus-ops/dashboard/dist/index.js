// nexus-ops dashboard plugin — 下游探活 + Supabase 业务表只读查 tab
// 预建 JS(非 TS,无 npm build)。SDK 提供 React/hooks,fetch /api/plugins/nexus-ops/*。
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  const React = SDK.React;
  const hooks = SDK.hooks;
  const API = "/api/plugins/nexus-ops";
  const js = function (m) { try { return JSON.stringify(m, null, 2); } catch (e) { return String(m); } };

  function table(rows, cols) {
    if (!rows || !rows.length) return React.createElement("div", null, "(空)");
    return React.createElement("table", { border: "1", cellPadding: "4",
        style: { borderCollapse: "collapse", fontSize: "12px" } },
      React.createElement("thead", null, React.createElement("tr", null,
        cols.map(function (c) { return React.createElement("th", { key: c }, c); }))),
      React.createElement("tbody", null, rows.map(function (row, i) {
        return React.createElement("tr", { key: i }, cols.map(function (c) {
          return React.createElement("td", { key: c }, js(row[c]));
        }));
      }))
    );
  }

  const OpsTab = function () {
    const [probe, setProbe] = hooks.useState({});
    const [states, setStates] = hooks.useState([]);
    const [tasks, setTasks] = hooks.useState([]);
    const [logsV, setLogs] = hooks.useState([]);
    const [mem, setMem] = hooks.useState([]);
    const [tab, setTab] = hooks.useState("probe");
    const [msg, setMsg] = hooks.useState("");
    const [busy, setBusy] = hooks.useState(false);

    const get = async function (path, setFn) {
      setBusy(true);
      try {
        const r = await fetch(API + path);
        const d = await r.json();
        if (d.error) { setMsg(d.error); return; }
        setMsg("");
        setFn(d.rows || d);
      } catch (e) { setMsg("请求失败: " + e); }
      finally { setBusy(false); }
    };

    const refreshAll = hooks.useCallback(async function () {
      await get("/probe", setProbe);
      await get("/states", setStates);
      await get("/tasks", setTasks);
      await get("/logs", setLogs);
      await get("/memory", setMem);
    }, []);

    hooks.useEffect(function () { refreshAll(); }, [refreshAll]);

    const Btn = function (label, key) {
      return React.createElement("button", {
        onClick: function () { setTab(key); }, disabled: busy,
        style: { fontWeight: tab === key ? "bold" : "normal", marginRight: "6px" }
      }, label);
    };

    return React.createElement("div", { style: { padding: "16px" } },
      React.createElement("h2", null, "下游 Space + 业务表"),
      React.createElement("button", { onClick: refreshAll, disabled: busy }, busy ? "..." : "🔄 全刷新"),
      React.createElement("div", { style: { margin: "8px 0" } },
        Btn("探活", "probe"), Btn("States", "states"), Btn("Tasks", "tasks"),
        Btn("Logs", "logs"), Btn("Memory", "memory")),
      msg ? React.createElement("div", { style: { color: "#c00" } }, msg) : null,
      tab === "probe" ? React.createElement("div", null,
        React.createElement("h3", null, "下游 Space 健康"),
        React.createElement("ul", null,
          Object.keys(probe).map(function (k) {
            return React.createElement("li", { key: k },
              k + ": ",
              React.createElement("span", {
                style: { color: probe[k] === "ok" ? "#080" : "#c00", fontWeight: "bold" }
              }, probe[k] || "unknown"));
          }))) : null,
      tab === "states" ? table(states, ["thread_id"]) : null,
      tab === "tasks" ? table(tasks, ["thread_id", "space", "status", "created_at"]) : null,
      tab === "logs" ? table(logsV, ["thread_id", "space_name", "action", "status", "created_at"]) : null,
      tab === "memory" ? table(mem, ["key"]) : null
    );
  };

  window.__HERMES_PLUGINS__.register("nexus-ops", OpsTab);
})();
