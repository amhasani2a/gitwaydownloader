import asyncio
import io
import os
import urllib.request
import logging
import sqlite3
import re
import uuid
import html
from urllib.parse import urljoin, urlparse, unquote
import aiohttp
from bs4 import BeautifulSoup

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

BOT_TOKEN = "Token"  
ADMIN_ID = 1234567

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


def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)''')
    c.execute("INSERT OR IGNORE INTO stats (key, value) VALUES ('total_scrapes', 0)")
    c.execute("INSERT OR IGNORE INTO stats (key, value) VALUES ('total_pdfs', 0)")
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def update_stat(key):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("UPDATE stats SET value = value + 1 WHERE key = ?", (key,))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    users_count = c.fetchone()[0]
    c.execute("SELECT key, value FROM stats")
    stats = dict(c.fetchall())
    conn.close()
    return users_count, stats.get('total_scrapes', 0), stats.get('total_pdfs', 0)

def get_all_users():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

class SearchState(StatesGroup):
    waiting_for_url = State()
    waiting_for_query = State()

class AdminBroadcast(StatesGroup):
    waiting_for_message = State()

DOWNLOADABLE_EXTENSIONS = ['.zip', '.rar', '.pdf', '.mp3', '.mp4', '.mkv', '.apk', '.exe', '.doc', '.docx', '.iso']

async def fetch_url(url: str, is_search: bool = False):
    """Guerrilla fetching: Uses realistic headers to bypass anti-bot protections."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(url, timeout=20, allow_redirects=True) as response:
                if response.status in [200, 201, 202]:
                    return await response.text()
                return None
        except Exception as e:
            logging.error(f"Error fetching URL: {e}")
            return None

def extract_data(html_content: str, base_url: str):
    """Robust content extraction acting like a readability engine."""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. Extract Download
    links = soup.find_all('a', href=True)
    download_links = []
    seen_urls = set()
    
    for link in links:
        href = link['href']
        full_url = urljoin(base_url, href)
        if any(full_url.lower().endswith(ext) for ext in DOWNLOADABLE_EXTENSIONS):
            if full_url not in seen_urls:
                seen_urls.add(full_url)
                link_text = link.get_text(strip=True) or "Download File"
                download_links.append((link_text[:30], full_url))
                
    
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'button', 'iframe']):
        tag.decompose()

    
    content_area = soup.find('article') or soup.find('main') or soup.find(id=re.compile('content|main|article', re.I)) or soup.find('body') or soup
    
    
    blocks = content_area.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'li'])
    raw_texts = [block.get_text(separator=' ', strip=True) for block in blocks]
    
    
    clean_paragraphs = [p for p in raw_texts if len(p) > 25]
    text_content = "\n\n".join(clean_paragraphs)
                
    return text_content, download_links

def create_pdf_in_memory(text: str) -> io.BytesIO:
    """Generates PDF supporting Persian and English natively."""
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
    """Splits massive text into Telegram-safe chunks without breaking paragraphs."""
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = ""
    
    for p in paragraphs:
        if len(current_chunk) + len(p) + 2 < max_length:
            current_chunk += p + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = p + "\n\n"
            
    if current_chunk:
        chunks.append(current_chunk.strip())
        
    return chunks

async def process_and_send_url(url: str, message: Message):
    status_msg = await message.answer("🔄 Scanning website structure... Extracting payload.")
    
    html_content = await fetch_url(url)
    if not html_content:
        await status_msg.edit_text("❌ Connection blocked or failed. The site might be using an aggressive firewall.")
        return
        
    update_stat('total_scrapes')
    text_content, download_links = extract_data(html_content, url)
    
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
            update_stat('total_pdfs')
        except Exception as e:
            logging.error(f"PDF Generation Error: {e}")

    await status_msg.delete()

    
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
            await asyncio.sleep(0.3) # Prevent Telegram flood control limits

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    add_user(message.from_user.id)
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
    await message.answer("⌨️ Target locked. Send me your search query:")
    await state.set_state(SearchState.waiting_for_query)

@router.message(SearchState.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
    data = await state.get_data()
    site_url = data['search_url']
    query = message.text
    
    domain = urlparse(site_url).netloc
    status_msg = await message.answer(f"🔍 Scraping DuckDuckGo for `{query}` within `{domain}`...")
    
    search_link = f"https://html.duckduckgo.com/html/?q=site:{domain}+{query.replace(' ', '+')}"
    html_content = await fetch_url(search_link, is_search=True)
    
    if not html_content:
        await status_msg.edit_text("❌ Search engine throttled the request. Try again later.")
        await state.clear()
        return

    soup = BeautifulSoup(html_content, 'html.parser')
    results = soup.find_all('a', class_='result__url', limit=5)
    
    if not results:
        await status_msg.edit_text("⚠️ Zero results matched your query on that site.")
        await state.clear()
        return

    builder = InlineKeyboardBuilder()
    
    for res in results:
        href = res.get('href')
        if href:
            if "uddg=" in href:
                href = unquote(href.split("uddg=")[1].split("&")[0])
            
            short_id = uuid.uuid4().hex[:10]
            search_url_cache[short_id] = href
            
            link_path = urlparse(href).path
            btn_text = f"📄 {link_path[:35]}..." if len(link_path) > 35 else f"📄 {link_path}"
            if btn_text in ["📄 ", "📄 /"]:
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
    await callback.message.answer(f"🔗 Executing on: {url}")
    await process_and_send_url(url, callback.message)

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    users, scrapes, pdfs = get_stats()
    stats_text = (
        "⚙️ **System Diagnostics**\n\n"
        f"👥 Active Users: {users}\n"
        f"🔗 Operations: {scrapes}\n"
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
    users = get_all_users()
    sent = 0
    await message.answer("⏳ Pushing broadcast...")
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
    await process_and_url(url, message)

async def process_and_url(url: str, message: Message):
    await process_and_send_url(url, message)


async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    setup_font() 
    
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    
    print("System Online. Awaiting requests...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())