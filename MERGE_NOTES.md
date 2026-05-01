# Audio Merge integration notes

This repository keeps the Join Request Acceptor bot as the main bot.

Merge rule requested:

- Keep the Join Request Acceptor `/start` command unchanged.
- Remove/disable the Audio Merge `/start` command.
- Keep Audio Merge commands separately as `/admin`, `/importlinks`, and `/stop`.

Important runtime note:

The current Join Request Acceptor bot is a Pyrogram bot, while the uploaded Audio Merge project is a python-telegram-bot application. Both frameworks cannot safely poll updates for the same `BOT_TOKEN` at the same time. The audio code must be ported into Pyrogram handlers, or the audio bot must run with a separate token.

Target merged command layout:

```text
/start        -> original Join Request Acceptor start output, unchanged
/accept       -> join request accept feature
/login        -> login session feature
/logout       -> logout session feature
/broadcast    -> broadcast feature
/admin        -> audio merge admin panel
/importlinks  -> audio import links feature
/stop         -> stop audio queue/upload
```
