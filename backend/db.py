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
                    text("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'")
                )
            # Backfill NULLs for existing rows
            conn.execute(
                text("UPDATE users SET plan = 'free' WHERE plan IS NULL")
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


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    _sqlite_migrate()


def get_session():
    with Session(engine) as session:
        yield session
