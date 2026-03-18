# Zscaler URL Checker - K-12 Edition

See the main `CLAUDE.md` in the project root for full documentation.

## Quick Reference

- **Main project docs**: `../CLAUDE.md`
- **Deployment guide**: `../DEPLOYMENT.md`
- **Never commit**: `zscaler_checker_config.json`, `auth_config.json`, `service-account.json`
- **Rate limits**: Always `time.sleep(1)` between sequential Zscaler API calls
- **Auth decorators**: `@login_required` for user routes, `@admin_required` for settings/config routes
- **Docker rebuild**: `docker compose up -d --build`
