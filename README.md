# YouTube / TikTok / Instagram Telegram Downloader Bot

One Render service that:
- Runs a Telegram webhook — send it a link, it downloads with **yt-dlp**, merges with **ffmpeg**, hosts the result, and replies with a tap-to-download button that opens the video straight in the browser (Chrome, Safari, etc.). This is deliberate: Telegram bots can only *upload* files up to 50MB, but there's no such limit on a link the user opens themselves.
- Exposes an optional `POST /download` API (protected by a bearer token) you can call from a Lovable frontend — it returns the same kind of download link as JSON.
- Automatically deletes hosted videos after they expire (default 60 minutes) so the server's disk doesn't fill up.

## 1. Get a Telegram bot token
Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → follow the prompts → copy the token it gives you.

## 2. Push this folder to GitHub
Create a new GitHub repo and push everything in this `downloader-bot/` folder to it (as the repo root).

## 3. Deploy on Render
1. Go to [render.com](https://render.com) → **New** → **Blueprint**.
2. Connect the GitHub repo you just created. Render will read `render.yaml` automatically.
3. When asked for `TELEGRAM_BOT_TOKEN`, paste the token from step 1.
4. Click **Apply**. Render builds the Docker image (installs ffmpeg + deps) and deploys it.
   - `DOWNLOADER_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` are generated automatically — you don't need to set them.
5. On every startup, the service auto-registers itself as the bot's Telegram webhook using Render's own public URL — no manual `setWebhook` call needed.

## 4. Test it
- Visit `https://<your-service>.onrender.com/health` → should return `{"status":"ok"}`.
- Open a chat with your bot on Telegram and send a YouTube, TikTok, or Instagram link.

## 5. (Optional) Connect to Lovable
If your Lovable app should also trigger downloads (e.g. a "Download" button on a web page):
1. In Render, open the service → **Environment** tab → copy the generated `DOWNLOADER_TOKEN` value.
2. In Lovable, add two secrets:
   - `DOWNLOADER_URL` = `https://<your-service>.onrender.com`
   - `DOWNLOADER_TOKEN` = the value you copied
3. Have Lovable call `POST {DOWNLOADER_URL}/download` with header `Authorization: Bearer {DOWNLOADER_TOKEN}` and JSON body `{"url": "<video link>"}`. It responds with `{"download_url": "...", "expires_in_minutes": 60}` — point users at `download_url`.

## Notes & limits
- **How the link works**: each download gets a random, hard-to-guess filename (e.g. `/files/9f1a2b...c3.mp4`). Anyone with the link can open it, but nobody can discover it without the bot sending it to them. Links stop working after `FILE_TTL_MINUTES` (default 60) — configurable as an env var in Render.
- **Video quality**: capped at `MAX_VIDEO_HEIGHT` (default 1080p) via an env var in Render, mainly to keep file sizes and disk usage reasonable on the free tier. Raise or lower it in Render's Environment tab any time — no code change needed.
- **Disk space**: Render's free plan has limited, ephemeral disk. A background job sweeps expired files every `CLEANUP_INTERVAL_MINUTES` (default 5), so storage doesn't grow unbounded.
- **Private/restricted Instagram content**: public posts and reels work out of the box; private accounts or age-gated content need a logged-in cookies file, which isn't included here.
- **Free tier cold starts**: Render's free web services sleep after inactivity, so the first message after a while may take 30–60s to respond.
- **Keep yt-dlp fresh**: platforms change frequently and break scrapers. `yt-dlp` is left unpinned in `requirements.txt` on purpose — redeploy periodically (or set up Render's auto-deploy on a schedule) to pick up fixes.
