/**
 * Nexus Gateway — Cloudflare Worker
 *
 * Space 间统一入口：鉴权 + 路由 + 保活探测。
 * 部署：cd workers/gateway && npx wrangler deploy
 * Secret：npx wrangler secret put NEXUS_API_KEY
 * 变量：SPACE_OWNER（wrangler.toml [vars] 或 dashboard）
 */

interface RouteBody {
  space: "langgraph" | "claude" | "codex";
  path: string; // 仅白名单端点：/execute /run /complete /health
  body: unknown;
}

const SPACE_REPOS: Record<string, string> = { langgraph: "langgraph", claude: "claude-code", codex: "codex" };

export default {
  async fetch(req: Request, env: Record<string, string>): Promise<Response> {
    const url = new URL(req.url);
    // 入站 X-Request-ID 透传，缺则生成，全链路串联排障用
    const rid = (req.headers.get("X-Request-ID") ?? "").trim() || crypto.randomUUID();

    if (url.pathname === "/health") {
      return json({ status: "ok", service: "nexus-gateway" });
    }

    // /probe ：探测全部下游 Space /health（保活核心）。需鉴权。
    if (url.pathname === "/probe") {
      const err = requireAuth(req, env, rid);
      if (err) return err;
      return json(await probeAllSpaces(env));
    }

    if (url.pathname !== "/route") {
      return errResponse("not_found", "not found", 404, rid);
    }
    if (req.method !== "POST") {
      return errResponse("method_not_allowed", "method not allowed", 405, rid);
    }

    // 鉴权
    const err = requireAuth(req, env, rid);
    if (err) return err;

    const { space, path, body } = (await req.json().catch(() => ({}))) as Partial<RouteBody>;
    if (!space || !SPACE_REPOS[space]) {
      return errResponse("invalid_space", "invalid space", 400, rid);
    }
    if (!path || !isAllowedPath(path)) {
      // 白名单 + 防争用：仅放行已知下游端点，挡住任意 path 透传（SSRF 面）
      return errResponse("invalid_path", "invalid path", 400, rid);
    }

    // 下游 URL：显式 var 优先，否则拼接
    const explicit = env[`${space.toUpperCase()}_URL`];
    const base =
      explicit ||
      `https://${env.SPACE_OWNER}-${SPACE_REPOS[space]}.hf.space`;
    const target = `${base}${path}`;

    try {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        // 用自定义 header 传 NEXUS_API_KEY，下游 app auth() 读 X-Nexus-Key。
        // Authorization 留给 HF 层：私有 Space 的 HF Gateway 用 Bearer HF_TOKEN 鉴权，
        // 若也占 Authorization 会冲突（HF 层 401，进不到 app）。
        "X-Nexus-Key": `Bearer ${env.NEXUS_API_KEY}`,
        // 透传 request_id 给下游 Space，全链路同 rid 串联
        "X-Request-ID": rid,
      };
      // 私有 Space：HF 层需 Bearer HF_TOKEN，否则 HF Gateway 401。
      if (env.HF_TOKEN) headers["Authorization"] = `Bearer ${env.HF_TOKEN}`;
      const upstream = await fetch(target, {
        method: "POST",
        headers,
        body: JSON.stringify(body ?? {}),
        signal: AbortSignal.timeout(60_000),
      });
      const payload = await upstream.text();
      return new Response(payload, {
        status: upstream.status,
        headers: {
          "Content-Type": upstream.headers.get("Content-Type") ?? "application/json",
          "X-Request-ID": rid,
        },
      });
    } catch (e) {
      // 下游不可达（连接级，未生成内容）→ 可重试
      return errResponse(
        "downstream_unreachable",
        `downstream ${space} unreachable: ${String(e)}`,
        502,
        rid,
        true,
      );
    }
  },

  // Cron 触发器：周期保活，唤醒休眠的免费 Space。
  // 在 wrangler.toml 配 [[triggers]] crons。
  async scheduled(_event: ScheduledEvent, env: Record<string, string>, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(probeAllSpaces(env).then((r) => {
      console.log("[keepalive]", JSON.stringify(r));
    }));
  },
};

function json(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * 下游路径白名单（防 SSRF：挡住任意 path 透传到任意 URL/端点）。
 * 仅放行各下游 Space 已声明的端点。
 */
const ALLOWED_PATHS = new Set([
  "/execute",   // langgraph
  "/run",        // claude
  "/complete",   // codex
  "/health",     // 全 Space
]);

function isAllowedPath(path: string): boolean {
  if (typeof path !== "string" || path.length === 0) return false;
  // 必须 `/` 起头，挡住绝对 URL（//host）与相对回溯
  if (path[0] !== "/") return false;
  if (path.includes("..")) return false;
  return ALLOWED_PATHS.has(path);
}

/** 统一错误响应体（与各 Space 一致）：{error:{code,message,retryable,request_id}} */
function errResponse(code: string, message: string, status: number, rid: string, retryable = false): Response {
  return json({ error: { code, message, retryable, request_id: rid } }, status);
}

/** 鉴权校验，返回 Response 则表示失败需直接返回。 */
function requireAuth(req: Request, env: Record<string, string>, rid: string): Response | null {
  if (!env.NEXUS_API_KEY) return errResponse("config_error", "NEXUS_API_KEY not set on worker", 500, rid);
  const auth = req.headers.get("authorization") ?? "";
  if (auth !== `Bearer ${env.NEXUS_API_KEY}`) return errResponse("unauthorized", "unauthorized", 401, rid);
  return null;
}

function spaceBase(env: Record<string, string>, space: string): string {
  const explicit = env[`${space.toUpperCase()}_URL`];
  return explicit || `https://${env.SPACE_OWNER}-${SPACE_REPOS[space]}.hf.space`;
}

/** 探测所有下游 Space 的 /health。用了 hermes（也探测自身主控）。 */
async function probeAllSpaces(env: Record<string, string>): Promise<Record<string, string>> {
  const spaces = ["hermes", "langgraph", "claude", "codex"];
  const results: Record<string, string> = {};
  await Promise.all(
    spaces.map(async (s) => {
      const owner = env.SPACE_OWNER;
      const repo = s === "hermes" ? "hermes" : SPACE_REPOS[s];
      const base = env[`${s.toUpperCase()}_URL`] || `https://${owner}-${repo}.hf.space`;
      try {
        const headers: Record<string, string> = { "X-Nexus-Key": `Bearer ${env.NEXUS_API_KEY}` };
        if (env.HF_TOKEN) headers["Authorization"] = `Bearer ${env.HF_TOKEN}`;
        const r = await fetch(`${base}/health`, {
          headers,
          signal: AbortSignal.timeout(15_000),
        });
        results[s] = `ok:${r.status}`;
      } catch (e) {
        results[s] = `down:${String(e).slice(0, 60)}`;
      }
    }),
  );
  return results;
}
