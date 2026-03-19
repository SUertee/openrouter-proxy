# Tokyo Proxy Setup

这个方案用于链路：

`上海应用 -> 东京代理 -> 国外模型 API`

## 1) 东京服务器启动代理

在东京机上设置环境变量：

```bash
export PROXY_TOKEN='replace-with-strong-token'
export UPSTREAM_BASE_URL='https://api.openai.com'
export UPSTREAM_API_KEY='sk-xxxx'
export UPSTREAM_TIMEOUT_SEC='120'
```

启动：

```bash
cd yili-ai-workspace
uvicorn scripts.tokyo_llm_proxy:app --host 0.0.0.0 --port 8787
```

建议在 Nginx/Caddy 层加 TLS，并限制来源 IP（只允许上海服务器访问）。

## 2) 上海应用调用东京代理

OpenAI Chat Completions 兼容调用地址示例：

`https://<tokyo-domain>/proxy/v1/chat/completions`

请求头：

- `X-Proxy-Token: <PROXY_TOKEN>`
- `Content-Type: application/json`

请求体保持 OpenAI 兼容格式即可。

## 3) 在上海做链路压测

使用脚本比较直连和代理：

```bash
cd yili-ai-workspace
python scripts/shanghai_proxy_smoketest.py \
  --model gpt-4.1-mini \
  --count 30 \
  --direct-url https://api.openai.com/v1/chat/completions \
  --direct-api-key <DIRECT_KEY> \
  --proxy-url https://<tokyo-domain>/proxy/v1/chat/completions \
  --proxy-token <PROXY_TOKEN>
```

## 4) 安全建议

- 每个环境使用不同 `PROXY_TOKEN`。
- 代理层不要打印完整请求体与密钥。
- 在反向代理层加速率限制和 WAF。
- 如需更强安全性，可改为 HMAC 时间戳签名（当前版本是 token 鉴权）。
