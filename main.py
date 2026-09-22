import os
import re
import time
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx
import yt_dlp
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("downloader-bot")

app = FastAPI(title="yt-dlp Telegram Downloader")

# ---- Config (set these as environment variables on Render) ----
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DOWNLOADER_TOKEN = os.environ.get("DOWNLOADER_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET") or DOWNLOADER_TOKEN
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

MAX_VIDEO_HEIGHT = os.environ.get("MAX_VIDEO_HEIGHT", "1080")
FILE_TTL_MINUTES = int(os.environ.get("FILE_TTL_MINUTES", "60"))
CLEANUP_INTERVAL_MINUTES = int(os.environ.get("CLEANUP_INTERVAL_MINUTES", "5"))

STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "/app/storage"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

FILENAME_RE = re.compile(r"^[a-f0-9]{32}\.mp4$")

URL_RE = re.compile(
    r"(https?://)?(www\.|vm\.|vt\.|m\.)?"
    r"(youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/\S+",
    re.IGNORECASE,
)


def extract_url(text: str) -> Optional[str]:
    match = URL_RE.search(text or "")
    return match.group(0) if match else None


def get_public_base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")


def download_video(url: str) -> Path:
    """Download with yt-dlp, remux/merge to mp4 with ffmpeg. Returns the stored file's path."""
    file_id = uuid.uuid4().hex
    outtmpl = str(STORAGE_DIR / f"{file_id}.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": f"bestvideo[height<={MAX_VIDEO_HEIGHT}]+bestaudio/best[height<={MAX_VIDEO_HEIGHT}]/best",
        "merge_output_format": "mp4",
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)

    final_path = STORAGE_DIR / f"{file_id}.mp4"
    if final_path.exists():
        return final_path
    candidates = list(STORAGE_DIR.glob(f"{file_id}.*"))
    if candidates:
        return candidates[0]
    raise yt_dlp.utils.DownloadError("no output file produced")


async def tg_send_message(chat_id: int, text: str, download_url: Optional[str] = None):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if download_url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": "⬇️ Download video", "url": download_url}]]
        }
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)


async def cleanup_loop():
    """Background task: deletes stored files older than FILE_TTL_MINUTES, so disk doesn't fill up."""
    ttl_seconds = FILE_TTL_MINUTES * 60
    while True:
        try:
            now = time.time()
            for f in STORAGE_DIR.glob("*.mp4"):
                if now - f.stat().st_mtime > ttl_seconds:
                    f.unlink(missing_ok=True)
                    log.info("Cleaned up expired file: %s", f.name)
        except Exception:
            log.exception("Cleanup loop error")
        await asyncio.sleep(CLEANUP_INTERVAL_MINUTES * 60)


@app.on_event("startup")
async def on_startup():
    # Auto-register the Telegram webhook using Render's own public URL
    external_url = get_public_base_url()
    if external_url and TELEGRAM_BOT_TOKEN:
        webhook_url = f"{external_url}/telegram/webhook/{TELEGRAM_WEBHOOK_SECRET}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{TELEGRAM_API}/setWebhook", json={"url": webhook_url})
            log.info("setWebhook -> %s | response: %s", webhook_url, r.text)
    else:
        log.warning("Skipping webhook registration (missing RENDER_EXTERNAL_URL or TELEGRAM_BOT_TOKEN)")

    app.state.cleanup_task = asyncio.create_task(cleanup_loop())


@app.on_event("shutdown")
async def on_shutdown():
    task = getattr(app.state, "cleanup_task", None)
    if task:
        task.cancel()


@app.get("/")
def root():
    return {"service": "yt-dlp telegram downloader bot", "status": "running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/files/{filename}")
def serve_file(filename: str):
    if not FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")
    path = STORAGE_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found or expired")
    return FileResponse(path, media_type="video/mp4", filename=filename)


@app.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if not TELEGRAM_WEBHOOK_SECRET or secret != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    update = await request.json()
    message = update.get("message") or update.get("channel_post")
    if not message:
        return {"ok": True}

    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    url = extract_url(text)

    if not url:
        await tg_send_message(
            chat_id,
            "Send me a YouTube, TikTok, or Instagram link and I'll send you a download link for the video.",
        )
        return {"ok": True}

    await tg_send_message(chat_id, "Got it — downloading, this can take a moment...")

    try:
        file_path = download_video(url)
        base_url = get_public_base_url()
        download_url = f"{base_url}/files/{file_path.name}"
        await tg_send_message(
            chat_id,
            f"Your video is ready ✅ (link valid for {FILE_TTL_MINUTES} minutes)",
            download_url=download_url,
        )
    except yt_dlp.utils.DownloadError:
        log.exception("yt-dlp failed for %s", url)
        await tg_send_message(chat_id, "Couldn't download that link — it may be private, region-locked, or unsupported.")
    except Exception:
        log.exception("Unexpected error handling %s", url)
        await tg_send_message(chat_id, "Something went wrong processing that link.")

    return {"ok": True}


@app.post("/download")
async def api_download(request: Request, authorization: str = Header(None)):
    """Optional direct API — e.g. for a Lovable frontend.
    Call with: POST /download  {"url": "..."}  Header: Authorization: Bearer <DOWNLOADER_TOKEN>
    Returns JSON with a direct download link (same link system the Telegram bot uses).
    """
    if not DOWNLOADER_TOKEN or authorization != f"Bearer {DOWNLOADER_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")

    body = await request.json()
    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    try:
        file_path = download_video(url)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=422, detail=f"download failed: {e}")

    base_url = get_public_base_url()
    return {
        "download_url": f"{base_url}/files/{file_path.name}",
        "expires_in_minutes": FILE_TTL_MINUTES,
    }
