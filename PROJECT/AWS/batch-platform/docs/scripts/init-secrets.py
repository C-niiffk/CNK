#!/usr/bin/env python3
"""First provision only. Send secrets on stdin, never into Terraform state."""
import json
import os
from pathlib import Path
import secrets
import string
import subprocess


def main():
    os.chdir(Path(__file__).resolve().parents[2])
    arn = subprocess.check_output(
        ["terraform", "-chdir=terraform", "output", "-raw", "runtime_secret_arn"], text=True
    ).strip()
    versions = json.loads(subprocess.check_output([
        "aws", "--no-cli-pager", "secretsmanager", "list-secret-version-ids", "--secret-id", arn
    ], text=True))
    if versions.get("Versions"):
        raise SystemExit("Secret already has versions; rotation requires coordinated Oracle password changes.")

    alphabet = string.ascii_letters + string.digits
    values = {key: "".join(secrets.choice(alphabet) for _ in range(40))
              for key in ["owner_password", "reader_password", "internal_token", "ui_password"]}
    subprocess.run([
        "aws", "--no-cli-pager", "secretsmanager", "put-secret-value",
        "--secret-id", arn, "--secret-string", "file:///dev/stdin"
    ], input=json.dumps(values), text=True, check=True, stdout=subprocess.DEVNULL)
    print("Runtime secret initialized. Retrieve only ui_password for login.")


if __name__ == "__main__":
    main()
