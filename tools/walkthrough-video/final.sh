#!/bin/bash
# final.sh <durations.json|-> <audio.mp3|-> <out.mp4>
set -e
cd /opt/capture
D=$1; AUD=$2; OUT=$3
if [ "$D" = "-" ]; then venv/bin/python build_plan.py; else venv/bin/python build_plan.py "$D"; fi
mkdir -p out/parts; rm -f out/parts/*
for i in 0 1 2 3; do venv/bin/python -u render.py plan.json out/parts/p$i.mp4 --part=$i/4 > /tmp/part$i.log 2>&1 & done
wait
printf "file '%s'\n" /opt/capture/out/parts/p0.mp4 /opt/capture/out/parts/p1.mp4 /opt/capture/out/parts/p2.mp4 /opt/capture/out/parts/p3.mp4 > out/parts/list.txt
if [ "$AUD" = "-" ]; then
  ffmpeg -v error -y -f concat -safe 0 -i out/parts/list.txt -c copy -movflags +faststart "$OUT"
else
  ffmpeg -v error -y -f concat -safe 0 -i out/parts/list.txt -i "$AUD" -c:v copy -c:a aac -b:a 160k -shortest -movflags +faststart "$OUT"
fi
echo FINAL-DONE "$OUT"
