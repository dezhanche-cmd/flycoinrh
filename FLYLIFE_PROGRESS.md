# FlyLife 项目进度

> 基于 flycoinrh 改造的果蝇脑模拟器，删除链上逻辑，加随机生命周期
> 部署平台：Railway
> GitHub: https://github.com/dezhanche-cmd/flycoinrh (分支 flylife)

---

## 2026-09-15 晚间进度

### ✅ 今日已完成

| 项目 | 状态 | 说明 |
|------|------|------|
| 寿命参数调整 | ✅ | LIFESPAN_MIN_S=1s, LIFESPAN_MAX_S=1800s (30min) |
| NaN JSON 序列化修复 | ✅ | 新增 _safe_json() 函数，防止 NaN 导致 crash |
| Railway 自动部署 | ✅ | GitHub push 触发，无需手动操作 |
| 果蝇正常漫游 | ✅ | 日志显示持续访问 Wikipedia、Open Library 等 |

### ✅ 昨日已完成（已记录）

- Railway 项目创建，分支 flylife
- 预构建 graph.npz (36MB) 和 annotations.json (12MB)
- fetch_connectome.py 优先使用预构建文件，跳过运行时构建
- flyeye.py/roam.py NaN 防御
- curl 安装到 Dockerfile

### ❌ 昨日报错（今日已修复）

- `ValueError: Out of range float values are not JSON compliant: nan`
  - 原因：roam_state.json 序列化时，life_age_s 等浮点值为 NaN
  - 修复：添加 _safe_json() 递归处理，将 NaN/inf 转为 null

### 📊 Railway 状态

- 域名：flycoinrh-production-4392.up.railway.app
- 状态：ACTIVE
- 部署方式：GitHub push 自动触发
- 日志：实时显示 arrived: 和 lifespan 事件

### 📋 访问方式

- 网页：https://flycoinrh-production-4392.up.railway.app
- 端口：4660 (本地)
- 状态接口：/state
- 死亡日志：/deaths

### 🔧 关键代码变更

```
roam.py:
  - LIFESPAN_MIN_S = 1
  - LIFESPAN_MAX_S = 1800
  - 新增 _safe_json() 函数
  - json.dumps 调用处加 _safe_json() 包装
```

### 📋 明日待办

1. 观察 1s-30min 寿命下的果蝇行为日志
2. 检查死亡日志是否正常写入 build/deaths.json
3. 验证前端 DEATH LOG 区域正常显示

---

*最后更新：2026-09-15 16:30 GMT+8*
