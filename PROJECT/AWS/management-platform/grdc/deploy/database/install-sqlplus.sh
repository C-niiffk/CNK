#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || exit 1
. /etc/os-release
[[ $ID == rhel && $VERSION_ID == 9.* && $(uname -m) == x86_64 ]] || exit 1
rpm --import https://yum.oracle.com/RPM-GPG-KEY-oracle-ol9
cat > /etc/yum.repos.d/management-instantclient.repo <<'REPO'
[management-instantclient]
name=Oracle Instant Client OL9 x86_64
enabled=0
baseurl=https://yum.oracle.com/repo/OracleLinux/OL9/oracle/instantclient/x86_64/
gpgcheck=1
gpgkey=https://yum.oracle.com/RPM-GPG-KEY-oracle-ol9
REPO
dnf install -y --enablerepo=management-instantclient oracle-instantclient19.27-basic oracle-instantclient19.27-sqlplus
install -d -m 755 /etc/management/oracle
cat > /etc/management/oracle/sqlnet.ora <<'CONF'
SQLNET.ENCRYPTION_CLIENT=REQUIRED
SQLNET.ENCRYPTION_TYPES_CLIENT=(AES256)
SQLNET.CRYPTO_CHECKSUM_CLIENT=REQUIRED
SQLNET.CRYPTO_CHECKSUM_TYPES_CLIENT=(SHA256)
CONF
/usr/lib/oracle/19.27/client64/bin/sqlplus -V
