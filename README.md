# gitwaydownloader
Web Scraper Telegram bot

# Web Scraper & PDF Generator Telegram Bot

A powerful Telegram bot built with **aiogram** that extracts readable content and downloadable files from any given URL. It features a built-in readability engine to strip out web clutter and can automatically generate beautifully formatted PDFs from the extracted text, with full support for RTL languages like Persian and Arabic.

## ✨ Features
*   Smart Text Extraction: Acts like a browser's "Reader Mode" to extract only the main article/content using `BeautifulSoup`.
*   Media & File Grabber: Automatically finds and lists download links for files (ZIP, RAR, MP3, MP4, PDF, APK, etc.) present on the page.
*   RTL-Friendly PDF Generation: Converts extracted text into a PDF file in-memory. Uses `arabic_reshaper` and `python-bidi` for flawless Persian/Arabic text rendering via `reportlab`.
*   Anti-Block Fetching: Uses randomized/custom browser headers to bypass basic anti-bot protections.
*   User Statistics & Admin Panel: Uses SQLite to track user counts, total scrapes, and generated PDFs.

Prerequisites
*   Python 3.8 or higher
*   A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

 Installation & Setup

1. Install Dependencies:**
   Run the following command to install the required Python libraries:
   ```bash
   pip install aiogram aiohttp beautifulsoup4 reportlab arabic-reshaper python-bidi
   ```

2. Configure the Bot:**
   Open `gitwaydownloadermain.py` and replace the placeholder values at the top of the file:
   ```python
   BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
   ADMIN_ID = 123456789  # Replace with your numeric Telegram User ID
   ```

3. **Run the Bot:**
   ```bash
   python gitwaydownloadermain.py
   ```

🚀 Usage
Simply start the bot in Telegram and send it any valid webpage URL. The bot will scan the site, extract the download links (provided as inline buttons), and send you the main text as a generated PDF or in readable message chunks.

Developer's Telegram Channel: @eots1
