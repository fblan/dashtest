.PHONY: seed dashboards up down reset psql logs check

seed:        ## regenerate simulated data (db/init/03_seed.sql)
	python3 generator/generate.py

dashboards:  ## regenerate Grafana dashboard JSON
	python3 grafana/build_dashboards.py

up:
	docker compose up -d --build

down:
	docker compose down

reset: seed dashboards  ## wipe volumes and reload everything from scratch
	docker compose down -v
	docker compose up -d --build

psql:
	docker compose exec postgres psql -U kitchen kitchen

logs:
	docker compose logs -f

check:       ## sanity checks on the loaded data
	docker compose exec -T postgres psql -U kitchen kitchen -c "SELECT count(*) AS cascade_violations FROM check_cascade" \
	  -c "SELECT platform, kms_version, count(*) AS campaigns, round(avg(pass_rate),1) AS avg_pass_rate FROM v_campaign_summary GROUP BY 1, 2, kms_version_order ORDER BY kms_version_order, 1" \
	  -c "SELECT platform, changes, name, count(*) FROM v_test_result WHERE transition = 'regression' GROUP BY 1, 2, 3 ORDER BY 2, 1, 3"
