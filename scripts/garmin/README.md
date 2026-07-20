# Garmin 本机自动同步

把 Forerunner 965 → Garmin Connect 的数据，拉到本仓库 `plans/garmin/`，供教练（Cursor Agent）读取。

## 一次配置（电脑）

```bash
cd /path/to/MyRunningLife
python3 -m pip install -r scripts/garmin/requirements.txt
cp scripts/garmin/.env.example scripts/garmin/.env
# 编辑 .env：填 GARMIN_EMAIL / GARMIN_PASSWORD
# 若用国内 Garmin（garmin.cn）：GARMIN_IS_CN=true
```

首次登录（可能要输 MFA 验证码）：

```bash
python3 scripts/garmin/sync_garmin.py --today
```

成功后令牌缓存在 `GARMINTOKENS`（默认 `~/.garminconnect_zhangchaojie`），之后一般不用再输密码。

## 日常用法

```bash
# 默认同步「昨天」（Asia/Shanghai）
python3 scripts/garmin/sync_garmin.py

# 指定日期 / 最近 3 天
python3 scripts/garmin/sync_garmin.py --date 2026-07-16
python3 scripts/garmin/sync_garmin.py --days 3
```

输出：
- 可读摘要：`plans/garmin/daily/YYYY-MM-DD.md`（可进 git）
- 原始 JSON：`plans/garmin/raw/YYYY-MM-DD.json`（**仅本机**，已在 `.gitignore`，不上传）

## 自动跑 + 推送到 GitHub

1. `.env` 里设 `GARMIN_GIT_AUTO_PUSH=true`（脚本只 `git add/commit/push` 日摘要 md，不含 raw）
2. 或用系统定时任务只同步，你手动 push


### macOS（LaunchAgent 示例：每天 8:00）

```bash
# 编辑路径后保存到 ~/Library/LaunchAgents/com.myrunninglife.garmin.plist
```

也可用 cron：

```cron
0 8 * * * cd /path/to/MyRunningLife && /usr/bin/python3 scripts/garmin/sync_garmin.py >> /tmp/garmin_sync.log 2>&1
```

## 安全

- **不要**把 `.env`、token 目录、`plans/garmin/raw/*.json` 提交进 git（已在 `.gitignore`）
- 云端 Agent **不会**替你保存 Garmin 密码；同步在你本机完成


## 和教练的配合

同步并 push 后，在对话里说一声「Garmin 已同步」或直接问今日/昨日分析即可。主观疼感（膝/踝评分）仍需你口头或写在日摘要底部备注栏。
