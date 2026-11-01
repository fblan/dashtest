# Grafana 12 with the data source and the Kitchen QA dashboards built in.
# Dashboards live in /etc/grafana/dashboards (not in the /var/lib/grafana volume),
# so every deploy serves the dashboards from the image.
FROM grafana/grafana:12.4.14
COPY provisioning /etc/grafana/provisioning
COPY dashboards /etc/grafana/dashboards
