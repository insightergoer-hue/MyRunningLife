# Garmin 自动同步

把 Forerunner 965 → Garmin Connect 的数据，拉到本仓库 `plans/garmin/`，供教练（Cursor Agent）读取。

## 架构（2026-08-31 起）

| 工作流 | 作用 |
|--------|------|
| `garmin-sync-reusable.yml` | 核心：拉 Garmin + `git-auto-commit-action` 提交 |
| `garmin-sync.yml` | 定时（每天上午 1 次）+ 手动 `workflow_dispatch` |

## GitHub Actions 配置

### Secrets

| Name | Value |
|------|--------|
| `GARMIN_EMAIL` | Garmin 登录邮箱 |
| `GARMIN_PASSWORD` | 密码 |
| `GARMIN_IS_CN` | `true`（可选；默认国内站） |

### 定时（北京时间）

- **09:00** — 每日一次自动同步（赶在 10:05 训练提醒前）
- GitHub cron 可能延迟；缺数据时到 Actions 手动 **Run workflow**

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

- **自动**：**09:00** 同步写入 → Cloud Agent **10:05** 提醒读 `plans/garmin/daily/`
- **手动**：同步后说「Garmin 已同步」或问「今天练什么」
- 膝/踝主观分仍需你补充
- 晨间若缺数据：Actions 手动跑一次，或本机 `sync_garmin.py --today`
