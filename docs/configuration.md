# 配置说明

面向小白的建议：先只配最小项，跑通后再做进阶配置。

## 最小可用配置（推荐先用这个）

```env
X666_API_KEY=your_llm_key
GROQ_API_KEY=your_groq_key
```

- `X666_API_KEY`：用于总结
- `GROQ_API_KEY`：用于转写

## 常用配置

| 变量名 | 用途 | 默认值 |
| --- | --- | --- |
| `ASR_PROVIDER` | 转写模式 | `groq`（可选 `local_whisper`） |
| `GROQ_API_KEYS` | 多 Key 轮询（逗号分隔） | 空 |
| `BILVIEW_STORAGE_DIR` | 自定义存储目录 | 自动选择 |
| `DB_AUTO_INIT_ON_STARTUP` | 启动自动初始化 DB | `0` |

## 数据库后端（进阶）

默认是本地 SQLite。需要云端数据库时可选：

1. Cloudflare D1（优先级最高）
2. Turso

### Cloudflare D1

```env
CLOUDFLARE_ACCOUNT_ID=your_account_id
CLOUDFLARE_D1_DATABASE_ID=your_database_id
CLOUDFLARE_API_TOKEN=your_api_token
```

### Turso

```env
TURSO_DATABASE_URL=libsql://xxx.turso.io
TURSO_AUTH_TOKEN=your_turso_token
```

## 默认配置代码位置

如果你需要改默认值，请看 `config.py` 中的：

- `DEFAULT_LLM_API_URL`
- `DEFAULT_LLM_MODEL`
- `DB_PATH`
- `DOWNLOAD_DIR`
