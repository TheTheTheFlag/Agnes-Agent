#!/bin/bash
curl -s -N -m 20 -X POST http://127.0.0.1:8081/api/chat \
  -H 'Content-Type: application/json' \
  -H 'X-Agnes-Internal-Token: layRvh1va32yPHIrwii5zxpLYkIMNoh4KDi_3SMwfO8FbQ4db-YKD-MWbSxXWmLw' \
  -H 'X-Agnes-Internal-User: Test' \
  --data @/home/mirror/Agnes-Agent/body_tmp.json | grep 'step' | tail -5
echo "CURL_DONE"