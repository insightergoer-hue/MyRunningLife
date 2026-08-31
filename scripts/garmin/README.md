# Garmin 自动同步

把 Forerunner 965 → Garmin Connect 的数据，拉到本仓库 `plans/garmin/`，供教练（Cursor Agent）读取。

## 架构（2026-08-31 起）

| 工作流 | 作用 |
|--------|------|
| `garmin-sync-reusable.yml` | 核心：拉 Garmin + `git-auto-commit-action` 提交 |
| `garmin-sync.yml` | 定时（含 09:53/10:04 watchdog）+ 手动 `workflow_dispatch` |

**为何需要 watchdog 时段**：GitHub `schedule` 只承诺「尽力执行」，本仓库多次出现 **09:07 cron 拖到下午** 才跑。09:53 / 10:04 在提醒前 **无条件再拉一次**（覆盖写，无变化不 commit）。

> 注：watchdog 时段合并进 `garmin-sync.yml`（2026-08-31）。独立 `garmin-sync-watchdog.yml` 已移除——新 workflow 文件的 cron 可能 **24h 内不触发**。

## GitHub Actions 配置

### Secrets

| Name | Value |
|------|--------|
| `GARMIN_EMAIL` | Garmin 登录邮箱 |
| `GARMIN_PASSWORD` | 密码 |
| `GARMIN_IS_CN` | `true`（可选；默认国内站） |

### 定时（北京时间）

- **09:07 / 09:26 / 09:52 / 10:00** — 常规晨间
- **09:53 / 10:04** — **watchdog 强制同步**（提醒前保底）
- **12:13** — 上午兜底
- **18:07 / 22:07** — 赛后/晚间手表同步后再拉

手动：Actions → **Garmin daily sync** → Run workflow。

### 冒烟检查

```bash
bash scripts/garmin/check_freshness.sh              # 今天北京日期
bash scripts/garmin/check_freshness.sh 2026-08-31 8  # 指定日期、最早小时
```

## 本机同步（备选）

```bash
pip install -r scripts/garmin/requirements.txt
cp scripts/garmin/.env.example scripts/garmin/.env
python3 scripts/garmin/sync_garmin.py --today
```

## 和教练的配合

- **自动**：watchdog **10:04** 写入 → Cloud Agent **10:05** 提醒读 `plans/garmin/daily/`
- **手动**：同步后说「Garmin 已同步」或问「今天练什么」
- 膝/踝主观分仍需你补充
