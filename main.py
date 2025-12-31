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

# Settings
MAX_FEEDS_PER_RUN = 3  # Reduced for 30-min intervals
OPENAI_TIMEOUT = 15
POST_DELAY = 2  # Reduced delay between posts

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

def fetch_new_feeds(last_id):
    """Find new feeds by trying incremental IDs"""
    new_feeds = []
    current_id = last_id + 1
    max_new_feeds = 5  # Reduced for 30-min intervals
    
    while len(new_feeds) < max_new_feeds:
        feed_url = f"https://www.lookonchain.com/feeds/{current_id}"
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(feed_url, headers=headers, timeout=30, allow_redirects=False)
            
            # If 404 - no more feeds, stop immediately
            if response.status_code == 404:
                logger.info(f"Feed {current_id}: 404 - reached end")
                break
            
            if response.status_code != 200:
                logger.warning(f"Feed {current_id}: status {response.status_code}, stopping")
                break
            
            # Parse the page
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find title
            title_elem = soup.find('h1')
            if not title_elem:
                logger.warning(f"Feed {current_id}: no title found, skipping")
                current_id += 1
                continue
            
            title = html.unescape(title_elem.get_text(strip=True))
            
            # Find time
            time_elem = soup.find('time') or soup.find(string=lambda text: text and 'ago' in text)
            time_text = time_elem if isinstance(time_elem, str) else (time_elem.get_text(strip=True) if time_elem else "")
            
            # Find main content (paragraphs after title)
            content_paragraphs = []
            for p in soup.find_all('p'):
                text = p.get_text(strip=True)
                if text and len(text) > 20:  # Skip very short paragraphs
                    content_paragraphs.append(html.unescape(text))
            
            full_content = '\n\n'.join(content_paragraphs)
            
            if not full_content or len(full_content) < 50:
                logger.warning(f"Feed {current_id}: content too short, skipping")
                current_id += 1
                continue
            
            # Success!
            new_feeds.append({
                'id': current_id,
                'title': title,
                'time': time_text,
                'content': full_content,
                'url': feed_url
            })
            
            logger.info(f"✅ Feed {current_id}: {title[:60]}...")
            
        except Exception as e:
            logger.error(f"Feed {current_id}: error - {e}, stopping")
            break
        
        current_id += 1
        time.sleep(0.5)  # Small delay between requests
    
    logger.info(f"Found {len(new_feeds)} new feeds")
    return new_feeds

def process_with_ai(content):
    """Process content through OpenAI - analyze for Telegram format with sentiment"""
    if len(content) > 3000:
        content = content[:3000] + "..."
        logger.warning("Content truncated to 3000 chars")
    
    for attempt in range(3):
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """You are a crypto analyst. Create a BRIEF Telegram post with sentiment analysis.

CRITICAL RULES:
1. Maximum 280 characters for the main text
2. 2-3 sentences ONLY
3. Include key numbers, tickers, amounts
4. Use professional crypto terminology
5. NO hashtags, NO emojis
6. Completely rewrite - NEVER copy exact phrases from source

SENTIMENT ANALYSIS:
Determine the market context/sentiment from this news:
- Strong negative: Major hacks, crashes, regulatory crackdowns, bankruptcies
- Moderate negative: Price drops, negative predictions, concerns, warnings
- Slight negative: Minor setbacks, caution, uncertainty
- Neutral: Announcements, statistics, routine updates
- Slight positive: Small gains, minor good news, potential opportunities
- Moderate positive: Significant gains, good predictions, partnerships, adoptions
- Strong positive: Major breakthroughs, massive gains, revolutionary developments

OUTPUT FORMAT (JSON):
{
  "text": "Your brief analysis here (max 280 chars)",
  "sentiment": "Moderate negative"
}

Example:
{
  "text": "Arthur Hayes, Tom Lee, and Michael Saylor's 2025 Bitcoin predictions fell short. Hayes forecasted $200K+, Lee predicted $250K, while Saylor expected $150K by year-end.",
  "sentiment": "Moderate negative"
}

If you cannot create original brief text - respond with: {"text": "SKIP", "sentiment": "Neutral"}"""
                    },
                    {
                        "role": "user",
                        "content": f"News content:\n\n{content}\n\nYour analysis (JSON only):"
                    }
                ],
                max_tokens=200,
                temperature=0.7,
                timeout=OPENAI_TIMEOUT
            )
            
            result = response.choices[0].message.content.strip()
            
            # Parse JSON response
            try:
                # Remove markdown code blocks if present
                if result.startswith('```'):
                    result = result.split('```')[1]
                    if result.startswith('json'):
                        result = result[4:]
                    result = result.strip()
                
                import json
                data = json.loads(result)
                
                text = data.get('text', '').strip()
                sentiment = data.get('sentiment', 'Neutral').strip()
                
                if text == "SKIP" or len(text) < 20:
                    logger.warning("AI refused or result too short")
                    return None
                
                # Ensure it's not too long
                if len(text) > 400:
                    logger.warning(f"AI output too long ({len(text)} chars), truncating")
                    text = text[:397] + "..."
                
                logger.info(f"AI output length: {len(text)} chars, sentiment: {sentiment}")
                return {'text': text, 'sentiment': sentiment}
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON: {e}, raw: {result}")
                return None
            
        except Exception as e:
            logger.error(f"OpenAI error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    
    return None

def send_to_telegram(analysis_data, is_error=False):
    """Send message to Telegram"""
    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    chat_id = ADMIN_CHAT_ID if is_error else TARGET_CHAT_ID
    
    if is_error:
        message = analysis_data  # For errors, data is just text
    else:
        # For normal posts, data is dict with text and sentiment
        text = analysis_data.get('text', '')
        sentiment = analysis_data.get('sentiment', 'Neutral')
        
        # Format: [text]\n\nContext: [sentiment]
        message = f"{text}\n\nContext: {sentiment}"
    
    if len(message) > 4096:
        message = message[:4090] + "..."
    
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
    logger.info("Starting Lookonchain Feed Scraper Bot...")
    
    try:
        # Get last processed ID
        last_id_str = get_last_processed_id()
        last_id_int = int(last_id_str) if last_id_str else 0
        logger.info(f"Last processed ID: {last_id_int}")
        
        # FIRST RUN PROTECTION
        if last_id_int == 0:
            # Try to find a recent feed to start from
            test_id = 42194  # Recent known ID
            logger.info(f"First run: testing with ID {test_id}")
            save_last_processed_id(str(test_id))
            logger.warning(f"First run: saved starting ID ({test_id}), no publishing")
            return
        
        # Find new feeds
        new_feeds = fetch_new_feeds(last_id_int)
        
        if not new_feeds:
            logger.info("No new feeds found")
            return
        
        # Get processed hashes for deduplication
        processed_hashes = get_processed_hashes()
        logger.info(f"Loaded {len(processed_hashes)} processed hashes")
        
        published_count = 0
        max_processed_id_int = last_id_int
        
        for i, feed in enumerate(new_feeds[:MAX_FEEDS_PER_RUN]):
            logger.info(f"\n--- Processing feed {feed['id']} ---")
            logger.info(f"Title: {feed['title'][:80]}...")
            logger.info(f"Time: {feed['time']}")
            logger.info(f"Content length: {len(feed['content'])} chars")
            
            # Deduplication by title
            content_hash = get_content_hash(feed['title'])
            if content_hash in processed_hashes:
                logger.info("Duplicate content detected, skipping")
                max_processed_id_int = max(max_processed_id_int, feed['id'])
                continue
            
            # Process FULL content with AI
            ai_analysis = process_with_ai(feed['content'])
            
            if not ai_analysis:
                logger.warning("AI processing failed")
                max_processed_id_int = max(max_processed_id_int, feed['id'])
                continue
            
            logger.info(f"AI analysis: {ai_analysis.get('text', '')[:100]}... | Sentiment: {ai_analysis.get('sentiment', 'N/A')}")
            
            # Send to Telegram
            success = send_to_telegram(ai_analysis)
            
            if success:
                published_count += 1
                logger.info(f"✅ Published ({published_count})")
                
                save_processed_hash(content_hash)
                processed_hashes.add(content_hash)
                
                max_processed_id_int = max(max_processed_id_int, feed['id'])
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
