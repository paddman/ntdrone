# Operations Runbook

## Start

```bash
cp .env.example .env
# edit SECRET_KEY, ADMIN_PASSWORD, database and integration settings
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 api worker
curl -fsS http://localhost:8000/healthz
```

Expected:

```json
{"status":"ok","service":"NT Drone Competition Portal","version":"0.1.0"}
```

## Stop

```bash
docker compose down
```

## Backup PostgreSQL

```bash
mkdir -p backups
docker compose exec -T db pg_dump -U ntdrone -d ntdrone -Fc > backups/ntdrone-$(date +%F-%H%M).dump
```

## Restore PostgreSQL

```bash
docker compose stop api worker
docker compose exec -T db dropdb -U ntdrone --if-exists ntdrone
docker compose exec -T db createdb -U ntdrone ntdrone
docker compose exec -T db pg_restore -U ntdrone -d ntdrone --clean --if-exists < backups/<file>.dump
docker compose start api worker
curl -fsS http://localhost:8000/healthz
```

## Check Worker

```bash
docker compose logs --since=30m worker
docker compose exec db psql -U ntdrone -d ntdrone -c \
  "select enabled, count(*) from vpn_credentials group by enabled;"
docker compose exec db psql -U ntdrone -d ntdrone -c \
  "select status, count(*) from simulation_runs group by status;"
```

## VPN Does Not Enable

1. Confirm team status:

```sql
select code, status from teams where code = '<TEAM_CODE>';
```

2. Confirm Booking is `CONFIRMED` and current time is inside Slot:

```sql
select b.status, s.starts_at, s.duration_minutes
from bookings b join slots s on s.id = b.slot_id
where b.team_id = '<TEAM_ID>';
```

3. Confirm Worker sees the credential:

```sql
select team_id, address, enabled, last_enabled_at, last_disabled_at
from vpn_credentials where team_id = '<TEAM_ID>';
```

4. For real WireGuard:

```bash
sudo wg show wg-ntdrone
sudo journalctl -u <vpn-controller-service> --since "30 minutes ago"
```

5. Safe rollback:

```bash
sudo wg set wg-ntdrone peer '<PUBLIC_KEY>' remove
```

## Simulation Stuck in RUNNING

```sql
select id, team_id, remote_run_id, status, started_at
from simulation_runs where status = 'RUNNING';
```

- Check remote Simulator logs and callback response
- Verify `SIMULATOR_API_TOKEN` matches both sides
- Send terminal callback `FAILED` with evidence in `result.summary`
- Do not delete the row; preserve Audit and troubleshooting evidence

## Rotate Simulator Token

1. Generate a new random token
2. Update Portal and Simulator secret stores
3. Restart Worker/API and Simulator in a controlled window
4. Verify Queue endpoint returns `401` with old token and works with new token
5. Record rotation in change log

## Rollback Application

```bash
git checkout <previous-tag-or-commit>
docker compose build api worker
docker compose up -d api worker
curl -fsS http://localhost:8000/healthz
```

Database migrations are not yet included in MVP. Before introducing schema-changing releases, add Alembic and require reversible migration scripts.
