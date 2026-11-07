# Deployment files

| File | Install to | Notes |
| --- | --- | --- |
| `airline-app.service` | `/etc/systemd/system/` | Runs `uvicorn eval_api:app` on 127.0.0.1:8100 |
| `airline-app.env.example` | `/etc/airline-app.env` (chmod 600) | Bedrock model ids, region, Langfuse keys |
| `Caddyfile` | Caddy config | HTTPS on sslip.io hostnames, basic auth; set your IP and a `caddy hash-password` hash |

Step-by-step: [docs/07-aws-deployment.md](../docs/07-aws-deployment.md).
