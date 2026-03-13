import os
import re
import logging
import httpx
from urllib.parse import urlparse, quote
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
AFFILIATE_ID = os.environ.get("AFFILIATE_ID", "17385530062")
SUB_ID = os.environ.get("SUB_ID", "fb")
RENDER_URL = os.environ.get("RENDER_URL", "")  # vd: https://ten-app.onrender.com

# ──────────────────────────────────────────────
# Shopee URL helpers
# ──────────────────────────────────────────────

def extract_product_ids_from_path(path: str):
    m = re.search(r"/product/(\d+)/(\d+)", path)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"-i\.(\d+)\.(\d+)", path)
    if m:
        return m.group(1), m.group(2)
    return None, None


def clean_shopee_url(full_url: str):
    parsed = urlparse(full_url)
    shop_id, item_id = extract_product_ids_from_path(parsed.path)
    if shop_id and item_id:
        return f"https://shopee.vn/product/{shop_id}/{item_id}"
    return None


async def resolve_short_url(short_url: str):
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ShopeeBot/1.0)"},
        ) as client:
            resp = await client.get(short_url)
            return str(resp.url)
    except Exception as e:
        logger.error(f"Lỗi resolve URL {short_url}: {e}")
        return None


def build_affiliate_url(clean_url: str) -> str:
    encoded = quote(clean_url, safe="")
    return (
        f"https://s.shopee.vn/an_redir"
        f"?origin_link={encoded}"
        f"&affiliate_id={AFFILIATE_ID}"
        f"&sub_id={SUB_ID}"
    )


def is_short_url(url: str) -> bool:
    return urlparse(url).netloc in ("s.shopee.vn", "vn.shp.ee", "shp.ee")


def is_shopee_url(url: str) -> bool:
    netloc = urlparse(url).netloc
    return "shopee.vn" in netloc or netloc in ("s.shopee.vn", "vn.shp.ee", "shp.ee")


def extract_urls(text: str):
    return re.findall(r"https?://[^\s<>\"']+", text)


async def process_url(url: str):
    if not is_shopee_url(url):
        return None
    if is_short_url(url):
        resolved = await resolve_short_url(url)
        if not resolved:
            return None
        clean = clean_shopee_url(resolved)
    else:
        clean = clean_shopee_url(url)
    if not clean:
        return None
    return build_affiliate_url(clean)


# ──────────────────────────────────────────────
# Telegram handler
# ──────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    urls = extract_urls(text)
    shopee_urls = [u for u in urls if is_shopee_url(u)]

    if not shopee_urls:
        await update.message.reply_text(
            "Vui lòng gửi link Shopee để tôi chuyển đổi nhé! 🛍️\n\n"
            "Hỗ trợ các dạng:\n"
            "• https://s.shopee.vn/...\n"
            "• https://vn.shp.ee/...\n"
            "• https://shopee.vn/..."
        )
        return

    affiliate_links = []
    for url in shopee_urls:
        result = await process_url(url)
        if result:
            affiliate_links.append(result)

    if not affiliate_links:
        await update.message.reply_text("❌ Không thể xử lý các link này. Vui lòng thử lại.")
        return

    response = "\n".join(f"`{link}`" for link in affiliate_links)
    await update.message.reply_text(response, parse_mode="Markdown")


# ──────────────────────────────────────────────
# FastAPI + webhook setup
# ──────────────────────────────────────────────

ptb_app = ApplicationBuilder().token(BOT_TOKEN).updater(None).build()
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ptb_app.initialize()
    await ptb_app.start()
    if RENDER_URL:
        webhook_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
        await ptb_app.bot.set_webhook(webhook_url)
        logger.info(f"Webhook đã đăng ký: {webhook_url}")
    else:
        logger.warning("RENDER_URL chưa được set, webhook chưa được đăng ký!")
    yield
    await ptb_app.stop()
    await ptb_app.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"status": "Shopee Bot đang chạy ✅"}


@app.post(f"/webhook/{BOT_TOKEN}")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)
