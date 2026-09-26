-- Business tables (separate from memU's own sqlite file).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS characters (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    spec         TEXT NOT NULL,           -- 'v2' | 'v3'
    card_type    TEXT NOT NULL DEFAULT 'character',  -- 'character' | 'story'
    card_json    TEXT NOT NULL,           -- full normalised card JSON
    avatar_path  TEXT,                    -- relative to /uploads/, e.g. 'admin/alice/avatar.png'
    cover_url    TEXT,                    -- story cover image URL (story cards only)
    story_status TEXT NOT NULL DEFAULT 'ongoing',  -- 'ongoing' | 'completed' | 'abandoned' (story cards only)
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    role          TEXT NOT NULL,          -- 'user' | 'assistant' | 'system'
    content       TEXT NOT NULL,
    image_paths   TEXT,                   -- comma-separated relative paths
    excluded_from_context INTEGER NOT NULL DEFAULT 0,  -- 1 = keep in the visible log but drop from LLM history + memU (failed turns)
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_msg_user_char
    ON messages (user_id, character_id, id);

CREATE TABLE IF NOT EXISTS memory_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    memu_name     TEXT NOT NULL,          -- the recall_file name we committed to memU
    topic         TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One open "topic" per (user, character) at a time. New user turns either
-- continue the current topic (turns rolled into the same recall_file) or
-- close it + open a new one (which then accumulates its own recall_file).
CREATE TABLE IF NOT EXISTS topics (
    id              TEXT PRIMARY KEY,    -- e.g. topic_<utc_ts>_<short>
    user_id         TEXT NOT NULL,
    character_id    TEXT NOT NULL,
    summary         TEXT NOT NULL,       -- one-line topic label
    started_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_active_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at       TEXT,                -- NULL while open
    memu_name       TEXT,                -- recall_file.name committed for this topic
    message_count   INTEGER NOT NULL DEFAULT 0,
    accumulated     TEXT NOT NULL DEFAULT ''  -- newline-joined "user: ..." / "assistant: ..." lines
);

CREATE INDEX IF NOT EXISTS idx_topics_open
    ON topics (user_id, character_id, last_active_at);

-- Per-character AI self-photo pool. The LLM can pick from this set when
-- it decides the current conversation warrants sending a picture. Each
-- row carries an auto-generated caption (vision) and its embedding
-- (bge-m3) for fast semantic retrieval before the vision model picks.
CREATE TABLE IF NOT EXISTS character_image_pool (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    character_id       TEXT NOT NULL,
    url                TEXT NOT NULL,
    thumb_url          TEXT NOT NULL,
    caption            TEXT NOT NULL DEFAULT '',
    caption_embedding  BLOB,                    -- numpy float32 tobytes
    source             TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'preset'
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pool_char
    ON character_image_pool (user_id, character_id);

-- Per-character AI mood state. Updated after every successful turn via a
-- lightweight LLM call. Injected into the system prompt on the next turn
-- so the AI's tone reflects a persistent, evolving emotional state.
CREATE TABLE IF NOT EXISTS character_mood (
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    mood_json     TEXT NOT NULL DEFAULT '{}',
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, character_id)
);

-- Per-character event memory. Events are extracted from conversation
-- (exam/interview/birthday/trip…) and surfaced to the AI so it can
-- proactively ask "how did that go?" when the event date passes.
CREATE TABLE IF NOT EXISTS character_events (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    event_text    TEXT NOT NULL,
    event_date    TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'asked' | 'resolved'
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    asked_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_pending
    ON character_events (user_id, character_id, status);

-- Per-character spatial state derived from conversation analysis.
-- Tracks where the user and the AI character currently are (home / on the
-- way / together at ...). Updated only when the conversation contains
-- explicit movement signals; injected into the system prompt so replies
-- respect the current time-space (e.g. no physical contact when apart).
CREATE TABLE IF NOT EXISTS spatial_state (
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, character_id)
);

-- Per-character custom scenes (环境描述). Each scene describes WHERE the
-- character is and HOW that environment shapes her behaviour; activating
-- one overrides the built-in time-based scene in the system prompt.
CREATE TABLE IF NOT EXISTS character_scenes (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scenes_char
    ON character_scenes (user_id, character_id);

-- Per-character AI "moments" (朋友圈-style life updates). Generated
-- periodically by the LLM to create the illusion of an independent life.
CREATE TABLE IF NOT EXISTS character_moments (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL DEFAULT '',  -- '' when author_type='user'
    author_type   TEXT NOT NULL DEFAULT 'character',  -- 'character' | 'user'
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_moments_char
    ON character_moments (user_id, character_id, created_at);

-- Per-character relationship type (auto-computed from intimacy, can also be
-- manually overridden). Determines the AI's tone and behaviour towards the user.
CREATE TABLE IF NOT EXISTS character_relationship (
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    relationship  TEXT NOT NULL DEFAULT '陌生人',
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, character_id)
);

-- Per-character personality evolution log. Each row records a natural
-- personality change extracted from conversation, allowing the character
-- to grow over time.
CREATE TABLE IF NOT EXISTS character_evolution (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    change        TEXT NOT NULL,
    old_personality TEXT,
    new_personality TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_evolution_char
    ON character_evolution (user_id, character_id, created_at);

-- Per-character dynamic prompt rules. Each rule has content + match
-- conditions (time range, emotions, scenes, relationships, intimacy,
-- keywords). The system matches rules against the current conversation
-- context and injects matched content into the system prompt.
CREATE TABLE IF NOT EXISTS prompt_rules (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    character_id    TEXT NOT NULL,
    name            TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    priority        INTEGER NOT NULL DEFAULT 100,
    content         TEXT NOT NULL,
    match_conditions TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prompt_rules_char
    ON prompt_rules (user_id, character_id);
-- Per-character relationship milestones. Automatically detected by the LLM
-- during the post-reply state-update tool call. Each milestone marks a
-- significant "first" in the relationship (first goodnight, first argument,
-- first date, etc.). Once recorded, a milestone is immutable — it represents
-- a permanent memory in the relationship history.
CREATE TABLE IF NOT EXISTS character_milestones (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    type          TEXT NOT NULL,          -- first_goodnight, first_argument, first_date, first_kiss, first_confession, first_meeting, first_gift, first_apology, first_jealousy, first_morning, custom
    title         TEXT NOT NULL,          -- human-readable label, e.g. "第一次说晚安"
    description   TEXT NOT NULL DEFAULT '',-- what happened in this moment
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, character_id, type)  -- one milestone per type per relationship
);
CREATE INDEX IF NOT EXISTS idx_milestones_char
    ON character_milestones (user_id, character_id, created_at);
-- Comments on moments (朋友圈评论). Both AI characters and the user can
-- comment on any moment in the shared timeline.
CREATE TABLE IF NOT EXISTS moment_comments (
    id             TEXT PRIMARY KEY,
    moment_id      TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    commenter_type TEXT NOT NULL DEFAULT 'character',  -- 'character' | 'user'
    character_id   TEXT NOT NULL DEFAULT '',           -- commenter's character_id (empty for user)
    content        TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (moment_id) REFERENCES character_moments(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_comments_moment
    ON moment_comments (moment_id, created_at);

-- Likes on moments (朋友圈点赞). Each character/user can like a moment once.
CREATE TABLE IF NOT EXISTS moment_likes (
    id            TEXT PRIMARY KEY,
    moment_id     TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    liker_type    TEXT NOT NULL DEFAULT 'character',  -- 'character' | 'user'
    character_id  TEXT NOT NULL DEFAULT '',           -- liker's character_id (empty for user)
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (moment_id, character_id, liker_type),
    FOREIGN KEY (moment_id) REFERENCES character_moments(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_likes_moment
    ON moment_likes (moment_id);
-- Daily news digests. AI reads 10 important news items per day (domestic,
-- work, hobby, user's profession), summarises them, and stores the digest
-- here. The digest is also committed to memU long-term memory so the AI
-- can reference current events in conversation.
CREATE TABLE IF NOT EXISTS news_digests (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    digest_date   TEXT NOT NULL,              -- YYYY-MM-DD, one digest per day per pair
    summary       TEXT NOT NULL,              -- LLM-summarised digest text
    raw_titles    TEXT NOT NULL DEFAULT '',   -- newline-joined original headlines
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, character_id, digest_date)
);
CREATE INDEX IF NOT EXISTS idx_news_char
    ON news_digests (user_id, character_id, digest_date);
-- Multi-AI group chat. A group contains multiple AI characters; when the
-- user sends a message, each character replies in turn according to its
-- own personality, seeing the full group conversation history.
CREATE TABLE IF NOT EXISTS chat_groups (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_groups_user
    ON chat_groups (user_id, created_at);

CREATE TABLE IF NOT EXISTS chat_group_members (
    group_id      TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    joined_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (group_id, character_id),
    FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS group_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    sender_type   TEXT NOT NULL,          -- 'user' | 'character'
    character_id  TEXT NOT NULL DEFAULT '',  -- which character sent (empty for user)
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_gmsg_group
    ON group_messages (group_id, id);
-- ========== Story feature tables ==========

-- Story templates: preset story configurations users can start from.
CREATE TABLE IF NOT EXISTS story_templates (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    genre         TEXT NOT NULL,          -- '奇幻' | '悬疑' | '恋爱' | '冒险' | '科幻' | '恐怖' | '日常'
    description   TEXT NOT NULL,
    world_setting TEXT NOT NULL DEFAULT '',
    plot_summary  TEXT NOT NULL DEFAULT '',
    user_role     TEXT NOT NULL DEFAULT '',
    opening       TEXT NOT NULL DEFAULT '',
    story_rules   TEXT NOT NULL DEFAULT '',
    npcs          TEXT NOT NULL DEFAULT '[]',  -- JSON array of preset NPCs
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Story chapters: track story progression through named chapters/stages.
CREATE TABLE IF NOT EXISTS story_chapters (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,          -- story card id
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    chapter_order INTEGER NOT NULL DEFAULT 0,
    is_completed  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_chapters_story
    ON story_chapters (user_id, character_id, chapter_order);

-- Story endings: record how a story concluded.
CREATE TABLE IF NOT EXISTS story_endings (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,          -- story card id
    ending_type   TEXT NOT NULL,          -- 'happy' | 'tragic' | 'bittersweet' | 'cliffhanger' | 'neutral'
    description   TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, character_id)        -- one ending per story playthrough
);

-- Story triggers: scenario triggers matched via RAG to control plot development.
-- Each trigger has a condition_text (used for embedding match against user's
-- message) and a trigger_text (injected into system prompt when matched).
CREATE TABLE IF NOT EXISTS story_triggers (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    character_id    TEXT NOT NULL,          -- story card id
    condition_text  TEXT NOT NULL,          -- situation description for RAG matching
    trigger_text    TEXT NOT NULL,          -- text injected into prompt when triggered
    is_active       INTEGER NOT NULL DEFAULT 1,
    last_triggered_at TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_triggers_story
    ON story_triggers (user_id, character_id, is_active);

-- Story memory: key plot points and facts the AI should remember.
-- Separate from memU's memory system — story memory is simpler and
-- story-specific (NPC states, user choices, plot developments).
CREATE TABLE IF NOT EXISTS story_memory (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,          -- story card id
    memory_type   TEXT NOT NULL,          -- 'plot' | 'choice' | 'npc_state' | 'world_change' | 'user_action'
    content       TEXT NOT NULL,
    importance    INTEGER NOT NULL DEFAULT 5,  -- 1-10, higher = more critical
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_story_mem
    ON story_memory (user_id, character_id, importance);
-- Per-story numeric state system (好感度/堕落度/服从度/体力/学习效率 etc.).
-- Dimensions and stage definitions are configured per story card via
-- card_json.story_state_config; this table stores the *current* values.
-- The stage is computed by the backend from dimension thresholds (not by
-- the LLM) and injected into the next turn's system prompt as an anchor.
CREATE TABLE IF NOT EXISTS story_state (
    user_id       TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    state_json    TEXT NOT NULL DEFAULT '{}',  -- {dimension_id: int 0-100}
    stage         INTEGER NOT NULL DEFAULT 1,   -- backend-computed stage id
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, character_id)
);

-- Conversation summary: rolling compressed summary of older dialogue
-- history. When unsummarized turns exceed the chunk threshold, the
-- oldest chunk is compressed via LLM and merged into this single row.
-- ``summarized_up_to`` tracks the last message ID covered by the summary.
CREATE TABLE IF NOT EXISTS conversation_summary (
    user_id           TEXT NOT NULL,
    character_id      TEXT NOT NULL,
    content           TEXT NOT NULL DEFAULT '',
    summarized_up_to  INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, character_id)
);