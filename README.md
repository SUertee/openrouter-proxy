# Tokyo OpenRouter Proxy Kit

目录用途：

- 东京代理：`tokyo_llm_proxy.py`
- 上海压测：`shanghai_proxy_smoketest.py`
- 用量检测：`openrouter_usage_check.py`

## 1) 东京服务器代理到 OpenRouter

先准备 `.env`：

```bash
cp .env.example .env
# 然后编辑 .env，填入你的真实 key/token
```

启动：

```bash
uvicorn tokyo_llm_proxy:app --host 0.0.0.0 --port 8787
```

上海调用地址：

`https://<tokyo-domain>/proxy/api/v1/chat/completions`

## 2) 上海侧请求示例

```bash
curl -X POST 'https://<tokyo-domain>/proxy/api/v1/chat/completions' \
  -H 'X-Proxy-Token: <PROXY_TOKEN>' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "openai/gpt-4.1-mini",
    "messages": [{"role":"user","content":"你好"}],
    "stream": false
  }'
```

## 3) 检测 OpenRouter 用量

```bash
python3 openrouter_usage_check.py
```

输出里重点看：

- `usage_daily`
- `usage_weekly`
- `usage_monthly`
- `limit_remaining`

额外说明：压测脚本会统计响应中的 `usage` 字段总 token（已自动加 `usage.include=true`）。

## 4) 直连 vs 东京代理压测

```bash
python3 shanghai_proxy_smoketest.py \
  --model openai/gpt-4.1-mini \
  --count 30
```

可选项：你也可以在命令行覆盖 `.env` 的值，例如 `--proxy-url`、`--proxy-token`。
