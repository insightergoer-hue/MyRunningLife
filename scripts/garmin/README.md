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
- **自动（不必每次手动）**，北京时间大致：
  | 时间 | 拉什么 |
  |------|--------|
  | **08:00** | 昨天 + 今天（昨夜睡眠、晨起恢复） |
  | **14:00** | 昨天 + 今天（午前活动补拉） |
  | **22:30** | 昨天 + 今天（晚训后） |
  | **次日 00:30** | 昨天 + 今天（很晚结束的训练收尾） |
- 手动（偶尔补洞）：仓库 **Actions** → **Garmin daily sync** → **Run workflow**  
  - 默认建议 `mode=both` 或 `today`

成功后会自动 commit `plans/garmin/daily/*.md` 到 `main`。教练读取仓库文件即可，**仍不要把 Garmin 密码发到聊天**。

### 3. 注意

- 若以后打开 MFA，Actions 会失效，改回本机同步
- Garmin 偶发风控时看 Actions 日志；本机跑一次通常可恢复
- GitHub 定时任务可能有约 ±15–30 分钟漂移，属正常
- 刚结束运动后 Connect 有时要几分钟才出完整活动；若 22:30 那次还不全，00:30 会再补一次

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

同步进仓库后，说一声「Garmin 已同步」或直接问分析即可。膝/踝等主观评分仍需你补充。
