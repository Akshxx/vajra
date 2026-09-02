# VAJRA Deployment on Fly.io

## Prerequisites

1. **Install flyctl**:
```bash
curl -L https://fly.io/install.sh | sh
```

2. **Login to Fly.io**:
```bash
fly auth login
```

## Deployment Steps

### 1. Launch the App (First Time Only)
```bash
cd /Users/akshayakumar/vajra
fly launch --no-deploy
```
- Select organization
- Choose region: **bom** (Mumbai) for Razorpay's Indian market
- Say **No** to deploy now (we'll configure secrets first)

### 2. Create PostgreSQL Database (Free 1GB)
```bash
fly pg create --name vajra-db --region bom
```
- Select configuration: **Development** (free 1GB)
- Note the connection string output

### 3. Create Redis (Choose One)

**Option A: Upstash (Free Tier - Recommended)**
```bash
# Create at https://console.upstash.com/redis
# Free: 10k requests/day, 256MB
# Copy UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN
```

**Option B: Fly Redis (Paid)**
```bash
fly redis create --name vajra-redis --region bom
```

### 4. Set Secrets (Required)
```bash
# Groq API Key (for LLM)
fly secrets set GROQ_API_KEY=gsk_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

# Database URL (from fly pg create output)
fly secrets set DATABASE_URL="postgresql+asyncpg://user:pass@vajra-db.internal:5432/vajra"

# Redis URL (Upstash or Fly Redis)
fly secrets set REDIS_URL="redis://default:password@vajra-redis.upstash.io:6379"

# Optional: OpenAI/Anthropic fallback
fly secrets set OPENAI_API_KEY=sk-...
fly secrets set ANTHROPIC_API_KEY=sk-...

# Optional: Observability
fly secrets set OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
```

### 5. Deploy
```bash
fly deploy
```

### 6. Verify Deployment
```bash
# Check status
fly status

# View logs
fly logs

# Check health
curl https://vajra.fly.dev/api/v1/health

# Open in browser
fly open
```

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | ✅ Yes | Groq API key for LLM |
| `DATABASE_URL` | ✅ Yes | PostgreSQL connection string |
| `REDIS_URL` | ✅ Yes | Redis connection string |
| `OPENAI_API_KEY` | No | Fallback LLM |
| `ANTHROPIC_API_KEY` | No | Fallback LLM |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | OpenTelemetry collector |
| `ENVIRONMENT` | Auto | Set to "production" |
| `LOG_LEVEL` | Auto | "INFO" |

## Scaling & Monitoring

### Scale Resources
```bash
# Scale to 2 VMs for HA
fly scale count 2

# Increase memory (if needed)
fly scale memory 1024
```

### View Metrics
```bash
fly dashboard
# Opens Fly.io dashboard with CPU, Memory, Network, Requests
```

### View Logs
```bash
# Live logs
fly logs -f

# Recent logs
fly logs -n 100
```

### Custom Domain
```bash
fly certs add yourdomain.com
fly certs add www.yourdomain.com
# Add CNAME record pointing to vajra.fly.dev
```

## Database Migrations
```bash
# Run migrations (if using alembic)
fly ssh console -C "python -m alembic upgrade head"

# Or connect to DB directly
fly pg connect -a vajra-db
```

## Backup Strategy
```bash
# Manual backup
fly pg dump -a vajra-db > backup_$(date +%Y%m%d).sql

# Automated: Use Fly.io scheduled machines or pg_dump cron
```

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| `fly deploy` fails | Check `fly logs` for error details |
| Database connection fails | Verify `DATABASE_URL` format and `fly pg connect` |
| Redis connection fails | Check `REDIS_URL` format and network access |
| Out of memory | Increase `fly scale memory 1024` |
| Cold starts | Set `auto_stop_machines = false` in fly.toml |

### Debug Commands
```bash
# SSH into running container
fly ssh console

# Check environment variables
fly ssh console -C "env | grep -E 'GROQ|DATABASE|REDIS'"

# Test database connection
fly ssh console -C "python -c 'from vajra.core.database import init_db; import asyncio; asyncio.run(init_db())'"

# Test Groq API
fly ssh console -C "python -c 'from vajra.core.llm import get_llm_client; import asyncio; c=asyncio.run(get_llm_client().chat(\"test\")); print(c)'"
```

## Cost Monitoring
```bash
# Check current usage
fly dashboard

# Set billing alerts in Fly.io dashboard
# Settings → Billing → Alerts
```

## CI/CD Integration

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every push. To add auto-deploy:

1. Add `FLY_API_TOKEN` as GitHub Secret:
   ```bash
   fly auth token  # Copy output
   # Add as GitHub Secret: FLY_API_TOKEN
   ```

2. Add deploy job to `.github/workflows/ci.yml`:
   ```yaml
   deploy-fly:
     needs: [lint-and-typecheck, unit-tests, eval-harness, integration-tests]
     if: github.event_name == 'push' && github.ref == 'refs/heads/main'
     runs-on: ubuntu-latest
     steps:
       - uses: actions/checkout@v4
       - uses: superfly/flyctl-actions/setup-flyctl@master
       - run: fly deploy --remote-only
         env:
           FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
   ```

## Security Checklist

- [ ] All secrets set via `fly secrets` (not in code/env files)
- [ ] Non-root user in Dockerfile
- [ ] Health checks configured
- [ ] HTTPS enforced (`force_https = true`)
- [ ] Database TLS enabled (Fly.io default)
- [ ] Redis TLS enabled (Upstash default)
- [ ] Rate limiting on API endpoints (add if needed)
- [ ] CORS configured for your frontend domain

## Quick Reference Card

```bash
# Deploy
fly deploy

# Logs
fly logs -f

# SSH into container
fly ssh console

# Restart app
fly apps restart vajra

# Scale
fly scale count 2

# Secrets
fly secrets list
fly secrets set KEY=value
fly secrets unset KEY

# Database
fly pg connect -a vajra-db
fly pg dump -a vajra-db > backup.sql

# Scale resources
fly scale memory 1024
fly scale count 2
```

## Support

- Fly.io Docs: https://fly.io/docs/
- Fly.io Community: https://community.fly.io/
- VAJRA Issues: https://github.com/Akshxx/vajra/issues
