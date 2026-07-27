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
  path: string; // 如 /execute、/run、/complete
  body: unknown;
}

const SPACE_REPOS: Record<string, string> = { langgraph: "langgraph", claude: "claude-code", codex: "codex" };

export default {
  async fetch(req: Request, env: Record<string, string>): Promise<Response> {
    const url = new URL(req.url);

    if (url.pathname === "/health") {
      return json({ status: "ok", service: "nexus-gateway" });
    }

    if (url.pathname !== "/route") {
      return json({ error: "not found" }, 404);
    }
    if (req.method !== "POST") {
      return json({ error: "method not allowed" }, 405);
    }

    // 鉴权
    const auth = req.headers.get("authorization") ?? "";
    if (!env.NEXUS_API_KEY) {
      return json({ error: "NEXUS_API_KEY not set on worker" }, 500);
    }
    if (auth !== `Bearer ${env.NEXUS_API_KEY}`) {
      return json({ error: "unauthorized" }, 401);
    }

    const { space, path, body }: RouteBody = await req.json().catch(() => ({} as RouteBody));
    if (!space || !path || !SPACE_REPOS[space]) {
      return json({ error: "invalid space/path" }, 400);
    }

    // 下游 URL：显式 var 优先，否则拼接
    const explicit = env[`${space.toUpperCase()}_URL`];
    const base =
      explicit ||
      `https://${env.SPACE_OWNER}-${SPACE_REPOS[space]}.hf.space`;
    const target = `${base}${path}`;

    try {
      const upstream = await fetch(target, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.NEXUS_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body ?? {}),
        signal: AbortSignal.timeout(60_000),
      });
      const payload = await upstream.text();
      return new Response(payload, {
        status: upstream.status,
        headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
      });
    } catch (e) {
      return json({ error: `downstream ${space} unreachable`, detail: String(e) }, 502);
    }
  },
};

function json(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
