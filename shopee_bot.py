import os
import re
import logging
import httpx
from urllib.parse import urlparse, urlencode, quote, parse_qs

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
AFFILIATE_ID = os.environ.get("AFFILIATE_ID", "17385530062")
SUB_ID = os.environ.get("SUB_ID", "fb")


def extract_product_ids_from_path(path: str):
    """
    Trích xuất shop_id và item_id từ path dạng:
    /product/SHOP_ID/ITEM_ID
    hoặc /ten-san-pham-i.SHOP_ID.ITEM_ID
    """
    # Dạng /product/SHOP_ID/ITEM_ID
    m = re.search(r"/product/(\d+)/(\d+)", path)
    if m:
        return m.group(1), m.group(2)

    # Dạng slug kết thúc bằng -i.SHOP_ID.ITEM_ID
    m = re.search(r"-i\.(\d+)\.(\d+)", path)
    if m:
        return m.group(1), m.group(2)

    return None, None


def clean_shopee_url(full_url: str) -> str | None:
    """Làm sạch URL Shopee thành dạng chuẩn https://shopee.vn/product/SHOP_ID/ITEM_ID?"""
    parsed = urlparse(full_url)
    shop_id, item_id = extract_product_ids_from_path(parsed.path)
    if shop_id and item_id:
        return f"https://shopee.vn/product/{shop_id}/{item_id}?"
    return None


async def resolve_short_url(short_url: str) -> str | None:
    """Theo dõi redirect để lấy URL gốc từ link rút gọn."""
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
    """Tạo URL affiliate từ clean URL đã làm sạch."""
    # Bỏ dấu ? thừa ở cuối trước khi encode
    origin = clean_url.rstrip("?")
    encoded = quote(origin, safe="")
    return (
        f"https://s.shopee.vn/an_redir"
        f"?origin_link={encoded}"
        f"&affiliate_id={AFFILIATE_ID}"
        f"&sub_id={SUB_ID}"
    )


def is_short_url(url: str) -> bool:
    """Kiểm tra xem URL có phải là link rút gọn không."""
    parsed = urlparse(url)
    return parsed.netloc in ("s.shopee.vn", "vn.shp.ee", "shp.ee")


def is_shopee_url(url: str) -> bool:
    """Kiểm tra xem URL có phải là link Shopee không."""
    parsed = urlparse(url)
    return "shopee.vn" in parsed.netloc or parsed.netloc in (
        "s.shopee.vn",
        "vn.shp.ee",
        "shp.ee",
    )


def extract_urls(text: str) -> list[str]:
    """Trích xuất tất cả URLs từ văn bản."""
    pattern = r"https?://[^\s<>\"']+"
    return re.findall(pattern, text)


async def process_url(url: str) -> str | None:
    """Xử lý một URL và trả về affiliate link hoặc None nếu lỗi."""
    if not is_shopee_url(url):
        return None

    if is_short_url(url):
        resolved = await resolve_short_url(url)
        if not resolved:
            return None
        clean = clean_shopee_url(resolved)
        if not clean:
            return None
    else:
        clean = clean_shopee_url(url)
        if not clean:
            return None

    return build_affiliate_url(clean)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    urls = extract_urls(text)

    if not urls:
        await update.message.reply_text(
            "Vui lòng gửi link Shopee để tôi chuyển đổi nhé! 🛍️\n\n"
            "Hỗ trợ các dạng:\n"
            "• https://s.shopee.vn/...\n"
            "• https://vn.shp.ee/...\n"
            "• https://shopee.vn/..."
        )
        return

    shopee_urls = [u for u in urls if is_shopee_url(u)]
    if not shopee_urls:
        await update.message.reply_text("Không tìm thấy link Shopee hợp lệ trong tin nhắn.")
        return

    affiliate_links = []
    for url in shopee_urls:
        result = await process_url(url)
        if result:
            affiliate_links.append(result)

    if not affiliate_links:
        await update.message.reply_text("❌ Không thể xử lý các link này. Vui lòng thử lại.")
        return

    # Gom tất cả link thành 1 tin nhắn, mỗi link 1 dòng, dạng code để copy dễ
    response = "\n".join(f"`{link}`" for link in affiliate_links)
    await update.message.reply_text(response, parse_mode="Markdown")


async def main():
    if not BOT_TOKEN:
        raise ValueError("Thiếu TELEGRAM_BOT_TOKEN trong biến môi trường!")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot đang chạy...")
    async with app:
        await app.start()
        await app.updater.start_polling()
        await app.updater.idle()
        await app.stop()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
