#!/usr/bin/env python3
"""
BrokerAI Pre-Deploy Validation Script
Run before every production deployment.
Usage: python scripts/pre_deploy_check.py [--env production|staging]
"""
import os
import sys
import json
import subprocess
from datetime import datetime

REQUIRED_ENV_VARS = [
    ("OPENAI_API_KEY", "OpenAI API key for AI generation"),
    ("JWT_SECRET_KEY", "JWT signing secret (must be ≥32 chars)"),
    ("DATABASE_URL", "Database connection URL"),
    ("AYRSHARE_API_KEY", "Ayrshare social publishing key"),
]

RECOMMENDED_ENV_VARS = [
    ("RESEND_API_KEY", "Resend transactional email key"),
    ("SENTRY_DSN", "Sentry error tracking DSN"),
    ("REDIS_URL", "Redis cache URL"),
    ("GOOGLE_CLIENT_ID", "Google OAuth2 client ID"),
    ("STRIPE_SECRET_KEY", "Stripe billing key"),
    ("STRIPE_WEBHOOK_SECRET", "Stripe webhook verification secret"),
    ("AYRSHARE_WEBHOOK_SECRET", "Ayrshare HMAC webhook secret"),
    ("FROM_EMAIL", "Sender email address for transactional emails"),
]

results = {"passed": [], "failed": [], "warnings": [], "timestamp": datetime.utcnow().isoformat()}

def check(name, condition, message, critical=True):
    if condition:
        results["passed"].append(f"✅ {name}: {message}")
    elif critical:
        results["failed"].append(f"❌ {name}: {message}")
    else:
        results["warnings"].append(f"⚠️  {name}: {message}")

def run():
    print(f"\n{'='*60}")
    print("BrokerAI Pre-Deploy Checklist")
    print(f"Timestamp: {results['timestamp']}")
    print(f"{'='*60}\n")

    # 1. Required env vars
    print("📋 REQUIRED ENVIRONMENT VARIABLES")
    for var, desc in REQUIRED_ENV_VARS:
        val = os.getenv(var, "")
        if var == "JWT_SECRET_KEY":
            check(var, len(val) >= 32, f"Present and ≥32 chars ({len(val)} chars)" if val else "MISSING", critical=True)
        else:
            check(var, bool(val), f"Present ({len(val)} chars)" if val else f"MISSING — {desc}", critical=True)

    # 2. Recommended env vars
    print("\n📋 RECOMMENDED ENVIRONMENT VARIABLES")
    for var, desc in RECOMMENDED_ENV_VARS:
        val = os.getenv(var, "")
        check(var, bool(val), f"Present" if val else f"Not set — {desc}", critical=False)

    # 3. Python dependency check
    print("\n📦 DEPENDENCIES")
    try:
        result = subprocess.run(["pip", "check"], capture_output=True, text=True)
        check("pip_deps", result.returncode == 0, "No dependency conflicts" if result.returncode == 0 else result.stdout[:200])
    except Exception as e:
        check("pip_deps", False, f"pip check failed: {e}")

    # 4. Import check
    print("\n🐍 APP IMPORT CHECK")
    try:
        result = subprocess.run(
            [sys.executable, "-c", "from backend.main import app; print('OK')"],
            capture_output=True, text=True, timeout=30
        )
        check("app_import", result.returncode == 0 and "OK" in result.stdout,
              "App imports cleanly" if result.returncode == 0 else result.stderr[:300])
    except Exception as e:
        check("app_import", False, f"Import failed: {e}")

    # 5. Test suite
    print("\n🧪 TEST SUITE")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no", "-x"],
            capture_output=True, text=True, timeout=120
        )
        passed = "passed" in result.stdout
        check("tests", passed, result.stdout.strip().split("\n")[-1] if result.stdout else result.stderr[:200])
    except Exception as e:
        check("tests", False, f"Test run failed: {e}")

    # 6. DATABASE_URL safety check
    print("\n🗄️  DATABASE SAFETY")
    db_url = os.getenv("DATABASE_URL", "")
    is_sqlite = db_url.startswith("sqlite")
    check("db_not_sqlite_prod", not is_sqlite or os.getenv("ALLOW_SQLITE_PROD"),
          "Using PostgreSQL" if not is_sqlite else "SQLite detected — set ALLOW_SQLITE_PROD=1 to override",
          critical=False)

    # 7. Summary
    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    for line in results["passed"]: print(line)
    for line in results["warnings"]: print(line)
    for line in results["failed"]: print(line)

    print(f"\n✅ Passed: {len(results['passed'])}  ⚠️  Warnings: {len(results['warnings'])}  ❌ Failed: {len(results['failed'])}")

    if results["failed"]:
        print("\n🚨 DEPLOY BLOCKED — Fix all failed checks before deploying.")
        sys.exit(1)
    elif results["warnings"]:
        print("\n⚠️  Deploy allowed but review warnings above.")
        sys.exit(0)
    else:
        print("\n🚀 All checks passed — safe to deploy!")
        sys.exit(0)

if __name__ == "__main__":
    run()
