// ══════════════════════════════════════════════════════════════════
// [骨架留档·2026-07-29 标注] 本件为 omn 血统 Express PSK 网关(gate.js)模板,
// 即 OmniRoute(diegosouzapw/omniroute 上游 TS/Next.js 模型路由网关)的 :7860→后端
// PSK 代理网关,非 Nexus 现役代码。Nexus hermes 现役 Space 用 FastAPI/uvicorn 直接
// 暴露业务端口(见 spaces/hermes/app/main.py),无此前置 PSK 网关层。
// 保留本件作 omn 网关契约速查可借鉴点:
//   ① GATE_ADMIN_ENABLED 纯布尔非 token 鉴权口径;
//   ② safeEqual 常量时间比 PSK,PSK 缺/<16 FATAL process.exit(1)(line46-49)
//      = omn 唯一真硬断言 exit 1 位(与版本硬断言 EXPECTED_VERSION 相异:omn 不存在硬断言只 fail-open WARN);
//   ③ 无 retry/无退避,网关契约严格 fail-closed(生产缺 PSK 即死)。
// Nexus 若引入网关层(如 OmniRoute 作下游模型数据面后端,见 memory nexus-omn-merge-port-plan)
// 此件可移植参考;直接当 Nexus 现役部署件执行则误。
// ══════════════════════════════════════════════════════════════════
const express = require('express');
const crypto = require('crypto');
const { createProxyMiddleware } = require('http-proxy-middleware');

const app = express();
const PORT = process.env.PORT || 7860;
const COMPONENT = process.env.NEXUS_COMPONENT || 'omniroute';

// 端口契约
const PORT_MAP = {
  omniroute: process.env.OMNIROUTE_PORT || 3000,
  hermes: process.env.HERMES_PORT || 8080,
  langgraph: process.env.LANGGRAPH_PORT || 8000,
  claude: process.env.CLAUDE_PORT || 8080,
  codex: process.env.CODEX_PORT || 8080
};
const TARGET_PORT = PORT_MAP[COMPONENT] || 3000;

// 1. 认证安全: CONSTANT-TIME PSK 安全校验 (护栏)
const INTERNAL_PSK = process.env.INTERNAL_PSK || '';
if (!INTERNAL_PSK || INTERNAL_PSK.length < 16) {
  console.error('[gate] FATAL: INTERNAL_PSK missing or <16 chars. Fail-closed!');
  process.exit(1);
}

function safeEqual(a, b) {
  if (!a || !b) return false;
  const ba = Buffer.from(a), bb = Buffer.from(b);
  if (ba.length !== bb.length) return false;
  return crypto.timingSafeEqual(ba, bb);
}

// 2. 限流守卫: 字节级别的大上下文防御 (防 OOM 崩溃)
const CTX_GUARD_ENABLED = process.env.GATE_CTX_GUARD_ENABLED !== '0';
const CTX_MAX_BYTES = parseInt(process.env.GATE_CTX_MAX_BYTES || '1500000', 10) || 1500000;
const CTX_BYTES_PER_TOKEN = parseInt(process.env.GATE_CTX_BYTES_PER_TOKEN || '8', 10) || 8;

// Admin 路由开关 (Fail-closed)
const ADMIN_ENABLED = process.env.GATE_ADMIN_ENABLED === '1';

app.use((req, res, next) => {
  // 健康检查与 Admin 开关
  if (req.path === '/healthz') return next();
  
  if (req.path.startsWith('/admin') && !ADMIN_ENABLED) {
    return res.status(404).end();
  }

  // 校验 PSK
  const auth = req.headers.authorization || '';
  if (!auth.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Unauthorized: missing token' });
  }
  const token = auth.slice(7).trim();
  if (!safeEqual(token, INTERNAL_PSK)) {
    return res.status(401).json({ error: 'Unauthorized: invalid token' });
  }

  // 字节数大小防御
  if (CTX_GUARD_ENABLED && req.method === 'POST') {
    const cl = parseInt(req.headers['content-length'] || '0', 10);
    if (cl > CTX_MAX_BYTES) {
      const estTokens = Math.floor(cl / CTX_BYTES_PER_TOKEN);
      return res.status(413).json({
        error: {
          type: 'context_length_exceeded',
          est_tokens: estTokens,
          limit_bytes: CTX_MAX_BYTES
        }
      });
    }
  }

  next();
});

// 3. 透明网关代理
app.get('/healthz', async (req, res) => {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);
    const r = await fetch(`http://127.0.0.1:${TARGET_PORT}/healthz`, { signal: controller.signal });
    clearTimeout(timeoutId);
    if (r.ok) {
      res.json({ ok: true, component: COMPONENT });
    } else {
      res.status(503).json({ ok: false, status: r.status });
    }
  } catch (err) {
    res.status(503).json({ ok: false, error: err.message });
  }
});

app.use('/', createProxyMiddleware({
  target: `http://127.0.0.1:${TARGET_PORT}`,
  changeOrigin: true,
  ws: true,
  logLevel: 'warn',
  onError: (err, req, res) => {
    const logLine = JSON.stringify({
      ts: Date.now(),
      level: 'error',
      component: 'gate',
      path: req.path,
      msg: err.message
    });
    process.stderr.write(logLine + '
');
    res.status(502).json({ error: 'Bad Gateway via Proxy' });
  }
}));

app.listen(PORT, '0.0.0.0', () => {
  console.log(`[gate] Running on port ${PORT}, routing to ${TARGET_PORT}`);
});
