#!/bin/sh
# 此檔案在 Agent 內執行；不會進入另一個 container 執行 shell。
set -eu
[ "$#" -eq 2 ] || { echo 'usage: reconcile.sh businessDate jobId' >&2; exit 2; }
business_date=$1
job_id=$2
case "$business_date" in
  ????-??-??) ;;
  *) echo 'invalid date' >&2; exit 2 ;;
esac
case "$job_id" in
  ''|*[!a-f0-9-]*) echo 'invalid job id' >&2; exit 2 ;;
esac
: "${BATCH_DATA_FILE:?set BATCH_DATA_FILE}"
: "${BATCH_WORK_DIR:?set BATCH_WORK_DIR}"
report="$BATCH_WORK_DIR/$job_id.txt"

# 使用整數分比較金額。資料由部署配置指定，不接受來自瀏覽器的檔案路徑。
if awk -F, -v date="$business_date" -v id="$job_id" '
  NR == 1 {
    if ($0 != "reference,expected_cents,actual_cents") { bad=1; exit }
    next
  }
  {
    if (NF != 3 || $1 !~ /^[A-Za-z0-9_-]+$/ || seen[$1]++ ||
        $2 !~ /^[0-9]+$/ || $3 !~ /^[0-9]+$/ || length($2)>9 || length($3)>9) {
      bad=1; exit
    }
    count++; expected += $2; actual += $3
    if ($2 != $3) mismatches++
  }
  END {
    if (bad || count == 0) { print "Invalid or empty input"; exit 2 }
    status = mismatches == 0 ? "SUCCEEDED" : "FAILED"
    printf "job_id=%s business_date=%s records=%d expected_cents=%.0f actual_cents=%.0f mismatches=%d status=%s\n",
           id, date, count, expected, actual, mismatches, status
    if (mismatches > 0) exit 1
  }
' "$BATCH_DATA_FILE" > "$report"; then
    cat "$report"
else
    result=$?
    cat "$report"
    exit "$result"
fi
