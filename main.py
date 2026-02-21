import os
import sys
import time
import json
import traceback
import logging
import html
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TARGET_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID', TARGET_CHAT_ID)

MAX_FEEDS_PER_RUN = 12
OPENAI_TIMEOUT = 15
POST_DELAY = 2

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
    try:
        with open('last_feed_id.txt', 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def save_last_processed_id(feed_id):
    with open('last_feed_id.txt', 'w') as f:
        f.write(feed_id)
    logger.info(f"Saved last processed ID: {feed_id}")

def get_processed_hashes():
    try:
        with open('processed_hashes.txt', 'r') as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()

def save_processed_hash(content_hash):
    with open('processed_hashes.txt', 'a') as f:
        f.write(f"{content_hash}\n")

def fetch_new_feeds(last_id):
    new_feeds = []
    current_id = last_id + 1
    max_new_feeds = 15
    consecutive_errors = 0
    max_consecutive_errors = 5
    max_attempts = 50
    
    attempts = 0
    while len(new_feeds) < max_new_feeds and attempts < max_attempts:
        attempts += 1
        feed_url = f"https://www.lookonchain.com/feeds/{current_id}"
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(feed_url, headers=headers, timeout=30, allow_redirects=False)
            
            if response.status_code == 404:
                consecutive_errors += 1
                logger.info(f"Feed {current_id}: 404 (consecutive errors: {consecutive_errors})")
                if consecutive_errors >= max_consecutive_errors:
                    logger.info(f"Reached end after {consecutive_errors} consecutive 404s")
                    break
                current_id += 1
                time.sleep(0.5)
                continue
            
            if response.status_code != 200:
                consecutive_errors += 1
                logger.warning(f"Feed {current_id}: status {response.status_code} (consecutive errors: {consecutive_errors})")
                if consecutive_errors >= max_consecutive_errors:
                    break
                current_id += 1
                time.sleep(0.5)
                continue
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            title_elem = soup.find('h1')
            if not title_elem:
                consecutive_errors += 1
                logger.warning(f"Feed {current_id}: no title (consecutive errors: {consecutive_errors})")
                if consecutive_errors >= max_consecutive_errors:
                    break
                current_id += 1
                time.sleep(0.5)
                continue
            
            title = html.unescape(title_elem.get_text(strip=True))
            
            time_elem = soup.find('time') or soup.find(string=lambda text: text and 'ago' in text)
            time_text = time_elem if isinstance(time_elem, str) else (time_elem.get_text(strip=True) if time_elem else "")
            
            detail_content_div = soup.find('div', class_='detail_content')
            
            if detail_content_div:
                full_content = html.unescape(detail_content_div.get_text(strip=True))
                logger.info(f"Found detail_content div ({len(full_content)} chars)")
            else:
                logger.warning(f"Feed {current_id}: no detail_content div, using fallback")
                content_paragraphs = []
                stop_markers = ['relevant content', 'source:', 'add to favorites']
                
                for p in soup.find_all('p'):
                    text = p.get_text(strip=True)
                    if text and len(text) > 30:
                        text_lower = text.lower()
                        if any(marker in text_lower for marker in stop_markers):
                            break
                        content_paragraphs.append(html.unescape(text))
                        if len(content_paragraphs) >= 5:
                            break
                
                if not content_paragraphs:
                    consecutive_errors += 1
                    logger.warning(f"Feed {current_id}: no content (consecutive errors: {consecutive_errors})")
                    if consecutive_errors >= max_consecutive_errors:
                        break
                    current_id += 1
                    time.sleep(0.5)
                    continue
                
                full_content = '\n\n'.join(content_paragraphs)
                logger.info(f"Fallback: collected {len(content_paragraphs)} paragraphs ({len(full_content)} chars)")
            
            if not full_content or len(full_content) < 50:
                consecutive_errors += 1
                logger.warning(f"Feed {current_id}: content too short ({len(full_content)} chars), skipping (consecutive errors: {consecutive_errors})")
                if consecutive_errors >= max_consecutive_errors:
                    break
                current_id += 1
                time.sleep(0.5)
                continue
            
            consecutive_errors = 0
            
            new_feeds.append({
                'id': current_id,
                'title': title,
                'time': time_text,
                'content': full_content,
                'url': feed_url
            })
            
            logger.info(f"✅ Feed {current_id}: {title[:60]}...")
            
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Feed {current_id}: error - {e} (consecutive errors: {consecutive_errors})")
            if consecutive_errors >= max_consecutive_errors:
                break
            current_id += 1
            time.sleep(0.5)
            continue
        
        current_id += 1
        time.sleep(0.5)
    
    logger.info(f"Found {len(new_feeds)} new feeds after {attempts} attempts")
    return new_feeds

def process_with_ai(content, feed_title):
    if len(content) > 2000:
        content = content[:2000]
        logger.warning(f"Content truncated to 2000 chars")
    
    for attempt in range(3):
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """You are a crypto analyst. Create a BRIEF Telegram post with sentiment analysis.

CRITICAL RULES:
1. Write ONLY about the article TITLE topic
2. Maximum 280 characters
3. 2-3 sentences with key numbers/tickers
4. IGNORE any unrelated content (if Bitcoin/Ethereum not in title, don't mention them)
5. If content doesn't match title → respond: {"text": "SKIP", "sentiment": "Neutral"}

SENTIMENT GUIDELINES (Crypto-specific):

PRICE MOVEMENTS (24h):
- Strong negative: >5% drop OR critical level break (BTC <$75K, ETH <$2200, SOL <$120)
- Moderate negative: 3-5% drop OR approaching key support
- Slight negative: 1-3% drop OR minor concerns
- Neutral: <1% change OR routine announcements
- Slight positive: 1-3% gain OR minor good news
- Moderate positive: 3-5% gain OR partnerships
- Strong positive: >5% gain OR major breakthroughs

UNREALIZED LOSSES:
- Strong negative: >$1B unrealized loss OR >50% loss on position
- Moderate negative: $100M-$1B unrealized loss OR 30-50% loss
- Slight negative: <$100M unrealized loss OR <30% loss

WHALE ACTIVITY:
- Strong negative: Major liquidations (>100), panic selling, >$1B moves with losses
- Moderate negative: Large CEX outflows, selling pressure, loan repayments
- Slight negative: Profit taking, small position reduction
- Neutral: Routine transfers, rebalancing without losses
- Slight positive: Small accumulation, DCA buying
- Moderate positive: Whale accumulation, institutional buying
- Strong positive: Massive buying, supply squeeze

STABLECOIN ACTIVITY:
- Neutral to Slight positive: Large USDT/USDC mints (indicates incoming liquidity)
- Slight negative: Large redemptions (liquidity leaving)

SECTOR-WIDE EVENTS:
- Strong negative: Mining stocks/multiple coins down >10%, sector crash
- Moderate negative: Sector down 5-10%
- Slight negative: Sector down <5%

TRADITIONAL MARKETS (Gold, Stocks):
- Use HALF the thresholds (e.g., -2% gold = Slight negative, not Moderate)

HACKS/EXPLOITS:
- Always "Strong negative" regardless of amount

SENTIMENT (pick one):
- Strong negative: Drops >5%, hacks, bankruptcies, >$1B losses, critical breaks, 262 liquidations, sector crash
- Moderate negative: Drops 3-5%, large outflows, warnings, $100M-$1B losses
- Slight negative: Drops 1-3%, minor setbacks, <$100M losses
- Neutral: <1% moves, announcements, routine updates, stablecoin mints
- Slight positive: Gains 1-3%, small opportunities, liquidity inflows
- Moderate positive: Gains 3-5%, partnerships, institutional interest
- Strong positive: Gains >5%, massive breakthroughs, supply shock

OUTPUT (JSON only):
{
  "text": "Your analysis (max 280 chars, about TITLE topic only)",
  "sentiment": "Strong negative"
}"""
                    },
                    {
                        "role": "user",
                        "content": f"Article Title: {feed_title}\n\nNews content:\n\n{content}\n\nYour analysis (JSON only, must relate to the title):"
                    }
                ],
                max_tokens=200,
                temperature=0.9,
                timeout=OPENAI_TIMEOUT
            )
            
            result = response.choices[0].message.content.strip()
            
            try:
                if result.startswith('```'):
                    parts = result.split('```')
                    if len(parts) >= 2:
                        result = parts[1]
                        if result.startswith('json'):
                            result = result[4:]
                        result = result.strip()
                
                data = json.loads(result)
                
                text = data.get('text', '').strip()
                sentiment = data.get('sentiment', 'Neutral').strip()
                
                if text == "SKIP" or len(text) < 20:
                    logger.warning(f"AI refused or result too short")
                    return None
                
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

def get_emoji_for_sentiment(sentiment):
    sentiment_lower = sentiment.lower()
    
    if 'strong negative' in sentiment_lower:
        return '⚠️'
    elif 'negative' in sentiment_lower:
        return '📊'
    elif 'strong positive' in sentiment_lower:
        return '🚀'
    elif 'positive' in sentiment_lower:
        return '📰'
    else:
        return '📰'

def get_hashtags_from_title(title):
    title_lower = title.lower()
    hashtags = []
    
    has_major_coin = False
    
    if 'bitcoin' in title_lower or 'btc' in title_lower:
        hashtags.append('#BTC')
        has_major_coin = True
    if 'ethereum' in title_lower or 'eth' in title_lower:
        hashtags.append('#ETH')
        has_major_coin = True
    if 'solana' in title_lower or ' sol ' in title_lower or title_lower.endswith('sol'):
        hashtags.append('#SOL')
        has_major_coin = True
    
    meme_coins = ['doge', 'shib', 'pepe', 'penguin', 'bonk', 'floki', 'meme']
    if any(meme in title_lower for meme in meme_coins):
        hashtags.append('#Memecoins')
    
    if any(nft in title_lower for nft in ['nft', 'opensea', 'blur', 'nifty']):
        hashtags.append('#NFT')
    
    defi_terms = ['defi', 'staking', 'liquidity', 'aave', 'uniswap', 'compound', 'yield']
    if any(defi in title_lower for defi in defi_terms):
        hashtags.append('#DeFi')
    
    if not has_major_coin and any(alt in title_lower for alt in ['altcoin', 'token', 'coin']):
        hashtags.append('#Altcoins')
    
    macro_terms = ['fed', 'fomc', 'powell', 'interest rate', 'forex', 'global', 'dollar', 'treasury']
    if any(macro in title_lower for macro in macro_terms):
        hashtags.append('#Markets')
    
    whale_terms = ['whale', 'million', 'billion', 'accumulated', 'transferred']
    if any(whale in title_lower for whale in whale_terms) and '#Memecoins' not in hashtags:
        if len(hashtags) < 3:
            hashtags.append('#Whales')
    
    if not hashtags:
        hashtags.append('#Markets')
    
    return ' '.join(hashtags[:3])

def send_to_telegram(analysis_data, feed_title=None, is_error=False):
    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    chat_id = ADMIN_CHAT_ID if is_error else TARGET_CHAT_ID
    
    if is_error:
        message = analysis_data
    else:
        text = analysis_data.get('text', '')
        sentiment = analysis_data.get('sentiment', 'Neutral')
        
        emoji = get_emoji_for_sentiment(sentiment)
        hashtags = get_hashtags_from_title(feed_title) if feed_title else ''
        
        max_title_len = 200
        if feed_title and len(feed_title) > max_title_len:
            feed_title = feed_title[:max_title_len] + "..."
        
        # Хэштеги вверху сообщения
        if feed_title:
            if hashtags:
                message = f"{hashtags}\n\n{emoji} {feed_title}\n\n{text}\n\nContext: {sentiment}"
            else:
                message = f"{emoji} {feed_title}\n\n{text}\n\nContext: {sentiment}"
        else:
            if hashtags:
                message = f"{hashtags}\n\n{emoji} {text}\n\nContext: {sentiment}"
            else:
                message = f"{emoji} {text}\n\nContext: {sentiment}"
        
        if len(message) > 4096:
            footer = f"\n\nContext: {sentiment}"
            header = f"{hashtags}\n\n{emoji} {feed_title}\n\n" if feed_title else f"{hashtags}\n\n{emoji} "
            if not hashtags:
                header = f"{emoji} {feed_title}\n\n" if feed_title else f"{emoji} "
            max_text_len = 4096 - len(header) - len(footer) - 3
            
            if max_text_len > 100:
                text = text[:max_text_len] + "..."
                message = f"{header}{text}{footer}"
            else:
                feed_title = feed_title[:100] + "..." if feed_title else ""
                header = f"{hashtags}\n\n{emoji} {feed_title}\n\n" if feed_title else f"{hashtags}\n\n{emoji} "
                if not hashtags:
                    header = f"{emoji} {feed_title}\n\n" if feed_title else f"{emoji} "
                max_text_len = 4096 - len(header) - len(footer) - 3
                text = text[:max_text_len] + "..."
                message = f"{header}{text}{footer}"
    
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
    try:
        message = f"🚨 BOT ERROR\n\n{error_msg}"
        send_to_telegram(message, is_error=True)
    except Exception as e:
        logger.error(f"Failed to send error notification: {e}")

def main():
    logger.info("Starting Lookonchain Feed Scraper Bot...")
    
    try:
        last_id_str = get_last_processed_id()
        try:
            last_id_int = int(last_id_str) if last_id_str else 0
        except (ValueError, TypeError):
            logger.error(f"Invalid last_feed_id: '{last_id_str}', resetting to 0")
            last_id_int = 0
        logger.info(f"Last processed ID: {last_id_int}")
        
        if last_id_int == 0:
            test_id = 42194
            logger.info(f"First run: testing with ID {test_id}")
            save_last_processed_id(str(test_id))
            logger.warning(f"First run: saved starting ID ({test_id}), no publishing")
            return
        
        new_feeds = fetch_new_feeds(last_id_int)
        
        if not new_feeds:
            logger.info("No new feeds found")
            return
        
        processed_hashes = get_processed_hashes()
        logger.info(f"Loaded {len(processed_hashes)} processed hashes")
        
        published_count = 0
        max_processed_id_int = last_id_int
        
        for i, feed in enumerate(new_feeds[:MAX_FEEDS_PER_RUN]):
            logger.info(f"\n--- Processing feed {feed['id']} ---")
            logger.info(f"Title: {feed['title'][:80]}...")
            logger.info(f"Time: {feed['time']}")
            logger.info(f"Content length: {len(feed['content'])} chars")
            
            feed_id_str = str(feed['id'])
            if feed_id_str in processed_hashes:
                logger.info("Feed already processed, skipping")
                max_processed_id_int = max(max_processed_id_int, feed['id'])
                continue
            
            if 'meme coin' in feed['title'].lower():
                logger.info(f"⊘ Skipping meme coin news: {feed['title'][:80]}...")
                save_processed_hash(feed_id_str)
                processed_hashes.add(feed_id_str)
                max_processed_id_int = max(max_processed_id_int, feed['id'])
                continue
            
            # Фильтр шума: проверяем И title И первое предложение content
            title_text = feed['title'] if feed['title'] else ''
            first_sentence = feed['content'].split('.')[0] if feed['content'] else ''
            combined_text = title_text + ' ' + first_sentence
            combined_lower = combined_text.lower()
            
            noise_keywords = [
                'whale trader',
                'a whale',
                'the whale',       # Добавлено!
                'on-chain whale',
                'ultimate shorter',
                'antminer',
                '「',              # Специальный символ
            ]
            
            # Проверяем ключевые слова (case-insensitive)
            has_noise_keyword = any(kw in combined_lower for kw in noise_keywords)
            
            # Также проверяем символ 「 отдельно (он не в lower)
            has_special_char = '「' in combined_text
            
            # Проверяем "whale" как отдельное слово в начале предложения
            words = combined_text.strip().split()
            first_word = words[0].lower() if words else ''
            is_whale_start = first_word == 'whale'
            
            is_noise = has_noise_keyword or has_special_char or is_whale_start
            
            if is_noise:
                logger.info(f"⊘ Skipping noise (whale/antminer): {feed['title'][:60]}...")
                save_processed_hash(feed_id_str)
                processed_hashes.add(feed_id_str)
                max_processed_id_int = max(max_processed_id_int, feed['id'])
                continue
            
            logger.info(f"=== TITLE ===")
            logger.info(feed['title'])
            logger.info(f"=== CONTENT FOR AI (first 500 chars) ===")
            logger.info(feed['content'][:500])
            logger.info(f"=== END CONTENT (total {len(feed['content'])} chars) ===")
            
            ai_analysis = process_with_ai(feed['content'], feed['title'])
            
            if not ai_analysis:
                logger.warning(f"AI processing failed for feed {feed_id_str}, will retry next run")
                max_processed_id_int = max(max_processed_id_int, feed['id'])
                continue
            
            logger.info(f"AI analysis: {ai_analysis.get('text', '')[:100]}... | Sentiment: {ai_analysis.get('sentiment', 'N/A')}")
            
            save_processed_hash(feed_id_str)
            processed_hashes.add(feed_id_str)
            max_processed_id_int = max(max_processed_id_int, feed['id'])
            
            success = send_to_telegram(ai_analysis, feed_title=feed['title'])
            
            if success:
                published_count += 1
                logger.info(f"✅ Published ({published_count})")
            else:
                logger.error("Failed to publish to Telegram (feed marked as processed)")
            
            if i < min(len(new_feeds), MAX_FEEDS_PER_RUN) - 1:
                time.sleep(POST_DELAY)
        
        if max_processed_id_int > last_id_int:
            save_last_processed_id(str(max_processed_id_int))
        
        logger.info(f"\n📊 Summary: Published {published_count}/{len(new_feeds[:MAX_FEEDS_PER_RUN])} feeds")
        
    except Exception as e:
        error_msg = f"Unhandled error: {e}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        notify_error(f"{error_msg}\n\n{traceback.format_exc()[:500]}")
        raise

if __name__ == '__main__':
    main()
