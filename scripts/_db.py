import sqlite3
from pathlib import Path


def get_connection(db_path: Path) -> sqlite3.Connection:
    """WAL + busy_timeout + sqlite-vec 拡張ロード"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=500")
    # sqlite-vec 拡張
    conn.enable_load_extension(True)
    import sqlite_vec
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def init_db(conn: sqlite3.Connection):
    """§4 のスキーマを全て CREATE IF NOT EXISTS で実行。
    BM25 column weight 設定も含む。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            migrated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT OR IGNORE INTO schema_version (version) VALUES (1);

        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            project TEXT,
            started_at TEXT,
            last_updated TEXT,
            message_count INTEGER DEFAULT 0,
            cwd TEXT
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            chunk_index INTEGER,
            user_text TEXT,
            assistant_text TEXT,
            timestamp TEXT,
            entry_type TEXT CHECK(entry_type IN
                ('fact','decision','action','lesson','process','context') OR entry_type IS NULL),
            importance REAL,
            hit_count INTEGER DEFAULT 0,
            last_accessed TEXT,
            git_branch TEXT,
            files_touched TEXT,
            tools_used TEXT,
            char_count INTEGER,
            api_tokens INTEGER,
            is_compact_summary INTEGER DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            user_tokenized,
            assistant_tokenized,
            content='',
            content_rowid='id',
            contentless_delete=1
        );

        CREATE TABLE IF NOT EXISTS processing_state (
            session_id TEXT PRIMARY KEY,
            last_processed_offset INTEGER DEFAULT 0,
            checksum TEXT,
            backfill_requested INTEGER DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
    """)

    # Vec table (separate because CREATE VIRTUAL TABLE IF NOT EXISTS may not work with vec0)
    try:
        conn.execute("CREATE VIRTUAL TABLE vec_chunks USING vec0(embedding float[256])")
    except sqlite3.OperationalError:
        pass  # Already exists

    # Indexes (IF NOT EXISTS)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_session ON chunks(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_timestamp ON chunks(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project)")

    # BM25 column weight (idempotent - can be called multiple times)
    conn.execute(
        "INSERT INTO chunks_fts(chunks_fts, rank) VALUES('rank', 'bm25(3.0, 1.0)')"
    )

    conn.commit()
