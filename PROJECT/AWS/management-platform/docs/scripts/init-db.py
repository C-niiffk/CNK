#!/usr/bin/env python3
"""Execute repository SQL through SQL*Plus on App2 A.
No Java initializer; no database secrets in command-line arguments.
"""

import argparse
import bcrypt
import json
import os
import pathlib
import re
import shlex
import tempfile
import uuid

from remote import connection, run, aws


ROOT = pathlib.Path(__file__).resolve().parents[2]
WORK = ROOT / ".ha-work"

parser = argparse.ArgumentParser()
parser.add_argument("--key", required=True)
args = parser.parse_args()

deployment = json.loads((WORK / "deployment.json").read_text())
runtime = json.loads((WORK / "runtime.json").read_text())

os.environ["AWS_DEFAULT_REGION"] = deployment["region"]

# Reject invalid credentials before creating SQL files or connecting remotely.
for key in ('MGMT_DB_PASSWORD', 'APP2_DB_PASSWORD', 'GRDC_A_DB_PASSWORD', 'GRDC_B_DB_PASSWORD'):
    value = runtime.get(key)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{24,30}", value):
        raise SystemExit(key + ": expected 24-30 ASCII alphanumeric characters; repair credentials first")

authoritative = json.loads(aws(
    "secretsmanager", "get-secret-value",
    "--secret-id", deployment["runtime_secret_arn"]
)["SecretString"])
for key in ('MGMT_DB_PASSWORD', 'APP2_DB_PASSWORD', 'GRDC_A_DB_PASSWORD', 'GRDC_B_DB_PASSWORD'):
    if runtime[key] != authoritative.get(key):
        raise SystemExit(key + ": local runtime differs from Secrets Manager; run deploy.py secrets")

master = json.loads(
    aws(
        "secretsmanager",
        "get-secret-value",
        "--secret-id",
        deployment["db"]["management"]["secret_arn"],
    )["SecretString"]
)

endpoint = deployment["db"]["management"]
dsn = (
    f"//{endpoint['host']}:"
    f"{endpoint['port']}/"
    f"{endpoint['service']}"
)


def connect(username, password):
    """Build a SQL*Plus connection command."""
    if any(char in password for char in '"\r\n\0'):
        raise SystemExit(
            "SQL*Plus connection password contains unsupported characters"
        )

    return (
        "\nSET DEFINE OFF\n"
        f'CONNECT {username}/"{password}"@"{dsn}"\n'
    )


def source(relative_path, values=None):
    """
    Read a repository SQL file and replace SQL*Plus variables.

    The SQL files already surround password variables with single quotes.
    Therefore, escape embedded quotes but do not add another quote pair.
    """
    if values is None:
        values = {}

    path = ROOT / relative_path
    content = path.read_text()

    for key, value in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise SystemExit(
                f"Invalid SQL substitution variable: {key}"
            )

        if not isinstance(value, str):
            raise SystemExit(
                f"SQL substitution value for {key} must be a string"
            )

        if "\0" in value:
            raise SystemExit(
                f"SQL substitution value for {key} contains a NUL character"
            )

        escaped_value = value.replace("'", "''")

        # Remove the ACCEPT prompt because this script runs noninteractively.
        content, _ = re.subn(
            rf"^ACCEPT\s+{re.escape(key)}\b.*$",
            f"-- {key} supplied by init-db.py",
            content,
            flags=re.MULTILINE | re.IGNORECASE,
        )

        # Replace the variable only; preserve quotes already in the SQL file.
        content = re.sub(
            rf"&{re.escape(key)}\b",
            lambda _match: escaped_value,
            content,
        )

        if re.search(rf"&{re.escape(key)}\b", content):
            raise SystemExit(
                f"SQL substitution failed for {key} in {relative_path}"
            )

    return content + "\n"


header = (
    "SET ECHO OFF\n"
    "SET VERIFY OFF\n"
    "SET SERVEROUTPUT ON\n"
    "WHENEVER OSERROR EXIT FAILURE ROLLBACK\n"
    "WHENEVER SQLERROR EXIT FAILURE ROLLBACK\n"
)

sql = header

# STEP 1: Create management database users.
sql += connect(master["username"], master["password"])
sql += "PROMPT === STEP 1: MANAGEMENT USERS ===\n"
sql += source(
    "management-plane/management-server/deploy/database/01-create-users.sql",
    {
        "MGMT_DB_PASSWORD": runtime["MGMT_DB_PASSWORD"],
        "APP2_DB_PASSWORD": runtime["APP2_DB_PASSWORD"],
    },
)

# STEP 2: Create GRDC database users.
sql += "PROMPT === STEP 2: GRDC USERS ===\n"
sql += source(
    "grdc/deploy/database/01-create-users.sql",
    {
        "GRDC_A_PASSWORD": runtime["GRDC_A_DB_PASSWORD"],
        "GRDC_B_PASSWORD": runtime["GRDC_B_DB_PASSWORD"],
    },
)

# Generate the BCrypt hash for the existing Nacos runtime password.
nacos_hash = bcrypt.hashpw(
    runtime["NACOS_PASSWORD"].encode(),
    bcrypt.gensalt(rounds=12, prefix=b"2a"),
).decode()

# STEP 3 and STEP 4: Initialize both GRDC/Nacos databases.
for site in ("A", "B"):
    sql += f"PROMPT === STEP 3: GRDC {site} SCHEMA ===\n"
    sql += connect(
        "MP_GRDC" + site,
        runtime[f"GRDC_{site}_DB_PASSWORD"],
    )
    sql += source(
        "grdc/deploy/database/02-nacos-schema.sql"
    )

    sql += f"PROMPT === STEP 4: GRDC {site} INITIAL DATA ===\n"
    sql += source(
        "grdc/deploy/database/03-init-data.sql",
        {"NACOS_BCRYPT": nacos_hash},
    )

# STEP 5: Initialize the APP2 schema.
sql += "PROMPT === STEP 5: APP2 SCHEMA ===\n"
sql += connect("MP_APP2", runtime["APP2_DB_PASSWORD"])

schema = source(
    "application-plane/app-service/deploy/database/app2-schema.sql"
)

# Keep the original idempotent behavior: ignore ORA-00955 only.
for statement in schema.split(";"):
    statement = statement.strip()

    if not statement:
        continue

    sql += (
        "BEGIN\n"
        "  EXECUTE IMMEDIATE q'~"
        + statement
        + "~';\n"
        "EXCEPTION\n"
        "  WHEN OTHERS THEN\n"
        "    IF SQLCODE != -955 THEN RAISE; END IF;\n"
        "END;\n"
        "/\n"
    )

sql += (
    "SELECT COUNT(*) AS APP2_TABLE_READY "
    "FROM user_tables WHERE table_name='APP2_JOB';\n"
    "PROMPT SQL_INITIALIZATION_OK\n"
    "EXIT SUCCESS\n"
)

# Use the existing SSM/SSH deployment workflow.
ssh_options, target = connection(
    deployment,
    WORK,
    pathlib.Path(args.key).resolve(),
    "a",
)

remote_dir = "/home/ec2-user/mp-sql-" + uuid.uuid4().hex

run([
    "ssh",
    *ssh_options,
    target,
    "umask 077; mkdir " + shlex.quote(remote_dir),
])

try:
    with tempfile.TemporaryDirectory(dir=WORK) as temp_dir:
        local_sql = pathlib.Path(temp_dir) / "init.sql"
        local_sql.write_text(sql)
        local_sql.chmod(0o600)

        run([
            "scp",
            *ssh_options,
            str(local_sql),
            str(ROOT / "grdc/deploy/database/install-sqlplus.sh"),
            target + ":" + remote_dir + "/",
        ])

    run([
        "ssh",
        *ssh_options,
        target,
        "sudo bash "
        + shlex.quote(remote_dir + "/install-sqlplus.sh"),
    ])

    run([
        "ssh",
        *ssh_options,
        target,
        "TNS_ADMIN=/etc/management/oracle "
        "/usr/lib/oracle/19.27/client64/bin/sqlplus "
        "-S -L /nolog @"
        + remote_dir
        + "/init.sql",
    ])

finally:
    run([
        "ssh",
        *ssh_options,
        target,
        "rm -rf -- " + shlex.quote(remote_dir),
    ])

print(
    "SQL initialized; management tables will be created "
    "by Flyway at management-server startup."
)
