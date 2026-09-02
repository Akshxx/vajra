# VAJRA Deployment on Railway

## Prerequisites

1. **Install Railway CLI**:
```bash
npm install -g @railway/cli
# or
curl -fsSL https://railway.app/install.sh | sh
```

2. **Login to Railway**:
```bash
railway login
```

## Deployment Steps

### 1. Initialize Project (First Time Only)
```bash
cd /Users/akshayakumar/vajra
railway init
```
- Select "Create new project"
- Name it: `vajra`

### 2. Add PostgreSQL Database
```bash
railway add postgresql
```
- Railway provisions PostgreSQL automatically
- Note the `DATABASE_URL` variable that gets added

### 3. Add Redis
```bash
railway add redis
```
- Railway provisions Redis automatically
- Note the `REDIS_URL` variable that gets added

### 3. Set Required Secrets
```bash
# Groq API Key (for LLM)
railway variables set GROQ_API_KEY=gsk_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

# Optional: OpenAI/Anthropic fallback
railway variables set OPENAI_API_KEY=sk-...
railway variables set ANTHROPIC_API_KEY=sk-...

# Optional: Observability
railway variables set OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
```

### 4. Set Region (Optional - for Indian market)
```bash
railway environment set RAILWAY_REGION=mumbai
```

### 5. Deploy
```bash
railway up
```

### 6. Verify Deployment
```bash
# Check status
railway status

# View logs
railway logs

# Check health
curl https://vajra-production.up.railway.app/api/v1/health

# Open in browser
railway open
```

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | ✅ Yes | Groq API key for LLM |
| `DATABASE_URL` | ✅ Auto | PostgreSQL connection (auto-set by Railway) |
| `REDIS_URL` | ✅ Auto | Redis connection (auto-set by Railway) |
| `OPENAI_API_KEY` | No | Fallback LLM |
| `ANTHROPIC_API_KEY` | No | Fallback LLM |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | OpenTelemetry collector |
| `ENVIRONMENT` | Auto | Set to "production" |
| `LOG_LEVEL` | Auto | "INFO" |

## Railway Auto-Injects These Variables

| Variable | Source |
|----------|--------|
| `DATABASE_URL` | PostgreSQL service |
| `REDIS_URL` | Redis service |
| `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` | PostgreSQL |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` | Redis |

Your code should use `DATABASE_URL` and `REDIS_URL` directly.

## Scaling & Monitoring

### Scale Resources
```bash
# View current usage
railway usage

# Scale memory (if needed)
railway service scale --memory 1024
```

### View Logs
```bash
# Live logs
railway logs -f

# Recent logs
railway logs -n 100
```

### Custom Domain
```bash
railway domain add yourdomain.com
railway domain add www.yourdomain.com
# Add CNAME record pointing to your-app.up.railway.app
```

## Database Migrations
```bash
# Run migrations
railway run python -m alembic upgrade head

# Or connect to DB directly
railway connect postgresql
```

## Backup Strategy
```bash
# Manual backup
railway run pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql
```

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| `railway up` fails | Check `railway logs` for error details |
| Database connection fails | Verify `DATABASE_URL` format |
| Redis connection fails | Check `REDIS_URL` format |
| Out of memory | Increase memory limit in Railway dashboard |
| Build fails | Check `railway logs --build` |

### Debug Commands
```bash
# SSH into running container
railway shell

# Check environment variables
railway run env | grep -E 'GROQ|DATABASE|REDIS'

# Test database connection
railway run python -c "from vajra.core.database import init_db; import asyncio; asyncio.run(init_db())"

# Test Groq API
railway run python -c "from vajra.core.llm import get_llm_client; import asyncio; c=asyncio.run(get_llm_client().chat('test')); print(c)"
```

## Cost Monitoring

Railway provides $5 free credit/month. VAJRA typical usage:
- **VM**: ~$2-3/month (512MB RAM, shared CPU)
- **PostgreSQL**: ~$1-2/month (1GB)
- **Redis**: ~$1-2/month (256MB)
- **Total**: ~$5-7/month (within $5 credit for light usage)

Monitor at: https://railway.app/dashboard → Billing

## CI/CD Integration

Add `RAILWAY_TOKEN` as GitHub Secret:
```bash
railway token  # Copy output
# Add as GitHub Secret: RAILWAY_TOKEN
```

Add deploy job to `.github/workflows/ci.yml`:
```yaml
deploy-railway:
  needs: [lint-and-typecheck, unit-tests, eval-harness, integration-tests]
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: railwayapp/railway-action@v1
    - run: railway up --detach
      env:
        RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
```

## Security Checklist

- [ ] All secrets set via `railway variables set` (not in code/env files)
- [ ] Non-root user in Dockerfile
- [ ] Health checks configured
- [ ] HTTPS enforced (Railway default)
- [ ] Database TLS enabled (Railway default)
- [ ] Redis TLS enabled (Railway default)
- [ ] Rate limiting on API endpoints (add if needed)
- [ ] CORS configured for your frontend domain

## Quick Reference Card

```bash
# Deploy
railway up

# Logs
railway logs -f

# SSH into container
railway shell

# Restart app
railway service restart

# Variables
railway variables list
railway variables set KEY=value
railway variables delete KEY

# Database
railway connect postgresql
railway run pg_dump $DATABASE_URL > backup.sql

# Scale
railway service scale --memory 1024
```

## Support

- Railway Docs: https://docs.railway.app/
- Railway Discord: https://discord.gg/railway
- VAJRA Issues: https://github.com/Akshxx/vajra/issues