import os
import re
import logging
import httpx
from urllib.parse import urlparse, quote, parse_qs, unquote
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
AFFILIATE_ID = os.environ.get("AFFILIATE_ID", "17385530062")
SUB_ID = os.environ.get("SUB_ID", "fb")
RENDER_URL = os.environ.get("RENDER_URL", "")

# ──────────────────────────────────────────────
# Shopee URL helpers
# ──────────────────────────────────────────────

def extract_product_ids_from_path(path: str):
    """Trích xuất shop_id, item_id từ path URL."""
    # Dạng /product/SHOP_ID/ITEM_ID
    m = re.search(r"/product/(\d+)/(\d+)", path)
    if m:
        return m.group(1), m.group(2)
    # Dạng slug -i.SHOP_ID.ITEM_ID
    m = re.search(r"-i\.(\d+)\.(\d+)", path)
    if m:
        return m.group(1), m.group(2)
    # Dạng /username/SHOP_ID/ITEM_ID (vd: /opaanlp/1409988503/40154239884)
    clean_path = path.split("?")[0]
    m = re.match(r"^/[^/]+/(\d{6,})/(\d{6,})$", clean_path)
    if m:
        return m.group(1), m.group(2)
    return None, None


def extract_ids_from_query_params(url: str):
    """
    Xử lý trường hợp Shopee redirect về dạng có origin_link, target, link trong query.
    Ví dụ: https://s.shopee.vn/an_redir?origin_link=https%3A%2F%2Fshopee.vn%2Fproduct%2F...
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    for key in ("origin_link", "target", "link", "url"):
        if key in params:
            inner = unquote(params[key][0])
            logger.info(f"Inner URL từ param '{key}': {inner}")
            shop_id, item_id = extract_product_ids_from_path(urlparse(inner).path)
            if shop_id and item_id:
                return shop_id, item_id

    return None, None


def clean_shopee_url(full_url: str):
    """Làm sạch URL Shopee thành dạng chuẩn https://shopee.vn/product/SHOP_ID/ITEM_ID"""
    parsed = urlparse(full_url)

    # Thử trích xuất từ path
    shop_id, item_id = extract_product_ids_from_path(parsed.path)
    if shop_id and item_id:
        return f"https://shopee.vn/product/{shop_id}/{item_id}"

    # Thử tìm trong query params
    shop_id, item_id = extract_ids_from_query_params(full_url)
    if shop_id and item_id:
        return f"https://shopee.vn/product/{shop_id}/{item_id}"

    logger.warning(f"Không trích xuất được ID từ URL: {full_url}")
    return None


async def resolve_short_url(short_url: str):
    """Theo dõi tất cả redirects để lấy URL cuối cùng."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "vi-VN,vi;q=0.9",
            },
        ) as client:
            resp = await client.get(short_url)
            final_url = str(resp.url)
            logger.info(f"Resolved {short_url} → {final_url}")
            return final_url
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

        # Nếu resolved URL vẫn là short URL hoặc không parse được, thử lấy từ history
        if not clean:
            logger.warning(f"Không parse được từ resolved URL: {resolved}")
            return None
    else:
        clean = clean_shopee_url(url)

    if not clean:
        return None

    return build_affiliate_url(clean)


# ──────────────────────────────────────────────
# Telegram handler
# ──────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hãy gửi cho tôi Link Shopee mà bạn Muốn Chuyển Đổi !!")


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
    failed = []
    for url in shopee_urls:
        result = await process_url(url)
        if result:
            affiliate_links.append(result)
        else:
            failed.append(url)

    if not affiliate_links and not failed:
        await update.message.reply_text("❌ Không thể xử lý các link này. Vui lòng thử lại.")
        return

    parts = []

    if affiliate_links:
        links_text = "\n".join(f"`{link}`" for link in affiliate_links)
        footer = "⚡️Copy Link Trên Và Dán Lên CMT Gr Facebook: fb.com/groups/sansaleshopeelazada1"
        parts.append(f"⚡️Click để tự động Copy:\n{links_text}\n\n{footer}")

    if failed:
        warning = (
            "⚠️Đây không phải link Sản phẩm, Vui Lòng Copy Link Sản phẩm từ [App Shopee](https://s.shopee.vn/4AvBcURYH9)"
        )
        parts.append(warning)

    await update.message.reply_text("\n\n".join(parts), parse_mode="Markdown")


# ──────────────────────────────────────────────
# FastAPI + webhook setup
# ──────────────────────────────────────────────

ptb_app = ApplicationBuilder().token(BOT_TOKEN).updater(None).build()
ptb_app.add_handler(CommandHandler("start", start))
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
