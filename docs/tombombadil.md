# Tom Bombadil Discord bot

Tom Bombadil runs as a separate container in `docker-compose.yml` under
the `discord` profile. The bot:

- listens for messages in any channel it can see;
- when a message contains `Film: ...` and `Rating: ...`, parses it and
  saves a film note to Redis (so reruns and history stick across
  restarts);
- otherwise generates a conversational reply via Groq.

The HTTP-side `Tombombadil` agent class is registered with the API
regardless of whether the Discord bot is running — `Sauron` can still
route film-themed messages to it via `/execute`. The bot service is
only required if you want the bot to *listen on Discord*.

## One-time Discord setup

1. **Create the application + bot** at <https://discord.com/developers/applications>:
   - "New Application" → name it (e.g. `arda-tombombadil`).
   - "Bot" tab → "Add Bot".
   - "Privileged Gateway Intents" → enable **Message Content Intent**.
     The bot can't read message text without this.
   - Click "Reset Token" and copy the token. **This is the secret you
     need.** Treat it like any LLM key — don't paste it into chat,
     don't commit it.

2. **Generate an invite URL** under "OAuth2 → URL Generator":
   - Scopes: `bot`
   - Bot permissions: `Send Messages`, `Read Message History`,
     `Read Messages/View Channels`. (`Embed Links` and `Add Reactions`
     are nice-to-haves.)
   - Open the generated URL in a browser and pick the server you want
     the bot in.

3. **Store the token on the host** running ARDA. On Linux/Debian, a
   reasonable approach is just adding it to the `.env` (already
   `chmod 600`):

   ```bash
   cd ~/Code/arda-stack/arda
   sed -i "s|^DISCORD_TOKEN=.*|DISCORD_TOKEN=<paste-the-token>|" .env
   ```

   On macOS, prefer Keychain and inject into the docker compose call:

   ```bash
   security add-generic-password -a arda -s discord-token -w
   DISCORD_TOKEN=$(security find-generic-password -a arda -s discord-token -w) \
     docker compose --profile discord up -d
   ```

## Bring the bot up

```bash
cd ~/Code/arda-stack/arda
docker compose --profile discord up -d tombombadil

# Watch the logs until you see bot_ready
docker compose logs -f tombombadil
```

You should see:

```json
{"event": "bot_ready", "user": "arda-tombombadil#1234", ...}
```

In Discord, post a film note like:

```
Name: Solomon
Film: Ran
Rating: 9
```

The bot should react with `OK Ran (9/10) logged`. Plain conversational
messages (no `Film: / Rating:` pattern) get a short Groq-generated
reply.

## Bring it down

```bash
docker compose --profile discord stop tombombadil
docker compose --profile discord rm -f tombombadil
```

The rest of the stack (api, worker, redis) stays up.

## Troubleshooting

- **`PrivilegedIntentsRequired`** at startup: you forgot to enable
  Message Content Intent in the Developer Portal.
- **Bot is online but doesn't respond to film notes**: confirm the
  message contains both `Film:` and `Rating` (case-insensitive). The
  parser is strict.
- **`(error) NOAUTH Authentication required`** in worker logs: the bot
  is trying to talk to a Redis other than the compose-provided one.
  Check `REDIS_HOST=redis` is in the environment for the
  `tombombadil` container.
- **Token leaked**: revoke at the Developer Portal → Bot → Reset Token,
  generate a new one, update `.env`, restart the container.
