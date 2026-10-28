#!/usr/bin/env bash
# Read-only production snapshot for load/hang diagnosis (no writes, no restarts).
# Run from the laptop:
#   ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 bash -s < scripts/audit/prod_snapshot.sh
# Prints: load/memory, gunicorn workers, perf-relevant env keys (secrets masked),
# Redis state, active PostgreSQL backends and queries, planner settings,
# pg_stat_statements top queries (if installed), analyze state of the big relations,
# recent service-journal lines, and the web-server log location.
set +e
echo "=== NOW $(date -u +%FT%TZ) ==="; uptime; free -m | head -2; echo "cores: $(nproc)"
echo "=== service / gunicorn ==="
systemctl show ilcf-web -p ActiveState -p ExecStart --no-pager | cut -c1-500
ps -o pid,ppid,%cpu,%mem,rss,etime,args -C gunicorn --sort=-%cpu | head -20
echo "=== env (perf-relevant keys only, secrets masked) ==="
grep -E "WORKER|TIMEOUT|REDIS|PREWARM|CACHE|GLOBAL_DATA|GUNICORN|THREADS" /srv/illinois_campaign_finance/shared/.env | sed -E 's/(PASSWORD|SECRET|KEY|TOKEN)=.*/\1=***/; s#(redis://[^@]*@)#\1***#'
if command -v redis-cli >/dev/null; then echo "redis: ping=$(redis-cli ping 2>&1) dbsize=$(redis-cli dbsize 2>&1) $(redis-cli info memory 2>/dev/null | grep -E '^used_memory_human')"; else echo "redis-cli: not installed"; fi
PSQL="sudo -u postgres psql -d ilcf -X -At -F |"
echo "=== pg: backends ==="
$PSQL -c "SELECT count(*) FILTER (WHERE state='active') AS active, count(*) AS total FROM pg_stat_activity WHERE datname='ilcf'"
$PSQL -c "SELECT to_char(now()-query_start,'HH24:MI:SS') AS age, state, coalesce(wait_event_type,'-'), left(regexp_replace(query, E'\\s+', ' ', 'g'), 170) FROM pg_stat_activity WHERE datname='ilcf' AND state <> 'idle' AND pid <> pg_backend_pid() ORDER BY query_start LIMIT 30"
echo "=== pg: settings ==="
$PSQL -c "SELECT name, setting, unit FROM pg_settings WHERE name IN ('shared_buffers','work_mem','effective_cache_size','max_connections','max_parallel_workers_per_gather','jit','random_page_cost','statement_timeout','autovacuum_naptime','autovacuum_max_workers')"
echo "=== pg: pg_stat_statements (top 25 by total time; error here just means it is not installed) ==="
$PSQL -c "SELECT calls, round(total_exec_time/1000) AS total_s, round(mean_exec_time) AS mean_ms, round(max_exec_time) AS max_ms, left(regexp_replace(query, E'\\s+', ' ', 'g'), 150) FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 25" 2>&1 | head -30
echo "=== pg: analyze state of relations over 200k rows (tables + matviews) ==="
$PSQL -c "SELECT s.relname, c.relkind, s.n_live_tup, s.n_mod_since_analyze, coalesce(to_char(s.last_analyze,'MM-DD HH24:MI'),'-') AS last_analyze, coalesce(to_char(s.last_autoanalyze,'MM-DD HH24:MI'),'-') AS last_autoanalyze FROM pg_stat_user_tables s JOIN pg_class c ON c.oid = s.relid WHERE s.n_live_tup > 200000 ORDER BY s.n_live_tup DESC LIMIT 25"
echo "=== journal: last 6h timeout/boot/error/worker lines ==="
journalctl -u ilcf-web --since "-6h" --no-pager 2>/dev/null | grep -i -E "timeout|boot|critical|error|worker" | tail -n 25
echo "=== web server logs ==="
ls -la /var/log/caddy/ 2>/dev/null | head; grep -n -i -E "^\s*log|output file" /etc/caddy/Caddyfile 2>/dev/null | head -5
echo "=== END ==="
