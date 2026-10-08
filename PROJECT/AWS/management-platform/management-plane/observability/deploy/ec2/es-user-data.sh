#!/usr/bin/env bash
# Fresh RHEL 9 x86_64 hosts only. Terraform cloud-init runs this as root.
# No Docker, Podman, custom Java application or Terraform templates directory.
set -Eeuo pipefail
trap 'echo "ES provisioning failed at line ${LINENO}; inspect /var/log/cloud-init-output.log" >&2' ERR
[[ $(id -u) -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
source /etc/os-release
[[ "$ID" == rhel && "$VERSION_ID" == 9* && $(uname -m) == x86_64 ]] || {
  echo 'A RHEL 9 x86_64 AMI is required' >&2; exit 1;
}
: "${ES_VPC_CIDR:?Terraform must supply the VPC CIDR}"
# Keep this fixed to the previously used ES version; OS migration is not an ES upgrade.
ES_VERSION=8.19.4

dnf install -y curl ca-certificates python3 util-linux
exec 9>/run/platform-es-install.lock
flock -n 9 || { echo 'Another ES installer is running' >&2; exit 1; }
# IMDSv2 obtains the region for the regional SSM installer; no AWS CLI credentials required.
TOKEN=$(curl -fsS --retry 5 --connect-timeout 3 --max-time 10 -X PUT \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 300' http://169.254.169.254/latest/api/token)
REGION=$(curl -fsS --retry 5 --connect-timeout 3 --max-time 10 \
  -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
[[ "$REGION" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || { echo 'Invalid IMDS region' >&2; exit 1; }
if ! rpm -q amazon-ssm-agent >/dev/null 2>&1; then
  dnf install -y "https://s3.${REGION}.amazonaws.com/amazon-ssm-${REGION}/latest/linux_amd64/amazon-ssm-agent.rpm"
fi
systemctl enable --now amazon-ssm-agent

# Default-disabled repository prevents a routine OS update from upgrading Elasticsearch.
rpm --import https://artifacts.elastic.co/GPG-KEY-elasticsearch
cat > /etc/yum.repos.d/elasticsearch.repo <<'REPO'
[elasticsearch]
name=Elasticsearch 8.x
baseurl=https://artifacts.elastic.co/packages/8.x/yum
gpgcheck=1
gpgkey=https://artifacts.elastic.co/GPG-KEY-elasticsearch
enabled=0
autorefresh=1
type=rpm-md
REPO
if rpm -q elasticsearch >/dev/null 2>&1; then
  [[ $(rpm -q --qf '%{VERSION}' elasticsearch) == "$ES_VERSION" ]] || {
    echo 'Different ES version already installed; use a reviewed upgrade procedure' >&2; exit 1;
  }
else
  dnf install -y --enablerepo=elasticsearch "elasticsearch-${ES_VERSION}"
fi

printf 'vm.max_map_count=1048576\n' > /etc/sysctl.d/99-elasticsearch.conf
sysctl -p /etc/sysctl.d/99-elasticsearch.conf
install -d -o elasticsearch -g elasticsearch -m 0750 /var/lib/elasticsearch /var/log/elasticsearch
install -d -o root -g elasticsearch -m 2750 /etc/elasticsearch/jvm.options.d
# Keep previous private HTTP client contract; TLS/auth must be a separate coordinated change.
if [[ -f /etc/elasticsearch/elasticsearch.yml && ! -f /etc/elasticsearch/elasticsearch.yml.rpm-original ]]; then
  cp -p /etc/elasticsearch/elasticsearch.yml /etc/elasticsearch/elasticsearch.yml.rpm-original
fi
cat > /etc/elasticsearch/elasticsearch.yml <<'YAML'
cluster.name: platform-site
node.name: ${HOSTNAME}
path.data: /var/lib/elasticsearch
path.logs: /var/log/elasticsearch
network.host: 0.0.0.0
http.port: 9200
transport.host: 127.0.0.1
transport.port: 9300
discovery.type: single-node
xpack.security.enabled: false
xpack.security.autoconfiguration.enabled: false
xpack.security.http.ssl.enabled: false
xpack.security.transport.ssl.enabled: false
YAML
cat > /etc/elasticsearch/jvm.options.d/heap.options <<'JVM'
-Xms1g
-Xmx1g
JVM
chown root:elasticsearch /etc/elasticsearch/elasticsearch.yml /etc/elasticsearch/jvm.options.d/heap.options
chmod 0640 /etc/elasticsearch/elasticsearch.yml /etc/elasticsearch/jvm.options.d/heap.options
# Use the RPM-bundled JDK, independently of the business applications' JDK 21.
install -d -m 0755 /etc/systemd/system/elasticsearch.service.d
cat > /etc/systemd/system/elasticsearch.service.d/platform.conf <<'UNIT'
[Service]
Environment=ES_JAVA_HOME=/usr/share/elasticsearch/jdk
Environment=HOSTNAME=%H
LimitNOFILE=65535
LimitNPROC=4096
TimeoutStartSec=900
TimeoutStopSec=180
Restart=on-failure
RestartSec=10
UNIT
# Keep SELinux enabled; restore standard RPM path labels when restorecon is installed.
if command -v restorecon >/dev/null 2>&1; then
  restorecon -RF /etc/elasticsearch /var/lib/elasticsearch /var/log/elasticsearch
fi
# If the approved AMI enables firewalld, allow only the existing VPC source range.
# EC2 security group still further restricts ingress to the EKS source security group.
if systemctl is-active --quiet firewalld; then
  python3 -c 'import ipaddress,sys; assert ipaddress.ip_network(sys.argv[1]).version == 4' "$ES_VPC_CIDR"
  RULE="rule family=ipv4 source address=$ES_VPC_CIDR port port=9200 protocol=tcp accept"
  firewall-cmd --permanent --add-rich-rule="$RULE"
  firewall-cmd --reload
fi
systemctl daemon-reload
systemctl enable elasticsearch.service
systemctl restart elasticsearch.service
# Bounded health check; non-red status and a single node are required.
for attempt in $(seq 1 60); do
  if curl -fsS --max-time 8 'http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow&timeout=5s' \
    | python3 -c 'import json,sys; x=json.load(sys.stdin); sys.exit(0 if not x.get("timed_out") and x.get("status") in ("yellow","green") and x.get("number_of_nodes")==1 else 1)'; then
    echo 'ES_READY: native RHEL RPM service; HTTP 9200; single-node; version 8.19.4'
    exit 0
  fi
  sleep 5
done
systemctl status elasticsearch.service --no-pager || true
echo 'ES health check failed; inspect /var/log/elasticsearch and cloud-init-output.log' >&2
exit 1
