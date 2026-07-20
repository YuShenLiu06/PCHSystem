"""Web 账号主锚 + 多 UUID 绑定（身份模型升级）

Revision ID: 0014_web_accounts_bind
Revises: 0013_qty_per_unit_float
Create Date: 2026-07-19
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '0014_web_accounts_bind'
down_revision = '0013_qty_per_unit_float'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 新表 users.web_accounts（身份主锚）
    op.execute("""
        CREATE TABLE users.web_accounts (
            id bigserial PRIMARY KEY,
            username text UNIQUE,
            password_hash text,
            role text NOT NULL DEFAULT 'user',
            wiki_user_id text,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_active_at timestamptz NOT NULL DEFAULT now(),
            CHECK ((username IS NULL) = (password_hash IS NULL))
        );
    """)

    # 新表 users.bind_tokens（双向绑定短码）
    op.execute("""
        CREATE TABLE users.bind_tokens (
            token uuid PRIMARY KEY,
            short_code text NOT NULL UNIQUE,
            direction text NOT NULL CHECK (direction IN ('game_init', 'web_init')),
            player_uuid uuid NULL,
            target_account_id bigint NULL,
            expires_at timestamptz NOT NULL,
            used_at timestamptz NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CHECK (
                (direction = 'game_init' AND player_uuid IS NOT NULL AND target_account_id IS NULL)
                OR
                (direction = 'web_init' AND target_account_id IS NOT NULL AND player_uuid IS NULL)
            ),
            FOREIGN KEY (player_uuid) REFERENCES users.players(uuid) ON DELETE SET NULL,
            FOREIGN KEY (target_account_id) REFERENCES users.web_accounts(id) ON DELETE CASCADE
        );

        CREATE INDEX ix_bind_tokens_active
            ON users.bind_tokens (direction, short_code)
            WHERE used_at IS NULL;
    """)

    # users.players 加 web_account_id 外键
    op.execute("""
        ALTER TABLE users.players
            ADD COLUMN web_account_id bigint NULL
            REFERENCES users.web_accounts(id) ON DELETE SET NULL;
    """)


def downgrade() -> None:
    # 逆序删除：先删外键列，再删表
    op.execute("""
        ALTER TABLE users.players DROP COLUMN web_account_id;
        DROP TABLE users.bind_tokens;
        DROP TABLE users.web_accounts;
    """)
