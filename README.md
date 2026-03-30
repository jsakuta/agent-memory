# agent-memory

**English** | [日本語](README.ja.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Windows | macOS](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS-lightgrey.svg)]()
[![Claude Code Plugin](https://img.shields.io/badge/Claude%20Code-Plugin-blueviolet.svg)]()

Claude Code plugin that automatically saves and searches conversations across sessions.

Transcripts are stored in SQLite on session end, and searchable via a hybrid of FTS5 (full-text) and vector search (ruri-v3-130m). Optimized for Japanese.

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [How It Works](#how-it-works)
- [Known Limitations](#known-limitations)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Auto-save** --- Transcripts are captured to DB automatically on session end
- **Hybrid search** --- FTS5 trigram + vector KNN (ruri-v3-130m), merged via RRF
- **Context injection** --- Recent session highlights are injected at session start
- **Japanese-optimized** --- Trigram tokenizer + Japanese embedding model
- **Cross-platform** --- Windows (Git Bash) and macOS supported

## Quick Start

```bash
# 1. Place the plugin
cp -r agent-memory ~/.claude/plugins/local/agent-memory

# 2. Enable in settings.json
# Add: "enabledPlugins": { "agent-memory@local": true }

# 3. Add Stop hook to settings.json (see Installation below)

# 4. Restart Claude Code --- setup runs automatically
```

First launch installs dependencies, downloads the embedding model (~130MB), and backfills existing sessions.

## Installation

### 1. Place the plugin

```bash
cp -r agent-memory ~/.claude/plugins/local/agent-memory
```

### 2. Enable the plugin

Add to `~/.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "agent-memory@local": true
  }
}
```

### 3. Add Stop hook (required)

Due to a Claude Code bug ([#29767](https://github.com/anthropics/claude-code/issues/29767)), plugin Stop hooks are not executed. Manual addition to `settings.json` is required.

**Windows:**

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash '/c/Users/SHORTN~1/.claude/plugins/local/agent-memory/scripts/run-capture.sh'",
            "timeout": 5000
          }
        ]
      }
    ]
  }
}
```

> If your username contains parentheses or Unicode characters, use the 8.3 short name.
> Check with: `cmd /c "dir /x C:\Users"`

**macOS:**

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/plugins/local/agent-memory/scripts/run-capture.sh",
            "timeout": 5000
          }
        ]
      }
    ]
  }
}
```

> The SessionStart hook is defined in the bundled `hooks.json` --- no manual setup needed.

### 4. Launch a session

On first launch, the following happens automatically:

1. Python venv creation and dependency installation
2. ruri-v3-130m model download (~130MB)
3. Bulk import of existing sessions

## Usage

### Search past conversations

Use natural language inside Claude Code:

```
> What did we decide about the API design last time?
> Recall what we discussed in the previous session
> project:my-app search for auth flow
```

Or run directly from CLI:

```bash
node ~/.claude/plugins/local/agent-memory/scripts/run-search.mjs "search query"
node ~/.claude/plugins/local/agent-memory/scripts/run-search.mjs "project:my-project auth flow"
```

### Manual backfill

Import sessions from periods when the Stop hook was not running:

```bash
cd ~/.claude/plugins/local/agent-memory/scripts

# macOS
.venv/bin/python backfill_external.py

# Windows
.venv/Scripts/python.exe backfill_external.py
```

## Configuration

Create `config/settings.toml` to override defaults. See `config/settings.default.toml` for all options.

### Project mapping

Map working directories to project IDs for filtered search:

```toml
[projects]
"/Users/you/repos/my-app" = "my-app"
"C:/Users/you/repos/another" = "another"
```

The session's cwd is matched by longest prefix against these paths.

### Settings reference

| Setting | Default | Description |
|---------|---------|-------------|
| `token_budget` | 2000 | Max estimated tokens injected at session start |
| `recency_count` | 3 | Number of recent sessions considered for injection |
| `result_limit` | 20 | Max search results returned |
| `half_life_days` | 30.0 | Recency score decay half-life (days) |
| `fts_candidate_limit` | 100 | Candidate pool size for FTS5/Vec search |
| `inject_cache_ttl_seconds` | 300 | Cache TTL for injection deduplication |
| `recency_floor` | 0.01 | Minimum recency score |
| `access_boost_factor` | 0.3 | Weight for hit-count boost in recency |
| `vec.enabled` | true | Enable vector search |

## Troubleshooting

### Search returns 0 results

```bash
cd ~/.claude/plugins/local/agent-memory/scripts
node -e "import('./_run.mjs').then(m=>m.runPython('_health.py'))"
```

### Stop hook not capturing

1. Verify the Stop hook entry exists in `settings.json`
2. Check `data/logs/capture.log` for errors
3. Run manual backfill (see [Usage](#manual-backfill))

### Broken venv

```bash
rm -rf ~/.claude/plugins/local/agent-memory/scripts/.venv
# Restart Claude Code --- auto-rebuilds on next session
```

### Full reset

```bash
rm ~/.claude/plugins/local/agent-memory/data/memory.db
rm ~/.claude/plugins/local/agent-memory/data/backfill_complete
# Restart Claude Code --- rebuilds DB and runs backfill
```

## How It Works

```
Session Start                  Session End                  Search (skill)
     |                              |                            |
 setup.sh (first run)         run-capture.sh               run-search.mjs
     |                              |                            |
 setup.mjs                    capture.py                    search.py
 venv + model + backfill      JSONL diff parse              FTS5 + Vec
     |                        chunks -> SQLite               RRF merge
 inject.py                    backfill_vec.py               2-factor rerank
 recent context -> stdout     embeddings (async)            hit_count update
```

Search combines FTS5 trigram (3-stage fallback: phrase -> OR -> LIKE) and vector KNN (ruri-v3-130m, 512-dim), merged via RRF(k=60), then re-ranked by relevance (70%) + recency (30%). Vector search gracefully degrades when the model is not yet downloaded.

For full architecture details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Known Limitations

| Limitation | Impact | Workaround |
|------------|--------|------------|
| Stop hook requires manual setup | Medium | Claude Code [#29767](https://github.com/anthropics/claude-code/issues/29767) |
| Windows console flash on hook | Low | Bash wrapper suppresses child windows ([#15572](https://github.com/anthropics/claude-code/issues/15572)) |
| CPU inference only | Medium | GPU not supported for embeddings |
| No real-time sync | Low | Conversations are captured at session end |

## Requirements

- Claude Code >= 1.0.0
- Python >= 3.12
- Node.js >= 18
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss.

### Development setup

```bash
git clone https://github.com/jsakuta/agent-memory.git
cd agent-memory/scripts
uv venv .venv
uv pip install -e .
```

## License

[MIT](LICENSE)
