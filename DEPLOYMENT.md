# Deployment Guide & Security Review

> ⚠️ **Partly historical.** The Docker / compose / Cloud Run options below were
> written for the aiogram + FastAPI app that used to live in `src/`; that app and
> its `Dockerfile` have been removed. The deployed bot is the single file
> `bot.py` (`Procfile` → `python bot.py`). The security, quota and
> troubleshooting sections still apply to it. The removed app lives on in
> `legacy-aiogram-app.zip`.

## Security Model

### Threat Mitigations

| Threat | Mitigation |
|--------|-----------|
| **Unauthorized channel access** | Role-based access control, authorized user IDs |
| **Token theft** | Fernet encryption at rest, secure storage |
| **Malicious files** | MIME type validation, file size limits, extension checks |
| **SSRF** | No arbitrary URL fetching, validated file sources |
| **Metadata injection** | Input sanitization, length limits, character validation |
| **Replayed OAuth callbacks** | State token validation, one-time use |
| **Duplicate publication** | Idempotency keys, job deduplication |
| **Abusive users** | Rate limiting, audit logging |
| **Leaked logs** | Structured logging, no secrets in output |

### Security Checklist

- [x] OAuth tokens encrypted at rest
- [x] No passwords stored
- [x] Role-based access control
- [x] Input validation on all user data
- [x] Rate limiting enabled
- [x] Audit logging for sensitive actions
- [x] Environment-based configuration
- [x] No secrets in source code
- [x] Idempotent operations
- [x] Error messages don't expose internals

### Production Security Recommendations

1. **HTTPS everywhere**: Use TLS for all endpoints
2. **Webhook validation**: Validate Telegram webhook signatures
3. **IP allowlisting**: Restrict admin endpoints
4. **Secret rotation**: Regularly rotate encryption keys
5. **Monitoring**: Set up alerts for suspicious activity
6. **Backup**: Regular encrypted backups of database
7. **Updates**: Keep dependencies updated
8. **Auditing**: Regular security audits

## Deployment Options

### Option 1: Docker on VPS

**Requirements:**
- VPS with Docker installed (4GB+ RAM recommended)
- Domain name with SSL certificate
- Google Cloud Project with YouTube API enabled

**Steps:**

```bash
# 1. Clone repository
git clone <repo-url>
cd youtube-telegram-bot

# 2. Configure environment
cp .env.example .env
# Edit .env with your credentials

# 3. Configure production settings
# Update docker-compose.prod.yml with:
# - Your domain
# - SSL certificates
# - Production database credentials

# 4. Build and deploy
docker-compose -f docker-compose.prod.yml build
docker-compose -f docker-compose.prod.yml up -d

# 5. Initialize database
docker-compose exec app alembic upgrade head

# 6. Configure Telegram webhook
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/webhook"}'
```

### Option 2: Google Cloud Run

**Requirements:**
- Google Cloud account
- Cloud SQL (PostgreSQL)
- Cloud Storage (S3-compatible)

**Steps:**

```bash
# 1. Set up Cloud SQL
gcloud sql instances create youtube-bot \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=us-central1

# 2. Create database
gcloud sql databases create youtube_bot --instance=youtube-bot

# 3. Configure environment variables in Cloud Run

# 4. Deploy
gcloud run deploy youtube-bot \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated
```

### Option 3: Railway/Render

1. Connect GitHub repository
2. Add PostgreSQL add-on
3. Add Redis add-on
4. Configure environment variables
5. Deploy automatically

## Production Configuration

### docker-compose.prod.yml

```yaml
version: '3.8'

services:
  app:
    build: .
    command: uvicorn src.main:app --host 0.0.0.0 --port 8000
    environment:
      - APP_ENV=production
      - APP_DEBUG=false
      - DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/youtube_bot
      # ... other env vars
    ports:
      - "8000:8000"
    restart: always
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '1.0'

  worker:
    build: .
    command: celery -A src.worker worker --loglevel=warning --concurrency=4
    environment:
      # Same as app
    restart: always
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '0.5'

  nginx:
    image: nginx:alpine
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./certs:/etc/nginx/certs
    depends_on:
      - app
```

### Nginx Configuration

```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    location / {
        proxy_pass http://app:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /webhook {
        proxy_pass http://app:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Monitoring & Observability

### Metrics to Monitor

1. **Application Metrics**
   - Upload success rate
   - Average upload time
   - Error rate by type
   - Queue depth

2. **Infrastructure Metrics**
   - CPU/memory usage
   - Database connections
   - Redis memory usage
   - Disk space

3. **Business Metrics**
   - Daily active users
   - Videos uploaded per day
   - YouTube quota usage
   - Failed uploads

### Logging

Structured JSON logging with:
- Request IDs
- User IDs (hashed)
- Timestamps
- Error stacks
- Performance metrics

### Alerts

Set up alerts for:
- Error rate > 5%
- Upload failure rate > 10%
- Queue depth > 100
- Database connection pool exhausted
- YouTube quota < 10% remaining

## Backup Strategy

### Database

```bash
# Daily backup
docker-compose exec postgres pg_dump -U postgres youtube_bot | gzip > backup_$(date +%Y%m%d).sql.gz

# Restore
gunzip -c backup_20240101.sql.gz | docker-compose exec -T postgres psql -U postgres youtube_bot
```

### Media Files

- S3 versioning enabled
- Cross-region replication
- Lifecycle policies for old files
- Regular restore tests

## YouTube Quota Management

### Quota Costs

| Operation | Cost (units) |
|-----------|--------------|
| Video upload (resumable) | 1,600 |
| Video update | 50 |
| Thumbnail upload | 50 |
| Playlist add | 50 |
| Channel list | 1-3 |

### Best Practices

1. **Cache channel data**: Don't fetch on every request
2. **Batch operations**: Combine API calls when possible
3. **Monitor usage**: Track quota consumption daily
4. **Respect limits**: Implement backoff on quota errors
5. **User quotas**: Consider per-user limits

### Quota Reset

YouTube quota resets at midnight Pacific Time (PT) daily.

## Troubleshooting

### Common Issues

1. **OAuth errors**
   - Verify redirect URI matches exactly
   - Check client ID/secret
   - Ensure YouTube API is enabled

2. **Upload failures**
   - Check file format support
   - Verify channel permissions
   - Monitor quota usage

3. **Token expiration**
   - Implement automatic refresh
   - Monitor refresh failures
   - Alert on repeated failures

4. **Performance issues**
   - Check worker concurrency
   - Monitor database connections
   - Verify Redis memory

### Debug Mode

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Run with verbose output
docker-compose logs -f app worker
```

## Compliance & Legal

### Data Retention

- User data: Retained while account is active
- Media files: Deleted after successful upload + 7 days
- Audit logs: Retained for 1 year
- OAuth tokens: Refreshed automatically, deleted on disconnect

### User Rights

- Users can view their data
- Users can request deletion
- Users can revoke access
- Users can export their data

### Copyright

- Bot does not claim ownership of uploaded content
- Users confirm they have rights to upload
- DMCA takedown process available
- YouTube's Content ID system applies

### Privacy

- No data sold to third parties
- Minimal data collection
- Transparent privacy policy
- GDPR/CCPA compliant

## Support

For issues:
1. Check documentation
2. Review logs
3. Open GitHub issue
4. Contact maintainers

---

**Last Updated**: 2024
**Version**: 1.0.0
