import os
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

_env_url = os.getenv("DATABASE_URL")
if _env_url:
    DATABASE_URL = _env_url
else:
    _root = Path(__file__).resolve().parent.parent
    DATABASE_URL = f"sqlite:///{_root / 'brokerai.db'}"

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)


def _sqlite_migrate() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    insp = inspect(engine)
    if not insp.has_table("posts"):
        return
    cols = {c["name"] for c in insp.get_columns("posts")}
    statements = []
    if "user_id" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN user_id INTEGER")
    if "campaign_id" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN campaign_id INTEGER")
    if "image_url" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN image_url TEXT")
    if "video_script" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN video_script TEXT")
    if "embed_video_url" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN embed_video_url TEXT DEFAULT ''")
    if "day_label" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN day_label TEXT")
    if "publish_platforms" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN publish_platforms TEXT")
    if "platform_response" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN platform_response TEXT")
    if "published_at" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN published_at DATETIME")
    if "compliance_passed" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN compliance_passed INTEGER")
    if "compliance_checked_at" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN compliance_checked_at DATETIME")
    if "compliance_issues" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN compliance_issues TEXT")
    if "publish_attempts" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN publish_attempts INTEGER")
    if "idempotency_key" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN idempotency_key TEXT")
    if "likes" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN likes INTEGER DEFAULT 0")
    if "comments" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN comments INTEGER DEFAULT 0")
    if "shares" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN shares INTEGER DEFAULT 0")
    if "impressions" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN impressions INTEGER DEFAULT 0")
    if "engagement_rate" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN engagement_rate REAL DEFAULT 0")
    if "is_processing" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN is_processing INTEGER DEFAULT 0")
    if "publish_last_error" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN publish_last_error TEXT DEFAULT ''")
    if "platform" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN platform TEXT DEFAULT ''")
    if "social_post_id" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN social_post_id TEXT DEFAULT ''")
    if "content" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN content TEXT DEFAULT ''")
    if "max_attempts" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN max_attempts INTEGER DEFAULT 3")
    if "is_locked" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN is_locked INTEGER DEFAULT 0")
    if "lock_timestamp" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN lock_timestamp DATETIME")
    if "next_publish_attempt_at" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN next_publish_attempt_at DATETIME")
    if "last_error" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN last_error TEXT DEFAULT ''")
    if "slides" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN slides TEXT DEFAULT '[]'")
    if "is_carousel" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN is_carousel INTEGER DEFAULT 0")
    # S5-09 soft-delete + S5-02 A/B captions (older DBs pre-date these columns)
    if "deleted_at" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN deleted_at DATETIME")
    if "ab_variant_b" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN ab_variant_b TEXT")
    if "ab_winner" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN ab_winner TEXT")
    if "ab_status" not in cols:
        statements.append("ALTER TABLE posts ADD COLUMN ab_status TEXT")

    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))
        pragma = conn.execute(text("PRAGMA table_info(posts)")).fetchall()
        colnames = {row[1] for row in pragma}
        if "publish_platforms" in colnames:
            conn.execute(
                text(
                    "UPDATE posts SET publish_platforms = '[\"facebook\"]' "
                    "WHERE publish_platforms IS NULL OR publish_platforms = ''"
                )
            )
        if "platform_response" in colnames:
            conn.execute(
                text(
                    "UPDATE posts SET platform_response = '{}' "
                    "WHERE platform_response IS NULL OR platform_response = ''"
                )
            )
        if "compliance_issues" in colnames:
            conn.execute(
                text(
                    "UPDATE posts SET compliance_issues = '[]' "
                    "WHERE compliance_issues IS NULL OR compliance_issues = ''"
                )
            )
        if "image_url" in colnames:
            conn.execute(
                text("UPDATE posts SET image_url = '' WHERE image_url IS NULL")
            )
        if "video_script" in colnames:
            conn.execute(
                text("UPDATE posts SET video_script = '' WHERE video_script IS NULL")
            )
        if "embed_video_url" in colnames:
            conn.execute(
                text("UPDATE posts SET embed_video_url = '' WHERE embed_video_url IS NULL")
            )
        conn.execute(
            text("UPDATE posts SET status = 'published' WHERE status = 'scheduled'")
        )
        if "publish_attempts" in colnames:
            conn.execute(
                text(
                    "UPDATE posts SET publish_attempts = 0 WHERE publish_attempts IS NULL"
                )
            )
        if "likes" in colnames:
            conn.execute(text("UPDATE posts SET likes = 0 WHERE likes IS NULL"))
        if "comments" in colnames:
            conn.execute(text("UPDATE posts SET comments = 0 WHERE comments IS NULL"))
        if "shares" in colnames:
            conn.execute(text("UPDATE posts SET shares = 0 WHERE shares IS NULL"))
        if "impressions" in colnames:
            conn.execute(text("UPDATE posts SET impressions = 0 WHERE impressions IS NULL"))
        if "engagement_rate" in colnames:
            conn.execute(
                text("UPDATE posts SET engagement_rate = 0 WHERE engagement_rate IS NULL")
            )
        if "is_processing" in colnames:
            conn.execute(
                text("UPDATE posts SET is_processing = 0 WHERE is_processing IS NULL")
            )
        if "publish_last_error" in colnames:
            conn.execute(
                text(
                    "UPDATE posts SET publish_last_error = '' WHERE publish_last_error IS NULL"
                )
            )
        if "platform" in colnames:
            conn.execute(text("UPDATE posts SET platform = '' WHERE platform IS NULL"))
        if "social_post_id" in colnames:
            conn.execute(
                text("UPDATE posts SET social_post_id = '' WHERE social_post_id IS NULL")
            )
        if "content" in colnames:
            conn.execute(
                text(
                    "UPDATE posts SET content = COALESCE(NULLIF(caption, ''), '') "
                    "WHERE content IS NULL OR content = ''"
                )
            )
        if "max_attempts" in colnames:
            conn.execute(
                text("UPDATE posts SET max_attempts = 3 WHERE max_attempts IS NULL")
            )
        if "is_locked" in colnames and "is_processing" in colnames:
            conn.execute(
                text(
                    "UPDATE posts SET is_locked = 1 WHERE is_processing = 1 "
                    "AND (is_locked IS NULL OR is_locked = 0)"
                )
            )
        if "last_error" in colnames and "publish_last_error" in colnames:
            conn.execute(
                text(
                    "UPDATE posts SET last_error = publish_last_error "
                    "WHERE (last_error IS NULL OR trim(last_error) = '') "
                    "AND publish_last_error IS NOT NULL AND trim(publish_last_error) != ''"
                )
            )
        conn.execute(
            text("UPDATE posts SET status = 'review' WHERE status = 'pending_approval'")
        )
        conn.execute(
            text("UPDATE posts SET status = 'failed' WHERE status = 'publish_failed'")
        )
        conn.execute(
            text("UPDATE posts SET status = 'draft' WHERE status = 'pending'")
        )

    insp2 = inspect(engine)
    if insp2.has_table("campaigns"):
        ccols = {c["name"] for c in insp2.get_columns("campaigns")}
        with engine.begin() as conn:
            if "graph_thread_id" not in ccols:
                conn.execute(
                    text(
                        "ALTER TABLE campaigns ADD COLUMN graph_thread_id TEXT DEFAULT ''"
                    )
                )
            if "facebook_url" not in ccols:
                conn.execute(
                    text("ALTER TABLE campaigns ADD COLUMN facebook_url TEXT DEFAULT ''")
                )
            if "instagram_url" not in ccols:
                conn.execute(
                    text("ALTER TABLE campaigns ADD COLUMN instagram_url TEXT DEFAULT ''")
                )
            if "linkedin_url" not in ccols:
                conn.execute(
                    text("ALTER TABLE campaigns ADD COLUMN linkedin_url TEXT DEFAULT ''")
                )
            if "name" not in ccols:
                conn.execute(
                    text("ALTER TABLE campaigns ADD COLUMN name TEXT DEFAULT ''")
                )
            if "objective" not in ccols:
                conn.execute(
                    text("ALTER TABLE campaigns ADD COLUMN objective TEXT DEFAULT ''")
                )
            if "target_audience" not in ccols:
                conn.execute(
                    text("ALTER TABLE campaigns ADD COLUMN target_audience TEXT DEFAULT ''")
                )
            if "deleted_at" not in ccols:
                conn.execute(text("ALTER TABLE campaigns ADD COLUMN deleted_at DATETIME"))
            conn.execute(
                text(
                    "UPDATE campaigns SET facebook_url = '' WHERE facebook_url IS NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE campaigns SET instagram_url = '' WHERE instagram_url IS NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE campaigns SET linkedin_url = '' WHERE linkedin_url IS NULL"
                )
            )
            conn.execute(text("UPDATE campaigns SET name = '' WHERE name IS NULL"))
            conn.execute(text("UPDATE campaigns SET objective = '' WHERE objective IS NULL"))
            conn.execute(text("UPDATE campaigns SET target_audience = '' WHERE target_audience IS NULL"))

    # User table migrations
    if insp2.has_table("users"):
        ucols = {c["name"] for c in insp2.get_columns("users")}
        with engine.begin() as conn:
            if "timezone" not in ucols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'UTC'")
                )
                conn.execute(text("UPDATE users SET timezone = 'UTC'"))
            if "plan" not in ucols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'starter'")
                )
            # Backfill NULLs for existing rows; also migrate legacy 'free' rows to starter
            conn.execute(
                text("UPDATE users SET plan = 'starter' WHERE plan IS NULL OR plan = 'free'")
            )
            conn.execute(
                text("UPDATE users SET timezone = 'UTC' WHERE timezone IS NULL OR trim(timezone) = ''")
            )
            if "ayrshare_profile_key" not in ucols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN ayrshare_profile_key TEXT")
                )
            if "social_connected" not in ucols:
                conn.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN social_connected INTEGER DEFAULT 0"
                    )
                )
            conn.execute(
                text(
                    "UPDATE users SET social_connected = 0 WHERE social_connected IS NULL"
                )
            )
            if "facebook_url" not in ucols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN facebook_url TEXT DEFAULT ''")
                )
            if "instagram_url" not in ucols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN instagram_url TEXT DEFAULT ''")
                )
            if "linkedin_url" not in ucols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN linkedin_url TEXT DEFAULT ''")
                )
            conn.execute(
                text(
                    "UPDATE users SET facebook_url = '' WHERE facebook_url IS NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE users SET instagram_url = '' WHERE instagram_url IS NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE users SET linkedin_url = '' WHERE linkedin_url IS NULL"
                )
            )
            # RBAC columns
            if "account_type" not in ucols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN account_type TEXT DEFAULT 'individual'")
                )
            if "role" not in ucols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'owner'")
                )
            if "team_id" not in ucols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN team_id INTEGER")
                )
            # Backfill nulls for RBAC fields on existing rows
            conn.execute(
                text("UPDATE users SET account_type = 'individual' WHERE account_type IS NULL OR trim(account_type) = ''")
            )
            conn.execute(
                text("UPDATE users SET role = 'owner' WHERE role IS NULL OR trim(role) = ''")
            )
            # Brand kit columns (wizard Media & Design step)
            for col, default in [
                ("brand_logo_url", "''"),
                ("brand_primary_color", "''"),
                ("brand_secondary_color", "''"),
                ("brand_font", "''"),
                ("brand_voice", "''"),
                ("brand_source", "''"),
                ("brand_key_messages", "NULL"),
                ("brand_forbidden_words", "NULL"),
                ("brand_cta_style", "NULL"),
            ]:
                if col not in ucols:
                    conn.execute(
                        text(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT {default}")
                    )
                conn.execute(
                    text(f"UPDATE users SET {col} = '' WHERE {col} IS NULL")
                )

    # Campaign RBAC columns migration
    if insp2.has_table("campaigns"):
        ccols2 = {c["name"] for c in insp2.get_columns("campaigns")}
        with engine.begin() as conn:
            if "team_id" not in ccols2:
                conn.execute(
                    text("ALTER TABLE campaigns ADD COLUMN team_id INTEGER")
                )
            if "created_by" not in ccols2:
                conn.execute(
                    text("ALTER TABLE campaigns ADD COLUMN created_by INTEGER")
                )
                # Backfill: set created_by = user_id for existing campaigns
                conn.execute(
                    text("UPDATE campaigns SET created_by = user_id WHERE created_by IS NULL")
                )
            if "approved_by" not in ccols2:
                conn.execute(
                    text("ALTER TABLE campaigns ADD COLUMN approved_by INTEGER")
                )

    # team_invites table
    if not insp2.has_table("team_invites"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE team_invites ("
                    "id INTEGER PRIMARY KEY, "
                    "email TEXT NOT NULL, "
                    "team_id INTEGER NOT NULL, "
                    "role TEXT DEFAULT 'member', "
                    "token TEXT NOT NULL UNIQUE, "
                    "is_used INTEGER DEFAULT 0, "
                    "expires_at DATETIME NOT NULL, "
                    "created_at DATETIME)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_team_invites_token ON team_invites(token)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_team_invites_email ON team_invites(email)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_team_invites_team_id ON team_invites(team_id)"
                )
            )

    # S1-02: Stripe billing columns on users
    if insp2.has_table("users"):
        ucols_stripe = {c["name"] for c in insp2.get_columns("users")}
        with engine.begin() as conn:
            if "stripe_customer_id" not in ucols_stripe:
                conn.execute(text("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT"))
            if "stripe_subscription_id" not in ucols_stripe:
                conn.execute(text("ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT"))
            if "plan_expires_at" not in ucols_stripe:
                conn.execute(text("ALTER TABLE users ADD COLUMN plan_expires_at DATETIME"))

    # S0-07: Account lockout + S0-06: email_verified columns on users
    if insp2.has_table("users"):
        ucols2 = {c["name"] for c in insp2.get_columns("users")}
        with engine.begin() as conn:
            if "email_verified" not in ucols2:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0")
                )
                conn.execute(text("UPDATE users SET email_verified = 0 WHERE email_verified IS NULL"))
            if "failed_login_attempts" not in ucols2:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER DEFAULT 0")
                )
                conn.execute(text("UPDATE users SET failed_login_attempts = 0 WHERE failed_login_attempts IS NULL"))
            if "locked_until" not in ucols2:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN locked_until DATETIME")
                )
            # S4-05: Google OAuth profile fields
            if "google_id" not in ucols2:
                conn.execute(text("ALTER TABLE users ADD COLUMN google_id TEXT"))
            if "display_name" not in ucols2:
                conn.execute(text("ALTER TABLE users ADD COLUMN display_name TEXT"))
            if "avatar_url" not in ucols2:
                conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url TEXT"))

    # S0-03: Refresh tokens table
    if not insp2.has_table("refresh_tokens"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE refresh_tokens ("
                    "id INTEGER PRIMARY KEY, "
                    "user_id INTEGER NOT NULL, "
                    "token_hash TEXT NOT NULL UNIQUE, "
                    "revoked INTEGER DEFAULT 0, "
                    "expires_at DATETIME NOT NULL, "
                    "created_at DATETIME)"
                )
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id ON refresh_tokens(user_id)")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_token_hash ON refresh_tokens(token_hash)")
            )

    # S0-04: Password reset tokens table
    if not insp2.has_table("password_reset_tokens"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE password_reset_tokens ("
                    "id INTEGER PRIMARY KEY, "
                    "user_id INTEGER NOT NULL, "
                    "token_hash TEXT NOT NULL UNIQUE, "
                    "used INTEGER DEFAULT 0, "
                    "expires_at DATETIME NOT NULL, "
                    "created_at DATETIME)"
                )
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_prt_user_id ON password_reset_tokens(user_id)")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_prt_token_hash ON password_reset_tokens(token_hash)")
            )

    # S0-06: Email verification tokens table
    if not insp2.has_table("email_verification_tokens"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE email_verification_tokens ("
                    "id INTEGER PRIMARY KEY, "
                    "user_id INTEGER NOT NULL, "
                    "token_hash TEXT NOT NULL UNIQUE, "
                    "used INTEGER DEFAULT 0, "
                    "expires_at DATETIME NOT NULL, "
                    "created_at DATETIME)"
                )
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_evt_user_id ON email_verification_tokens(user_id)")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_evt_token_hash ON email_verification_tokens(token_hash)")
            )

    # Social accounts table (source-of-truth for connection status)
    if not insp2.has_table("social_accounts"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE social_accounts ("
                    "id INTEGER PRIMARY KEY, "
                    "user_id INTEGER NOT NULL, "
                    "platform TEXT DEFAULT 'ayrshare_profile', "
                    "is_connected INTEGER DEFAULT 0, "
                    "profile_key TEXT DEFAULT '', "
                    "created_at DATETIME, "
                    "updated_at DATETIME)"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_social_accounts_user_platform "
                    "ON social_accounts(user_id, platform)"
                )
            )
    else:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_social_accounts_user_platform "
                    "ON social_accounts(user_id, platform)"
                )
            )


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    _sqlite_migrate()


def get_session():
    with Session(engine) as session:
        yield session
