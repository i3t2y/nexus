#!/usr/bin/env bash
# Telegram CF Worker 透传 502 根因确诊(★2026-08-08 K-R7 后续)
#
# 目标:证实 502 是 CF Worker fetch upstream long-polling 触发(CF 平台限制) 而非 worker 代码 bug。
# 证据:worker 源码已贴(正则+白名单+透传逻辑无 bug),本地假 token 403(worker JS 活),
#   HF 启动日志 502,long polling getUpdates timeout=30s。
# 假设:短请求 getMe(瞬时返) 通 → long polling getUpdates timeout=30(等 30s)502 = CF fetch 长等待被限。
#
# 用法:export TG_TOKEN=<你的真 bot token>,然后 bash scripts/diag-telegram-worker.sh
#       token 仅在此脚本本地 curl 用,不进 repo 不进 curl 历史(去掉 -v 避落 HISTFILE)。
# 你跑完贴响应码即可,真 token 不回显。
#
# 安全:真 token 不入 chat/不进 git。脚本只打印 HTTP code + 耗时 + body 前 200 字符。

set -u
[ -z "${TG_TOKEN:-}" ] && { echo "ERR: export TG_TOKEN=<bot token> 先"; exit 1; }
WORKER="https://tele.nexush.cc.cd"
UP="https://api.telegram.org"

echo "=== 1. 短请求 getMe(worker 反代)应 200/401 瞬返 ==="
code=$(curl -sS -o /tmp/tg-resp.json -w "%{http_code} t=%{time_total}s" --max-time 15 "$WORKER/bot$TG_TOKEN/getMe")
echo "worker getMe: $code"
echo "body(前 200): $(head -c 200 /tmp/tg-resp.json)"
echo ""

echo "=== 2. 短请求 getMe(直原生 api.telegram.org)对照,应 401 unauthorized ==="
code=$(curl -sS -o /tmp/tg-resp.json -w "%{http_code} t=%{time_total}s" --max-time 15 "$UP/bot$TG_TOKEN/getMe")
echo "upstream getMe: $code"
echo "body(前 200): $(head -c 200 /tmp/tg-resp.json)"
echo ""

echo "=== 3. 关键:long polling getUpdates timeout=30(worker 反代)应 502 若假设成立 ==="
echo "  (此测试最长 ~32s,long polling 等满 30s 或收到 update 即返)"
code=$(curl -sS -o /tmp/tg-resp.json -w "%{http_code} t=%{time_total}s" --max-time 40 "$WORKER/bot$TG_TOKEN/getUpdates?timeout=30&offset=-1&limit=1")
echo "worker getUpdates(30s): $code"
echo "body(前 300): $(head -c 300 /tmp/tg-resp.json)"
echo ""

echo "=== 4. 对照:long polling 直 upstream(应 200 但等 30s) ==="
code=$(curl -sS -o /tmp/tg-resp.json -w "%{http_code} t=%{time_total}s" --max-time 40 "$UP/bot$TG_TOKEN/getUpdates?timeout=30&offset=-1&limit=1")
echo "upstream getUpdates(30s): $code"
echo "body(前 300): $(head -c 300 /tmp/tg-resp.json)"
echo ""

echo "=== 5. 短 polling getUpdates timeout=0(worker 反代,瞬时返)能否通 ==="
code=$(curl -sS -o /tmp/tg-resp.json -w "%{http_code} t=%{time_total}s" --max-time 15 "$WORKER/bot$TG_TOKEN/getUpdates?timeout=0&offset=-1&limit=1")
echo "worker getUpdates(timeout=0): $code"
echo "body(前 300): $(head -c 300 /tmp/tg-resp.json)"
echo ""
echo "=== 解读 ==="
echo "3: 502 = CF 长等待限(主因无疑);200 = worker 长轮询通(502 另因)"
echo "5: 200 = worker 短轮询通 → 改 hermes short polling 或 webhook 可解"
rm -f /tmp/tg-resp.json
