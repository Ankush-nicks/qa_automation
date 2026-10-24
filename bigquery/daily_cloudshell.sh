#!/usr/bin/env bash
# Run every morning in Google Cloud Shell:   bash ~/bigquery/daily_cloudshell.sh
# Creates all 5 CSVs and downloads one zip to your computer (also when some query failed).
cd "$(dirname "$0")"
python3 run_exports.py "$@"
STATUS=$?
ZIP=~/qa_exports/qa_exports_$(TZ=Asia/Kolkata date +%Y%m%d).zip
if [ -f "$ZIP" ]; then
  echo "Downloading $ZIP ..."
  cloudshell download "$ZIP"
fi
exit $STATUS
