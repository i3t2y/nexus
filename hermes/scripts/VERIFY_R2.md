# R2 快照 MANIFEST 循环验证清单

## 代码预证结论（2026-08-22）

### persist_to_r2.py（写端）
- `sync_once()`: 读 Neon 四表 → `snapshots/<ts>/<table>.json` 不可变 blob（PUT）→ MANIFEST.json（gen 递增、objects 指针指向 blob）
- 空快照保护：四表全空时 `objects` 为空 dict → `if objects:` 不写 MANIFEST（gen 不递增，不覆盖旧 MANIFEST）
- 半失败保护：某表读 Neon 异常 → `continue` 不进该表 objects，其余表正常写，MANIFEST 只含成功表
- SIGTERM 关机钩子：`_on_sigterm` → 当前周期结束 → `final flush` 补跑最后一轮

### restore_from_r2.py（读端）
- 指针循环：`_get_manifest()` → `manifest.objects[table].key` → `_get_snapshot_bytes(key)` → `_verify(sha256, bytes)` → `_restore_table(json.loads → Neon INSERT ... ON CONFLICT DO UPDATE)`
- 空快照保护：`rows=0` → 跳过写回（"空快照,跳过写回(防把表清空)"）
- 降级放行：无 MANIFEST / 无 objects 登记 / 无登记 sha256 → 降级放行（兼容旧快照格式）
- sha256 校验：实测与 MANIFEST 登记不符 → `verify_ok=False` → 拒绝写回
- `json.loads` 防护：2026-08-22 加固，损坏 blob→`None` 或 JSON 解析失败→安全返回

**结论：MANIFEST 指针闭环逻辑自洽，三个保护路径（空快照、sha256 不符、blob 损坏）均正确。**

---

## HF 侧现象验证（需用户观察）

下次 HF sonoke/h 启动后，观察 boot log 中以下关键行：

### 1. persist-r2 daemon 启动
```
[persist-r2] start, interval=1800s, bucket=nexus-checkpoints
[persist-r2] env diag={'R2_ENDPOINT': True, ...}
[persist-r2] Neon HTTP /sql connection OK: [{'ok': 1}]
```

### 2. 首张快照生成（30min 后）
```
[persist-r2] synced {'_gen': 1, '_snapshots_ts': '2026-08-...', 'agent_states': {'rows': N, 'sha256': '...', 'bytes': ...}, ...}
```
- `_gen: 1` → 首次成功
- `agent_states.rows: N` → Neon 四表此时应有数据（若仍为 0 说明 persist-neon 主路未写）

### 3. 恢复段验证（restore --list 或 restore --verify-only）
```
python restore_from_r2.py --list
```
应在容器内输出 MANIFEST 内容，例：
```json
{"gen": 1, "ts": "2026-08-...", "objects": {"agent_states": {"key": "snapshots/2026-08-.../agent_states.json", ...}}}
```

### 4. 正常 restore 空快照保护
若四表在 Neon 已有数据但 persist-r2 首张快照未生成（`snapshots/<ts>/` 还未写），
restore 从旧 `supabase-snapshot/` 降级读，可能返回：
```
[restore] FAIL table=agent_states rows=0 verify_ok=False msg=读取快照失败 (NoSuchKey): ...
```
→ 这是正常的（旧快照目录不存在），等 persist-r2 跑完首周期后 `restore --list` 确认。