# Security policy

## Reporting a vulnerability

Open a [private security advisory](https://github.com/Rosu315/tg-logging-bot/security/advisories/new)
instead of a public issue. Expect a first reply within seven days.

## What this bot stores

Running this bot means running a message archive. Anyone with access to the machine has
access to the data, so treat the deployment the way you treat your own Telegram session.

| Data | Where | Lifetime |
| --- | --- | --- |
| Text and metadata of messages in connected chats | `DB_PATH` (SQLite) | `RETENTION_DAYS`, or until delivered as a deletion alert |
| Media files, when `CACHE_MEDIA=true` | `MEDIA_DIR` | same |
| Business connection ids and owner ids | `DB_PATH` | until the connection is removed |
| `/backup` link-signing secret | `DB_PATH` (`meta` table), generated on first run | until the database is wiped |
| Bot token | `.env` | — |

Nothing is sent anywhere except to the account owner's own chat with the bot, or to
whoever holds a valid, unexpired `/backup` link.

## The `/backup` web viewer

`/backup` opens a read-only web page over the same archive. It's a separate attack
surface from the bot itself:

- The server binds to `WEB_HOST` (default `127.0.0.1`) — it is not meant to be reachable
  without something in front of it (a tunnel, a reverse proxy) terminating TLS.
- Access control is a single signed, time-limited token (`BACKUP_LINK_TTL_MINUTES`,
  default 30 minutes) carried in the URL, not a login. Anyone who obtains a valid link
  before it expires can read that owner's archive — treat the link like a password for
  as long as it's valid, and don't let it leak into logs, browser history on a shared
  device, or a referrer header.
- The signing secret lives in the database (see table above); if it leaks, every past and
  future link can be forged until it's rotated (currently: wipe the `meta` row).

## Hardening checklist

- Keep `.env` out of version control (it is already in `.gitignore`) and `chmod 600` it.
- Put the database on an encrypted volume — snapshots are stored in plain text.
- Keep `CACHE_MEDIA=false` unless you need it; `file_id` references are enough most of
  the time and cost no disk.
- Lower `RETENTION_DAYS` on shared hosts.
- Revoke the token in @BotFather if the host is ever compromised; that instantly kills
  every business connection.
- `/purge` wipes an owner's snapshots on demand, and disconnecting the bot wipes them
  automatically.

## Legal note

Recording other people's messages may require their consent where you live. You are
responsible for how you use this software.
