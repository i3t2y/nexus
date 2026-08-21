# Nexus Gateway (Cloudflare Worker)

Space 间通信的统一入口：鉴权、路由、超时。详见 `docs/COMMUNICATION.md`。

## 部署

```bash
cd workers/gateway
npm install
npx wrangler secret put NEXUS_API_KEY      # 输入与各 Space 同一把 key
# 编辑 wrangler.toml 的 SPACE_OWNER
npx wrangler deploy
```

记下输出 URL，填到 Hermes Space 的 `GATEWAY_URL`。

## 本地调试

```bash
npm run dev   # 起 wrangler dev，可 curl http://localhost:8787
```
