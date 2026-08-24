# Garmin 自动同步

把 Forerunner 965 → Garmin Connect 的数据，拉到本仓库 `plans/garmin/`，供教练（Cursor Agent）读取。

## GitHub Actions（推荐 · 未开 MFA）

### 1. 配置 Secrets

仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**：

| Name | Value |
|------|--------|
| `GARMIN_EMAIL` | Garmin 登录邮箱 |
| `GARMIN_PASSWORD` | 密码 |
| `GARMIN_IS_CN` | `true`（可选；不填时 workflow 默认国内站） |

密码只填在 GitHub 网页，**不要发到聊天或写进代码**。

### 2. 跑起来

- 工作流：`.github/workflows/garmin-sync.yml`
- 定时：每天 **北京时间 07:00** 自动同步 **今天 + 昨天**（昨夜恢复 + 前日完整活动）
- **08:00 训练提醒**依赖此同步；Garmin 须在前一晚完成手表同步
- 手动：仓库 **Actions** → **Garmin daily sync** → **Run workflow**（默认 mode=`morning`）

成功后会自动 commit `plans/garmin/daily/*.md` 到 `main`。

### 3. 注意

- 若以后打开 MFA，Actions 会失效，改回本机同步
- Garmin 偶发风控时看 Actions 日志；本机跑一次通常可恢复

---

## 本机同步（备选）

```bash
cd /path/to/MyRunningLife
python3 -m pip install -r scripts/garmin/requirements.txt
cp scripts/garmin/.env.example scripts/garmin/.env
# 编辑 .env：GARMIN_EMAIL / GARMIN_PASSWORD；国内站 GARMIN_IS_CN=true
python3 scripts/garmin/sync_garmin.py --today
```

日常：

```bash
python3 scripts/garmin/sync_garmin.py          # 昨天
python3 scripts/garmin/sync_garmin.py --days 3
```

输出：
- `plans/garmin/daily/YYYY-MM-DD.md`（可进 git）
- `plans/garmin/raw/YYYY-MM-DD.json`（仅本机，不进 git）

本机自动 push：`.env` 设 `GARMIN_GIT_AUTO_PUSH=true`。

## 安全

- 不要提交 `.env`、token、`plans/garmin/raw/*.json`
- 不要在聊天里发送 Garmin 密码

## 和教练的配合

- **自动**：GitHub Actions **07:00** 写入 `plans/garmin/daily/` → Cloud Agent **08:00** 每日提醒会读最新恢复数据
- **手动**：同步后进仓库说一声「Garmin 已同步」，或直接问「今天练什么 / 分析恢复」
- 膝/踝等主观评分仍需你补充
