#!/usr/bin/env bash
# Read-only: sample active PostgreSQL backends 6 times, 10 s apart, separating query leaders
# from parallel workers, so the number of real concurrent requests behind the load is visible.
#   ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 bash -s < scripts/audit/prod_activity_sample.sh
set +e
PSQL="sudo -u postgres psql -d ilcf -X -At -F |"
for i in 1 2 3 4 5 6; do
  echo "=== sample $i $(date -u +%T) load=$(cut -d' ' -f1-3 /proc/loadavg) ==="
  $PSQL -c "SELECT backend_type, count(*) FROM pg_stat_activity WHERE datname='ilcf' AND state='active' AND pid <> pg_backend_pid() GROUP BY 1 ORDER BY 2 DESC"
  $PSQL -c "SELECT pid, coalesce(leader_pid::text,'leader') AS leader, to_char(now()-query_start,'MI:SS') AS age, coalesce(wait_event_type,'-') AS wait, coalesce(client_addr::text,'local') AS client, left(regexp_replace(query, E'\\s+', ' ', 'g'), 110) FROM pg_stat_activity WHERE datname='ilcf' AND state='active' AND backend_type='client backend' AND pid <> pg_backend_pid() ORDER BY query_start LIMIT 12"
  [ "$i" -lt 6 ] && sleep 10
done
echo "=== gunicorn worker CPU now ==="
ps -o pid,%cpu,rss,etime,args -C gunicorn --sort=-%cpu | head -5 | cut -c1-120
echo "=== END ==="
