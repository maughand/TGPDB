# TGPDB
The General Purpose Discord Bot

## Server setup

Open `/botsetup panel` to configure the server. Choose a feature from the page dropdown; each page shows only that feature's controls. Channel and role dropdowns save immediately. Use the dialogs to edit member count text, automod words, or the starboard reaction emoji and threshold. Starboard supports standard emoji and usable custom emoji from this server.

Welcome and farewell pages each support up to 25 saved messages. Choose a channel, then **Add message**. Select a saved message to edit, remove, or preview it. The bot randomly chooses one message whenever a member joins or leaves. Disabling messages keeps the collection so it can be enabled again later.

Message formatting supports `@(user)` for a member mention, `@(username)` for their display name, `@(server)` for the server name, and `@(member_count)` for the member count. For example: `Welcome @(user) to @(server)! You are member @(member_count).` Existing brace placeholders such as `{user}` still work, and existing single-message settings appear in the new editor automatically. `/welcome set` and `/farewell set` replace the collection with one message.

## Moderation

The moderation cog in [`cogs/moderation.py`](./cogs/moderation.py) provides both slash commands and `!` prefix commands:

- `kick`, `ban`, `unban`, and `softban`
- `timeout` and `untimeout`
- `warn` (DMs the member and logs the warning)
- `purge`, `slowmode`, `lock`, and `unlock`
- `nickname`
- `role add` and `role remove`

Commands enforce the invoker's Discord permissions and both role hierarchies. The bot needs the corresponding permissions in the server, plus the `Members` and `Message Content` privileged intents enabled in the Discord developer portal.

Copy [`.env.example`](./.env.example) to `.env`, set `BOT_TOKEN`, and run:

```sh
python bot.py
```

Set `DEV_GUILD_ID` while developing to sync slash commands to one server immediately; without it, commands are synced globally.

## Per-server setup and storage

Administrators can configure feature channels with the `botsetup` command group:

- `botsetup channel moderation_log #channel`
- `botsetup channel welcome #channel`
- `botsetup channel member_count #channel`
- `botsetup channel announcements #channel`
- `botsetup show`, `botsetup clear <feature>`, and `botsetup sync`

Guild settings and member snapshots are stored in the local SQLite database [`.gitignore`](./.gitignore) excludes. Data is keyed by `guild_id`, so one server's channel assignments never affect another server. The database is not opened during cog loading; it is initialized after Discord reports the bot ready.

Successful timeout, ban, unban, softban, and warning actions are also recorded per guild member. The database keeps action history, reasons, moderators, timeout durations, deletion windows, cumulative mute/ban/softban counts, and current mute/ban state.

## Social-media links

The media-links cog watches for supported Instagram, Facebook, X/Twitter, and `fb.watch` URLs. It sends the source URL to a self-hosted Cobalt API and posts the returned temporary media URL to Discord. The bot does not download or store media files locally.

Configure the service in `.env`:

```env
COBALT_API_URL=https://cobalt.example.com/
COBALT_REQUEST_URL=http://cobalt:9000/
COBALT_API_KEY=
COBALT_TIMEOUT_SECONDS=45
CLOUDFLARED_TUNNEL_TOKEN=your-tunnel-token
```

The returned Cobalt URL must be reachable by Discord over HTTPS for Discord to generate the video embed. Protect a public Cobalt instance with API-key authentication and rate limits. Cobalt supports additional services beyond the domains currently enabled in the cog; add them to `SUPPORTED_HOSTS` in [`cogs/media_links.py`](./cogs/media_links.py) after verifying their behavior.

The root [`docker-compose.yml`](./docker-compose.yml) starts the bot, Cobalt, and `cloudflared` together with `restart: unless-stopped`. Create a Cloudflare Tunnel public hostname that routes to `http://cobalt:9000` and place its tunnel token in `.env`. The bot uses the private `COBALT_REQUEST_URL` while Cobalt uses the public `COBALT_API_URL` when generating tunnel links.

## Notifications, fun, and music

The notification cog supports `notify add`, `notify remove`, and `notify list`. Use channel IDs for YouTube subscriptions and channel names for Twitch/Kick. YouTube RSS works without an API key; set `YOUTUBE_API_KEY` to label live broadcasts separately. Twitch requires `TWITCH_CLIENT_ID` and `TWITCH_APP_TOKEN`; Kick requires `KICK_API_TOKEN`.

Fun commands include `8ball`, `roll`, `coinflip`, `choose`, `rate`, and `ship`.

Music commands include `play`, `skip`, `pause`, `resume`, `queue`, `volume`, `stop`, and `leave`. The Docker image installs FFmpeg and music uses `yt-dlp` to stream audio without saving tracks to disk.
