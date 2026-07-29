# ══════════════════════════════════════════════════════════════
# 骨架模板留档, 非 Nexus hermes 现役 Dockerfile
# 现役 hermes Dockerfile 见 spaces/hermes/Dockerfile (commit a142da9, 更简: 1 行 FROM ghcr ARG 占位 + 墓碑)
# 本件系 omn 血统通用骨架留档以示 ARG 三件定态 + /data 软链 + ENTRYPOINT start.sh 的原貌,
# 非当前 Nexus 集群任何组件的现役构建件。引用须对齐 a142da9 实际而非此模板。
# ══════════════════════════════════════════════════════════════
# Nexus 集群通用 Dockerfile — 三件定态, 永不再改业务逻辑
# 版本号全部驱逐为 ARG 默认值, 升级改 ARG 或 GHCR 推新 tag
# ══════════════════════════════════════════════════════════════

# ARG = build 期值 / HF Variable buildtime 覆盖 / 默认值兜底 三层优先级
# GHCR owner i3t2y 经 commit fe275ae 核对为真 owner,勿改
ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable
FROM ${BASE_IMAGE}
USER root

ARG BASE_IMAGE                                    # FROM 后重声明继承全局值(ARG 多 stage 作用域必需,非冗余)
ENV BASE_IMAGE=${BASE_IMAGE}                      # 转存 runtime env(修正项 commit fe275ae)

# ⚠️ 边界警示:litestream 仅懂 SQLite WAL,跑不起 Postgres。
# Nexus 主控 hermes 与 langgraph 用 Supabase PostgreSQL(litestream 读不了其物理 WAL),启即误配。
# 本 ARG 仅对 omniroute 等 SQLite 组件生效(OmniRoute 用 SQLite 故 litestream 正用)。
# Nexus hermes/langgraph 侧禁用;Nexus 备份正解=Supabase 自带 daily backup + PITR(Pro+ RPO 2min)/
# 免费档 supabase db dump 入 R2(supabase.com/docs/guides/platform/backups)。
ARG LITESTREAM_VERSION=0.5.9
ENV LITESTREAM_VERSION=${LITESTREAM_VERSION}

ARG HF_HUB_RANGE=">=1.0,<2.0"                    # 双引号包防注入
ENV HF_HUB_RANGE=${HF_HUB_RANGE}

# 组件名 (hermes/langgraph/omniroute/claude/codex)。
# 默认此处存 omniroute 为兼容 omn 血统(omniroute 是上游 OmniRoute 统一 AI 网关 v3.8.49 路径,非本组 Nexus 组件)。
# Nexus 现役主控实为 hermes Space(Python FastAPI langgraph/Gradio)+3 worker,裸构建应指 hermes 非 omniroute;
# 现役 hermes 已落 a142da9 自有简版 Dockerfile,本模板仅留骨架兼容注。
ARG NEXUS_COMPONENT=omniroute
ENV NEXUS_COMPONENT=${NEXUS_COMPONENT}

# /data 软链到 /app/data。
# ⚠️ Nexus 现役(a142da9)此 /data 由 Storage Bucket 提供 RW 挂载,非泛称"HF Space persistent volume":
#   HF 官文(manage-spaces Note 逐字)"Models,datasets,Spaces always mounted as read-only.
#   Only storage buckets support read-write mounts"——Bucket 唯一支持 RW,Dataset 强制只读。
#   运行态写件全去此 Bucket RW /data(日志/checkpoint/artifacts);Dataset RO 仅供逻辑层回退锚(见 start.sh)。
# 本模板件此行保留骨架原貌(ln -sfn 兜底),但用词对齐 Bucket RW 而非 persistent volume。
# 挂载形态选择:本模板走 hf download --local-dir + cp -a 写可写副本,与 omn-merge 现役一致(修正项④);
#   对比 audit 裁决的"Dataset Volume 只读挂载"为已准未施蓝图(audit:268"本轮勘探裁决闭环非实施")。
RUN mkdir -p /data /app/data && ln -sfn /data /app/data || true

COPY start.sh /start.sh
RUN chmod +x /start.sh

# EXPOSE 7860:HF Space 默认端口。omniroute 等 omn 血统组件默认 7860;
# Nexus 现役 hermes Space 实际端口策略见 spaces/hermes/* (a142da9)，本骨架件不展开组件级差异(三件定态有意省略)。
# start.sh 现役行为对齐 omn 血统:hf download bootstrap + cp -a 可写副本、entrypoint npm install 一次性写 /logic(修正项⑥)。
# ⚠️ fail-open vs 硬 exit 1:omn 血统 entrypoint 故意 fail-open WARN(版本不齐仍可跑，修正项⑤);
#   真硬 exit 1 仅在 gate.js PSK 缺失 + entrypoint npm install FATAL。Nexus 若要硬断言须自写，omn-merge 给反例。
EXPOSE 7860
ENTRYPOINT ["/start.sh"]
