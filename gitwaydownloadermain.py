import asyncio
import io
import os
import urllib.request
import logging
import re
import uuid
import html
from urllib.parse import urljoin, urlparse

import aiosqlite
from bs4 import BeautifulSoup
import trafilatura
from playwright.async_api import async_playwright
from duckduckgo_search import DDGS

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import arabic_reshaper
from bidi.algorithm import get_display

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# config
BOT_TOKEN = "token"  
ADMIN_ID = 123456789              

FONT_NAME = 'Vazirmatn-Regular.ttf'
FONT_URL = "https://github.com/rastikerdar/vazirmatn/raw/master/fonts/ttf/Vazirmatn-Regular.ttf"

search_url_cache = {}


def setup_font():
    """Downloads Persian font from CDN if it doesn't exist locally."""
    if not os.path.exists(FONT_NAME):
        logging.info("Downloading Persian font from CDN...")
        try:
            urllib.request.urlretrieve(FONT_URL, FONT_NAME)
            logging.info("Font downloaded successfully.")
        except Exception as e:
            logging.error(f"Failed to download font: {e}")

# Async Database Setup
async def init_db():
    async with aiosqlite.connect('bot_data.db') as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)''')
        await db.execute("INSERT OR IGNORE INTO stats (key, value) VALUES ('total_scrapes', 0)")
        await db.execute("INSERT OR IGNORE INTO stats (key, value) VALUES ('total_pdfs', 0)")
        await db.commit()

async def add_user(user_id):
    async with aiosqlite.connect('bot_data.db') as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def update_stat(key):
    async with aiosqlite.connect('bot_data.db') as db:
        await db.execute("UPDATE stats SET value = value + 1 WHERE key = ?", (key,))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect('bot_data.db') as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT key, value FROM stats") as cursor:
            stats = dict(await cursor.fetchall())
    return users_count, stats.get('total_scrapes', 0), stats.get('total_pdfs', 0)

async def get_all_users():
    async with aiosqlite.connect('bot_data.db') as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = [row[0] for row in await cursor.fetchall()]
    return users

# FSM States
class SearchState(StatesGroup):
    waiting_for_url = State()
    waiting_for_query = State()

class AdminBroadcast(StatesGroup):
    waiting_for_message = State()

# Core Scraper & PDF Logic
DOWNLOADABLE_EXTENSIONS = ['.zip', '.rar', '.pdf', '.mp3', '.mp4', '.mkv', '.apk', '.exe', '.doc', '.docx', '.iso']

async def fetch_url_modern(url: str):
    """Playwright Engine with Root-Bypass to render JS natively."""
    try:
        async with async_playwright() as p:
            
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = await context.new_page()
            
            
            await page.goto(url, wait_until="domcontentloaded", timeout=40000)
            
            await page.wait_for_timeout(2000)
            
            content = await page.content()
            await browser.close()
            return content
    except Exception as e:
        logging.error(f"Error fetching URL with Playwright: {e}")
        return None

def extract_data_modern(html_content: str, base_url: str):
    """State-of-the-Art extraction via Trafilatura + BeautifulSoup for media"""
    
    text_content = trafilatura.extract(html_content, include_comments=False, include_tables=True)
    if not text_content:
        text_content = ""
    
    
    soup = BeautifulSoup(html_content, 'html.parser')
    links = soup.find_all('a', href=True)
    download_links = []
    seen_urls = set()
    
    for link in links:
        href = link.get('href')
        if href:
            full_url = urljoin(base_url, href)
            if any(full_url.lower().endswith(ext) for ext in DOWNLOADABLE_EXTENSIONS):
                if full_url not in seen_urls:
                    seen_urls.add(full_url)
                    link_text = link.get_text(strip=True) or "Download File"
                    download_links.append((link_text[:30], full_url))
                
    return text_content, download_links

def create_pdf_in_memory(text: str) -> io.BytesIO:
    """Renders Bi-Directional text properly into PDF Format"""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    
    try:
        pdfmetrics.registerFont(TTFont('PersianFont', FONT_NAME))
        font_name = 'PersianFont'
    except Exception as e:
        logging.warning(f"Font not found: {e}. Using default Helvetica.")
        font_name = 'Helvetica'

    custom_style = ParagraphStyle(
        'CustomStyle',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=12,
        leading=20,
        alignment=2,
        wordWrap='LTR'
    )
    
    flowables = []
    
    cleaned_text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    
    for paragraph in cleaned_text.split('\n\n'):
        if paragraph.strip():
            reshaped_text = arabic_reshaper.reshape(paragraph.strip())
            bidi_text = get_display(reshaped_text)
            flowables.append(Paragraph(bidi_text, custom_style))
            flowables.append(Spacer(1, 12))
            
    doc.build(flowables)
    buffer.seek(0)
    return buffer

def split_text_for_telegram(text: str, max_length: int = 3900):
    """Safely splits large blocks of text to bypass Telegram size limits"""
    paragraphs = text.split('\n')
    chunks = []
    current_chunk = ""
    
    for p in paragraphs:
        if len(current_chunk) + len(p) + 2 < max_length:
            current_chunk += p + "\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = p + "\n"
            
    if current_chunk:
        chunks.append(current_chunk.strip())
        
    return chunks

async def process_and_send_url(url: str, message: Message):
    status_msg = await message.answer("🔄 Scanning website structure... Extracting payload.")
    
    html_content = await fetch_url_modern(url)
    if not html_content:
        await status_msg.edit_text("❌ Connection blocked or failed. The site might be using an aggressive firewall.")
        return
        
    await update_stat('total_scrapes')
    text_content, download_links = extract_data_modern(html_content, url)
    
    if not text_content and not download_links:
        await status_msg.edit_text("⚠️ Extraction complete, but no readable text or supported files were found.")
        return

    builder = InlineKeyboardBuilder()
    for link_name, link_url in download_links[:20]:
        builder.button(text=f"⬇️ {link_name}", url=link_url)
    builder.adjust(1)

    pdf_buffer = None
    if text_content:
        try:
            pdf_buffer = await asyncio.to_thread(create_pdf_in_memory, text_content)
            await update_stat('total_pdfs')
        except Exception as e:
            logging.error(f"PDF Generation Error: {e}")

    try:
        await status_msg.delete()
    except:
        pass

    # 1. Send Document & Links
    if pdf_buffer:
        pdf_file = BufferedInputFile(pdf_buffer.getvalue(), filename="Extracted_Content.pdf")
        caption = f"🌐 **Source:** {url}\n✅ Generated PDF and found {len(download_links)} files."
        await message.answer_document(
            document=pdf_file,
            caption=caption,
            reply_markup=builder.as_markup() if download_links else None,
            parse_mode="Markdown"
        )
    elif download_links:
        await message.answer(
            f"🌐 **Source:** {url}\n✅ Found Download Links:",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

    # 2. Send Text sequentially to prevent flood warnings
    if text_content:
        chunks = split_text_for_telegram(text_content)
        for idx, chunk in enumerate(chunks):
            header = f"📄 **Part {idx+1}/{len(chunks)}:**\n\n" if len(chunks) > 1 else "📝 **Extracted Text:**\n\n"
            safe_chunk = html.escape(chunk)
            await message.answer(
                f"{header}{safe_chunk}",
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            await asyncio.sleep(0.5)

# Telegram Handlers
router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    await add_user(message.from_user.id)
    welcome_text = (
        "👋 Welcome to the Web Extractor Bot!\n\n"
        "Send me any website URL, and I will:\n\n"
        "1️⃣ Extract the text from the site (Note: it may not work on some websites).\n\n"
        "2️⃣ Generate a PDF of the article.\n\n"
        "3️⃣ Provide download buttons for your files.\n\n"
        "🚀 Created by: @eots1\n\n"
        "🔍 Use /search to target a specific domain."
    )
    await message.answer(welcome_text)

@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext):
    await message.answer("🌐 Provide the target base URL (e.g., https://example.com):")
    await state.set_state(SearchState.waiting_for_url)

@router.message(SearchState.waiting_for_url)
async def process_search_url(message: Message, state: FSMContext):
    if not message.text.startswith("http"):
        await message.answer("❌ Invalid protocol. Please include http:// or https://")
        return
    await state.update_data(search_url=message.text)
    await message.answer("🎯 Target locked. Send me your search query:")
    await state.set_state(SearchState.waiting_for_query)

#  DDG Search Logic 
def fetch_ddg_results(search_query):
    """Synchronous DDG wrapper to run safely in an async thread."""
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(search_query, max_results=5):
                results.append(r)
    except Exception as e:
        logging.error(f"DDGS Search Error: {e}")
    return results

@router.message(SearchState.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
    data = await state.get_data()
    site_url = data['search_url']
    query = message.text
    
    domain = urlparse(site_url).netloc
    status_msg = await message.answer(f"🔍 Scraping DuckDuckGo for `{query}` within `{domain}`...")
    
    search_query = f"site:{domain} {query}"
    
    
    results = await asyncio.to_thread(fetch_ddg_results, search_query)

    if not results:
        await status_msg.edit_text("⚠️ Zero results matched your query on that site or engine throttled.")
        await state.clear()
        return

    builder = InlineKeyboardBuilder()
    
    for res in results:
        href = res.get('href')
        if href:
            short_id = uuid.uuid4().hex[:10]
            search_url_cache[short_id] = href
            
            link_path = urlparse(href).path
            btn_text = f"📄 {link_path[:35]}..." if len(link_path) > 35 else f"📄 {link_path}"
            if btn_text in ["📄 ", "📄 /", "📄"]:
                btn_text = f"📄 {domain} (Home)"
                
            builder.button(text=btn_text, callback_data=f"ext_{short_id}")
            
    builder.adjust(1)
    
    await status_msg.edit_text(
        f"✅ Results retrieved for `{query}`.\n👇 Tap a link below to execute extraction:",
        reply_markup=builder.as_markup()
    )
    await state.clear()

@router.callback_query(F.data.startswith("ext_"))
async def process_search_result_click(callback: CallbackQuery):
    short_id = callback.data.split("_")[1]
    url = search_url_cache.get(short_id)
    
    if not url:
        await callback.answer("❌ Session dropped from cache. Please search again.", show_alert=True)
        return
        
    await callback.answer()
    await callback.message.answer(f"⚙️ Executing on: {url}")
    await process_and_send_url(url, callback.message)

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    users, scrapes, pdfs = await get_stats()
    stats_text = (
        "📊 **System Diagnostics**\n\n"
        f"👥 Active Users: {users}\n"
        f"⚙️ Operations: {scrapes}\n"
        f"📄 Documents Generated: {pdfs}\n\n"
        "Use /broadcast to push a global payload."
    )
    await message.answer(stats_text, parse_mode="Markdown")

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("📢 Awaiting broadcast payload:")
    await state.set_state(AdminBroadcast.waiting_for_message)

@router.message(AdminBroadcast.waiting_for_message)
async def process_broadcast(message: Message, state: FSMContext):
    users = await get_all_users()
    sent = 0
    await message.answer("🚀 Pushing broadcast...")
    for user_id in users:
        try:
            await message.copy_to(user_id)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ Push complete. Reached {sent} hosts.")
    await state.clear()

@router.message(F.text.regexp(r'^https?://'))
async def handle_url_message(message: Message):
    url = message.text.strip()
    await process_and_send_url(url, message)

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    setup_font() 
    
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    
    print("System Online. Awaiting requests...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())