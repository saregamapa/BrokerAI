#!/bin/bash
# BrokerAI Production Smoke Test
# Usage: ./scripts/smoke_test.sh https://your-app.onrender.com

BASE_URL=${1:-"http://localhost:8000"}
PASS=0
FAIL=0

check() {
  local name=$1
  local cmd=$2
  local expected=$3
  result=$(eval "$cmd" 2>/dev/null)
  if echo "$result" | grep -q "$expected"; then
    echo "✅ $name"
    ((PASS++))
  else
    echo "❌ $name (got: ${result:0:80})"
    ((FAIL++))
  fi
}

echo "🚀 BrokerAI Smoke Test — $BASE_URL"
echo "$(date)"
echo "---"

check "health_ok"    "curl -sf $BASE_URL/health"       '"status":"ok"'
check "health_ready" "curl -sf $BASE_URL/health/ready" '"ready":true'
check "dashboard_page" "curl -sf $BASE_URL/dashboard.html" '<title>'
check "pricing_page" "curl -sf $BASE_URL/pricing.html" '<title>'
check "login_page" "curl -sf $BASE_URL/login.html" '<title>'
check "signup_page" "curl -sf $BASE_URL/signup.html" '<title>'

echo "---"
echo "Results: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] && echo "✅ All smoke tests passed" || echo "❌ Smoke tests FAILED"
exit $FAIL
