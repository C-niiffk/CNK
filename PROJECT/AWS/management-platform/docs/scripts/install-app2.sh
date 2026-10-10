#!/usr/bin/env bash
# Invoke from the verified extracted bundle: sudo bash docs/scripts/install-app2.sh META SECRET SITE [--initialize-empty]
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
metadata=$(realpath "${1:?Deployment JSON required}")
secret=$(realpath "${2:?Secret JSON required}")
site=${3:?Site a or b required}
[[ "$site" == a || "$site" == b ]] || exit 1
initialize=${4:-}
bundle=$(cd "$(dirname "$0")/../.." && pwd)
cd "$bundle"
sha256sum -c SHA256SUMS >/dev/null
. /etc/os-release
[[ "$ID" == rhel && "$VERSION_ID" == 9.* ]] || { echo "This installer targets RHEL 9" >&2; exit 1; }
dnf install -y java-21-openjdk-headless podman python3 xfsprogs util-linux curl
java_version=$(java -version 2>&1)
[[ "$java_version" == *'"21.'* ]] || { echo "Select Java 21 with alternatives --config java" >&2; exit 1; }
[[ -x /usr/lib/systemd/system-generators/podman-system-generator ]] || {
 echo "A Podman version with Quadlet is required (RHEL 9.4+ recommended)" >&2; exit 1;
}
exec 9>/run/app2-deploy.lock
flock -n 9 || { echo "Another App2 deployment is running" >&2; exit 1; }
getent passwd app2 >/dev/null || useradd --system --home-dir /var/lib/management/app2 --shell /sbin/nologin app2
volume=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["app2"][sys.argv[2]]["volume_id"])' "$metadata" "$site")
bash docs/scripts/prepare-app2-volume.sh "$volume" "$initialize"
chown app2:app2 /var/lib/management/app2
chmod 750 /var/lib/management/app2
install -d -m 700 -o app2 -g app2 /var/lib/management/app2/db
install -d -m 750 -o app2 -g app2 /var/log/management/app2 /var/log/management/app2/skywalking
install -d -m 700 /var/lib/management/filebeat-a /var/lib/management/filebeat-b /etc/management
install -d -m 755 /opt/management/app2/releases /etc/containers/systemd
version=$(cat payload/VERSION)
[[ "$version" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || exit 1
release=/opt/management/app2/releases/$version
[[ ! -e "$release" ]] || { echo "Release $version already exists; choose a new immutable version" >&2; exit 1; }
mkdir "$release"
cp -R payload/app.jar payload/skywalking-agent "$release/"
chown -R root:root "$release"
chmod -R u=rwX,go=rX "$release"
# Pull before stopping Java, so registry failure does not interrupt the app.
podman pull docker.elastic.co/beats/filebeat:8.19.4
podman pull quay.io/prometheus/node-exporter:v1.9.1
previous=""
if [[ -L /opt/management/app2/current ]]; then previous=$(readlink -f /opt/management/app2/current); fi
if systemctl cat app2.service >/dev/null 2>&1; then
 systemctl stop app2.service
 if systemctl is-active --quiet app2.service; then echo "App2 writer did not stop" >&2; exit 1; fi
fi
# Retain previous H2 files for an explicit migration; they are never imported or deleted automatically.
python3 docs/scripts/configure-app2.py "$metadata" "$secret" "$site"
# Remove the uploaded copy after it is copied into the protected systemd env file.
rm -f "$secret"
install -m 644 application-plane/app-service/deploy/ec2/app2.service /etc/systemd/system/app2.service
if [[ -f /etc/containers/systemd/filebeat.container ]]; then
 systemctl stop filebeat.service
 rm /etc/containers/systemd/filebeat.container
fi
for destination in a b; do
 install -m 644 application-plane/agent-client/deploy/ec2/filebeat-$destination.container /etc/containers/systemd/filebeat-$destination.container
done
install -m 644 application-plane/agent-client/deploy/ec2/node-exporter.container /etc/containers/systemd/node-exporter.container
install -m 600 application-plane/agent-client/deploy/ec2/filebeat.yml /etc/management/filebeat.yml
ln -s "$release" /opt/management/app2/current.next
mv -Tf /opt/management/app2/current.next /opt/management/app2/current
if [[ -n "$previous" ]]; then printf '%s\n' "$previous" > /etc/management/app2-previous-release; fi
restorecon -R /opt/management/app2 /etc/management /var/log/management/app2 || true
if systemctl is-active --quiet firewalld; then
 vpc_cidr=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["vpc_cidr"])' "$metadata")
 for port in 8080 9100; do
  firewall-cmd --permanent --add-rich-rule="rule family=ipv4 source address=$vpc_cidr port port=$port protocol=tcp accept"
 done
 firewall-cmd --reload
fi
systemctl daemon-reload
# Quadlets use their [Install] WantedBy on generation; do not systemctl enable generated services.
systemctl restart filebeat-a.service filebeat-b.service node-exporter.service
systemctl enable --now app2.service
for attempt in $(seq 1 60); do
 if curl --fail --silent http://127.0.0.1:8080/actuator/health/readiness >/dev/null; then
  echo "App2 $version ready; Java is on the host, three monitoring containers run in Podman"; exit 0
 fi
 sleep 3
done
echo "App2 not ready. Inspect journalctl -u app2; no automatic data rollback performed." >&2
exit 1
