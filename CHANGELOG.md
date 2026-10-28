# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Fork of [Vestige](https://github.com/manch1915/vestige-bot) v0.1.0, renamed to
TG Logging Bot. Everything below this point is specific to the fork; [0.1.0](#010--2026-09-10)
and its links are the upstream project's own history.

### Added

- `/backup`: a short-lived signed link into a read-only web viewer (aiohttp, same
  process) rendering the snapshot archive as a scrollable chat.
- Deletions are flagged (`deleted_at`) instead of discarded, and edits keep the previous
  text (`prev_text`) — both now show up, marked, in the web viewer.
- A `peers` table tracks each chat's display title from `message.chat` on every capture,
  fixing chats where the owner had only ever sent (never received).

### Fixed

- View-once rescue: Telegram doesn't deliver that content as its own `business_message`
  update, and the reply's embedded copy carries a placeholder `message_id`, not the real
  one. The rescued file is now matched by `file_unique_id`, with a from-scratch insert as
  a last resort when no prior snapshot exists at all.

## [0.1.0] — 2026-09-10

First release (upstream, manch1915/vestige-bot).

### Added

- Deletion alerts: a copy of every message the interlocutor deletes, text and media alike.
- Edit alerts: the previous and the current version of an edited message, side by side.
- View-once rescue: replying to a self-destructing file before opening it saves it.
- Settings section reachable at `t.me/<bot>?start=settings` or `/settings`, with a switch
  per notification type and a Russian/English toggle.
- `/status` and `/purge` commands.
- SQLite snapshot storage with a retention janitor and optional media caching to disk.
- Docker image, compose file and a CI workflow across Python 3.10–3.12.

[Unreleased]: https://github.com/manch1915/vestige-bot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/manch1915/vestige-bot/releases/tag/v0.1.0
