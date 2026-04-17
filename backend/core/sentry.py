"""Sentry SDK initialization — no-op when SENTRY_DSN is not set."""
import os
import logging

log = logging.getLogger("brokerai.sentry")


def init_sentry() -> bool:
    """Initialize Sentry. Returns True if initialized, False if skipped."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        log.debug("SENTRY_DSN not set — Sentry disabled")
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_RATE", "0.1")),
            environment=os.getenv("ENVIRONMENT", "production"),
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            send_default_pii=False,  # GDPR safety
        )
        log.info("sentry_initialized dsn_prefix=%s", dsn[:30])
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("sentry_init_failed err=%s", e)
        return False
