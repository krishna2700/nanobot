# nanobot Skills

Skills are placed in the workspace `skills/` directory (`~/.nanobot/workspace/skills/` by default).

## Getting Started

Run `nanobot onboard` to populate your workspace with the default built-in skills.
Skills are copied as regular files, so you can freely modify, remove, or add new ones.

## Skill Format

Each skill is a directory containing a `SKILL.md` file with:
- YAML frontmatter (name, description, metadata)
- Markdown instructions for the agent

## Attribution

These skills are adapted from [OpenClaw](https://github.com/openclaw/openclaw)'s skill system.
The skill format and metadata structure follow OpenClaw's conventions to maintain compatibility.

## Default Skills

| Skill | Description |
|-------|-------------|
| `github` | Interact with GitHub using the `gh` CLI |
| `weather` | Get weather info using wttr.in and Open-Meteo |
| `summarize` | Summarize URLs, files, and YouTube videos |
| `tmux` | Remote-control tmux sessions |
| `clawhub` | Search and install skills from ClawHub registry |
| `skill-creator` | Create new skills |
| `memory` | Two-layer memory system with grep-based recall |
| `cron` | Schedule reminders and recurring tasks |
