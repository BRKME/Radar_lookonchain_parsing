import os
import sys
import time
import hashlib
import logging
import html
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TARGET_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID', TARGET_CHAT_ID)

# Lookonchain website
LOOKONCHAIN_FEEDS_URL = "https://www.lookonchain.com/feeds"

# Settings
MAX_FEEDS_PER_RUN = 5
OPENAI_TIMEOUT = 15
POST_DELAY = 3

# Validate environment variables
required_vars = {
    'OPENAI_API_KEY': OPENAI_API_KEY,
    'TELEGRAM_BOT_TOKEN': BOT_TOKEN,
    'TELEGRAM_CHAT_ID': TARGET_CHAT_ID
}

for var_name, var_value in required_vars.items():
    if not var_value:
        logger.error(f"{var_name} not set")
        sys.exit(1)

openai_client = OpenAI(api_key=OPENAI_API_KEY)

def get_last_processed_id():
    """Get last processed feed ID"""
    try:
        with open('last_feed_id.txt', 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def save_last_processed_id(feed_id):
    """Save last processed feed ID"""
    with open('last_feed_id.txt', 'w') as f:
        f.write(feed_id)
    logger.info(f"Saved last processed ID: {feed_id}")

def get_processed_hashes():
    """Get hashes of processed content"""
    try:
        with open('processed_hashes.txt', 'r') as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()

def save_processed_hash(content_hash):
    """Save hash of processed content"""
    with open('processed_hashes.txt', 'a') as f:
        f.write(f"{content_hash}\n")

def get_content_hash(text):
    """Create hash for deduplication"""
    normalized = ' '.join(text.lower().split())
    return hashlib.md5(normalized.encode()).hexdigest()

def fetch_lookonchain_feeds():
    """Fetch and parse Lookonchain feeds page"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(LOOKONCHAIN_FEEDS_URL, headers=headers, timeout=30)
        response.raise_for_status()
        
        # Check if response is empty
        if not response.text or len(response.text) < 100:
            logger.warning("Empty or too short response from website")
            return []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find ALL feed links (not just hot feeds)
        feed_links = soup.find_all('a', href=True)
        
        feeds = []
        seen_ids = set()
        
        for link in feed_links:
            href = link.get('href', '')
            
            # Check if it's a feed link
            if href.startswith('/feeds/') and href.count('/') == 2:
                try:
                    feed_id = href.split('/')[-1]
                    
                    # Skip if not a number or already seen
                    if not feed_id.isdigit() or feed_id in seen_ids:
                        continue
                    
                    seen_ids.add(feed_id)
                    
                    # Get title
                    title = html.unescape(link.get_text(strip=True))
                    
                    # Skip if title is empty or too short
                    if not title or len(title) < 10:
                        continue
                    
                    # Get date if available
                    date_elem = link.find_next_sibling() or link.find_next()
                    date = ""
                    if date_elem and date_elem.name == 'time':
                        date = date_elem.get_text(strip=True)
                    
                    feeds.append({
                        'id': feed_id,
                        'title': title,
                        'date': date,
                        'url': f"https://www.lookonchain.com/feeds/{feed_id}"
                    })
                    
                except (ValueError, IndexError, AttributeError):
                    continue
        
        # Sort by ID descending (newest first)
        feeds.sort(key=lambda x: int(x['id']), reverse=True)
        
        logger.info(f"Found {len(feeds)} feeds on page")
        return feeds
        
    except Exception as e:
        logger.error(f"Error fetching feeds: {e}")
        return []

def process_with_ai(text):
    """Process text through OpenAI"""
    if len(text) > 2000:
        text = text[:2000] + "..."
        logger.warning("Text truncated to 2000 chars")
    
    for attempt in range(3):
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """Ты криптоаналитик. Создай ПОЛНОСТЬЮ ОРИГИНАЛЬНЫЙ анализ.

КРИТИЧЕСКИЕ ПРАВИЛА:
1. НИКОГДА не используй более 5 слов подряд из исходного текста
2. Полностью ПЕРЕПИШИ информацию своими словами
3. Анализ должен быть на 80%+ отличен от оригинала
4. Сохрани только: точные цифры, тикеры, суммы в USD
5. ВСЁ ОСТАЛЬНОЕ - твои формулировки

Формат: 2-3 предложения МАКСИМУМ
Стиль: Краткий, информативный, профессиональный

Если не можешь создать оригинальный текст - ответь "SKIP"."""
                    },
                    {
                        "role": "user",
                        "content": f"Новость: {text}\n\nТвой анализ:"
                    }
                ],
                max_tokens=300,
                temperature=0.7,
                timeout=OPENAI_TIMEOUT
            )
            
            result = response.choices[0].message.content.strip()
            
            if result == "SKIP" or len(result) < 20:
                logger.warning("AI refused or result too short")
                return None
            
            return result
            
        except Exception as e:
            logger.error(f"OpenAI error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    
    return None

def send_to_telegram(text, is_error=False):
    """Send message to Telegram"""
    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    chat_id = ADMIN_CHAT_ID if is_error else TARGET_CHAT_ID
    
    if not is_error:
        footer = "\n\n📊 Источник: Lookonchain"
        message = text + footer
    else:
        message = text
    
    if len(message) > 4096:
        message = message[:4000] + "..."
        if not is_error:
            message += footer
    
    for attempt in range(3):
        try:
            data = {
                'chat_id': chat_id,
                'text': message,
                'disable_web_page_preview': False
            }
            resp = requests.post(f"{base_url}/sendMessage", data=data, timeout=30)
            if resp.status_code == 200:
                return True
            else:
                logger.error(f"Telegram error: {resp.text}")
        except Exception as e:
            logger.error(f"Telegram error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2)
    
    return False

def notify_error(error_msg):
    """Send error notification"""
    try:
        message = f"🚨 BOT ERROR\n\n{error_msg}"
        send_to_telegram(message, is_error=True)
    except Exception as e:
        logger.error(f"Failed to send error notification: {e}")

def main():
    """Main logic"""
    logger.info("Starting Lookonchain Web Scraper Bot...")
    
    try:
        # Fetch feeds
        feeds = fetch_lookonchain_feeds()
        
        if not feeds:
            logger.warning("No feeds found")
            return
        
        # Get last processed ID
        last_id = get_last_processed_id()
        last_id_int = int(last_id) if last_id else 0
        logger.info(f"Last processed ID: {last_id}")
        
        # Get processed hashes
        processed_hashes = get_processed_hashes()
        logger.info(f"Loaded {len(processed_hashes)} processed hashes")
        
        # Filter new feeds
        new_feeds = []
        for feed in feeds:
            feed_id_int = int(feed['id'])
            if feed_id_int <= last_id_int:
                continue  # Skip old feeds, don't break (might have unsorted IDs)
            new_feeds.append(feed)
        
        new_feeds.reverse()  # Process oldest first
        
        logger.info(f"Found {len(new_feeds)} new feeds")
        
        if not new_feeds:
            logger.info("No new feeds to process")
            return
        
        # FIRST RUN PROTECTION
        if last_id_int == 0 and new_feeds:
            latest_id = new_feeds[-1]['id']  # Last after reverse = newest
            save_last_processed_id(latest_id)
            logger.warning(f"First run: saved latest ID ({latest_id}), no publishing")
            return
        
        published_count = 0
        max_processed_id_int = last_id_int
        
        for i, feed in enumerate(new_feeds[:MAX_FEEDS_PER_RUN]):
            logger.info(f"\n--- Processing feed {feed['id']} ---")
            logger.info(f"Title: {feed['title'][:100]}...")
            logger.info(f"Date: {feed['date']}")
            
            # Deduplication
            content_hash = get_content_hash(feed['title'])
            if content_hash in processed_hashes:
                logger.info("Duplicate content detected, skipping")
                max_processed_id_int = max(max_processed_id_int, int(feed['id']))
                continue
            
            # Process with AI
            ai_analysis = process_with_ai(feed['title'])
            
            if not ai_analysis:
                logger.warning("AI processing failed")
                max_processed_id_int = max(max_processed_id_int, int(feed['id']))
                continue
            
            logger.info(f"AI analysis: {ai_analysis[:100]}...")
            
            # Send to Telegram
            success = send_to_telegram(ai_analysis)
            
            if success:
                published_count += 1
                logger.info(f"✅ Published ({published_count})")
                
                save_processed_hash(content_hash)
                processed_hashes.add(content_hash)
                
                max_processed_id_int = max(max_processed_id_int, int(feed['id']))
            else:
                logger.error("Failed to publish")
                break
            
            # Delay between posts
            if i < min(len(new_feeds), MAX_FEEDS_PER_RUN) - 1:
                time.sleep(POST_DELAY)
        
        # Save final ID
        if max_processed_id_int > last_id_int:
            save_last_processed_id(str(max_processed_id_int))
        
        logger.info(f"\n📊 Summary: Published {published_count}/{len(new_feeds[:MAX_FEEDS_PER_RUN])} feeds")
        
    except Exception as e:
        error_msg = f"Unhandled error: {e}"
        logger.error(error_msg)
        import traceback
        logger.error(traceback.format_exc())
        notify_error(f"{error_msg}\n\n{traceback.format_exc()[:500]}")
        raise

if __name__ == '__main__':
    main()
