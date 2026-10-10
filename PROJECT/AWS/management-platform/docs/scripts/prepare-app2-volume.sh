#!/usr/bin/env bash
# Only formats the explicitly identified, empty EBS volume, with an explicit flag.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
volume=${1:?Usage: prepare-app2-volume.sh vol-ID [--initialize-empty]}
[[ "$volume" =~ ^vol-[0-9a-f]+$ ]] || exit 1
mount_path=/var/lib/management/app2
serial=${volume//-/}
device=$(lsblk -dn -o PATH,SERIAL | awk -v v="$serial" '{gsub(/-/,"",$2);if($2==v)print $1}')
[[ -n "$device" && $(printf '%s\n' "$device" | wc -l) -eq 1 && -b "$device" ]] || {
 echo "Expected Nitro EBS device $volume not found; inspect lsblk and Terraform attachment" >&2; exit 1;
}
if mountpoint -q "$mount_path"; then
 [[ "$(readlink -f "$(findmnt -n -o SOURCE --target "$mount_path")")" == "$(readlink -f "$device")" ]] || {
  echo "A different volume is mounted at $mount_path" >&2; exit 1;
 }
 echo "Expected data volume already mounted"; exit 0
fi
[[ $(lsblk -nr -o TYPE "$device" | wc -l) -eq 1 ]] || { echo "Partitioned volume: manual review required" >&2; exit 1; }
filesystem=$(blkid -s TYPE -o value "$device" || true)
if [[ -z "$filesystem" ]]; then
 [[ ${2:-} == --initialize-empty ]] || { echo "Empty volume; explicitly pass --initialize-empty for FIRST deployment" >&2; exit 1; }
 [[ -z "$(wipefs --no-act --noheadings "$device")" ]] || { echo "Existing disk signatures detected" >&2; exit 1; }
 mkfs.xfs "$device"
elif [[ "$filesystem" != xfs ]]; then
 echo "Expected xfs, found $filesystem; refusing to format" >&2; exit 1
fi
uuid=$(blkid -s UUID -o value "$device")
mkdir -p "$mount_path"
[[ -z "$(ls -A "$mount_path")" ]] || { echo "Mount directory is not empty; refusing to hide files" >&2; exit 1; }
python3 - "$uuid" "$mount_path" <<'FSTAB'
from pathlib import Path
import sys
uuid,target=sys.argv[1:]
p=Path('/etc/fstab'); text=p.read_text(); expected=f'UUID={uuid}'
rows=[line.split() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
existing=[r for r in rows if len(r)>1 and r[1]==target]
if existing and (len(existing)!=1 or existing[0][0]!=expected):
 raise SystemExit('Conflicting fstab entry; review manually')
if not existing:
 p.write_text(text.rstrip()+f'\n{expected} {target} xfs defaults,nofail 0 2\n')
FSTAB
systemctl daemon-reload
mount "$mount_path"
mountpoint -q "$mount_path"
echo "Mounted $volume at $mount_path"
