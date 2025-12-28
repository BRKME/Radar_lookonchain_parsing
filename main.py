import os
import sys
import time
import base64
import json
import hashlib
import random
import asyncio
from collections import deque
from playwright.async_api import async_playwright
from openai import OpenAI
import requests
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TARGET_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TWITTER_USERNAME = 'lookonchain'

# Anti-detection: User-Agent rotation
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15'
]

required_vars = {
    'OPENAI_API_KEY': OPENAI_API_KEY,
    'TELEGRAM_BOT_TOKEN': BOT_TOKEN,
    'TELEGRAM_CHAT_ID': TARGET_CHAT_ID
}

for var_name, var_value in required_vars.items():
    if not var_value:
        print(f"ERROR: {var_name} not set")
        sys.exit(1)

openai_client = OpenAI(api_key=OPENAI_API_KEY)

def get_last_processed_hashes():
    """Get deque of recently processed content hashes (ordered, max 10)"""
    try:
        with open('last_content.txt', 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return deque(maxlen=10)
            # Load hashes in order, keep max 10
            hashes = [line.strip() for line in content.split('\n') if line.strip()]
            return deque(hashes, maxlen=10)
    except FileNotFoundError:
        return deque(maxlen=10)

def save_last_processed_hashes(hash_deque):
    """Save deque of processed hashes (automatically keeps last 10)"""
    with open('last_content.txt', 'w', encoding='utf-8') as f:
        # deque already maintains max 10, write in order
        f.write('\n'.join(hash_deque))

def get_content_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]

def normalize_facts_for_hash(facts):
    """Normalize facts dict for deterministic hashing"""
    if not isinstance(facts, dict):
        return json.dumps(facts, sort_keys=True)
    
    # Recursively sort all nested dicts
    def sort_dict(d):
        if isinstance(d, dict):
            return {k: sort_dict(v) for k, v in sorted(d.items())}
        elif isinstance(d, list):
            return [sort_dict(i) for i in d]
        return d
    
    normalized = sort_dict(facts)
    return json.dumps(normalized, sort_keys=True)

def validate_facts(facts):
    """Validate that facts dict contains minimum required data and is recent"""
    if not isinstance(facts, dict):
        return False
    
    # Must have at least one meaningful field
    required_fields = ['crypto', 'amount', 'action', 'exchange']
    has_data = any(facts.get(field) for field in required_fields)
    
    if not has_data:
        return False
    
    # Check for empty or placeholder values
    crypto = facts.get('crypto', '')
    if crypto.lower() in ['unknown', 'n/a', '', 'none']:
        return False
    
    # Check timestamp - filter out old tweets
    timestamp = facts.get('timestamp', '').lower()
    if timestamp:
        # Skip if timestamp contains dates (old tweets)
        old_indicators = ['2024', '2023', '2022', 'oct', 'nov', 'dec', 'jan', 'feb', 
                         'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep',
                         'd ago', 'days ago', 'day ago', 'week', 'month', 'year']
        if any(indicator in timestamp for indicator in old_indicators):
            print(f"⏭️  Skipping old tweet: timestamp={timestamp}")
            return False
    
    return True

async def capture_twitter_screenshot():
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox'
                ]
            )
            
            # Random User-Agent
            user_agent = random.choice(USER_AGENTS)
            
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 1920},
                user_agent=user_agent,
                locale='en-US',
                timezone_id='America/New_York'
            )
            
            # Disable automation detection
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
            
            page = await context.new_page()
            
            url = f'https://twitter.com/{TWITTER_USERNAME}'
            print(f"Loading {url} with UA: {user_agent[:50]}...")
            
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            
            # Random wait time (human-like behavior)
            wait_time = random.randint(5000, 10000)
            print(f"Waiting {wait_time}ms for content load...")
            await page.wait_for_timeout(wait_time)
            
            # Check for login wall
            page_text = await page.text_content('body')
            if 'Sign in' in page_text or 'Log in' in page_text:
                print("WARNING: Twitter showing login page - trying anyway")
            
            screenshot_path = 'twitter_screenshot.png'
            await page.screenshot(path=screenshot_path, full_page=False)
            print(f"Screenshot saved: {screenshot_path}")
            
            return screenshot_path
            
    except Exception as e:
        print(f"Error capturing screenshot: {e}")
        return None
    finally:
        if browser:
            await browser.close()

def extract_tweets_from_screenshot(image_path):
    try:
        with open(image_path, 'rb') as img_file:
            image_data = base64.b64encode(img_file.read()).decode('utf-8')
        
        if len(image_data) > 20_000_000:
            print("WARNING: Image too large for Vision API")
            return []
        
        print("Analyzing screenshot with GPT-4o-mini vision...")
        
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": """Extract KEY FACTUAL DATA ONLY from visible tweets (not full text).

CRITICAL: Extract ONLY tweets from the last 24 hours. Skip old tweets!

For each RECENT tweet, extract ONLY:
1. Cryptocurrency/token mentioned
2. Numerical amounts (BTC, USD, etc)
3. Wallet addresses (if visible)
4. Exchange names (Binance, Coinbase, etc)
5. Action type (bought, sold, transferred)
6. Timestamp (MUST be recent - hours/minutes ago, NOT days/months)

Format as JSON array:
[
  {
    "crypto": "BTC",
    "amount": "1000 BTC",
    "usd_value": "$40M",
    "action": "transferred",
    "exchange": "Binance",
    "timestamp": "2h ago"  // MUST be hours/minutes, NOT days!
  }
]

CRITICAL RULES:
- Extract FACTS only, NOT opinions or analysis
- Do NOT copy tweet text verbatim
- Extract ONLY tweets posted in last 24 hours
- Skip tweets with timestamps like "Oct 30, 2024" or "14m ago" if date is old
- ONLY include if timestamp shows hours (like "2h ago", "45m ago")
- Extract 3-5 latest RECENT tweets only
- Skip retweets and replies
- If NO recent tweets found, return empty array []"""
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=2000,  # Increased for long wallet addresses
            timeout=60
        )
        
        result = response.choices[0].message.content
        print(f"Vision API response: {result[:200]}...")
        
        result_clean = result.replace('```json', '').replace('```', '').strip()
        
        try:
            facts = json.loads(result_clean)
            return facts if isinstance(facts, list) else []
        except json.JSONDecodeError as je:
            print(f"JSON decode error: {je}")
            print(f"Raw response: {result_clean[:500]}")
            return []
        
    except Exception as e:
        print(f"Error extracting facts: {e}")
        return []

def process_with_ai(facts_data):
    """Transform factual data into original analysis"""
    for attempt in range(3):
        try:
            # Convert facts to readable format
            facts_str = json.dumps(facts_data, indent=2)
            
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system", 
                        "content": """You are a professional crypto market analyst focusing on RECENT market activity.

Your task: Create ORIGINAL analysis from provided factual data.

CRITICAL RULES:
1. NEVER copy or quote source text
2. Write in YOUR OWN analytical voice
3. Add market context and implications
4. Include risk assessment if relevant
5. Keep analysis concise (2-3 sentences)
6. Write as if you discovered this data yourself
7. Focus on RECENT activity (hours/minutes, NOT old news)
8. If data seems old, note it as "historical reference"

Style: Professional, analytical, informative, TIMELY"""
                    },
                    {
                        "role": "user", 
                        "content": f"""Based on these RECENT blockchain transaction facts, write original analysis:

{facts_str}

Provide:
- What happened (in your words)
- Market implications
- Context if relevant
- Emphasize RECENCY if data is fresh (hours ago)

Keep it under 200 words."""
                    }
                ],
                max_tokens=400,
                timeout=30,
                temperature=0.8  # Higher creativity for more transformation
            )
            return response.choices[0].message.content
            
        except Exception as e:
            print(f"OpenAI error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2)
    
    # Fallback: create basic summary from facts
    try:
        if isinstance(facts_data, dict):
            crypto = facts_data.get('crypto', 'Unknown')
            amount = facts_data.get('amount', '')
            action = facts_data.get('action', '')
            
            if crypto and amount and action:
                return f"Market activity detected: {amount} {crypto} {action}."
            elif crypto:
                return f"{crypto} blockchain activity detected."
        
        return "Crypto market update detected."
    except Exception as e:
        print(f"Fallback error: {e}")
        return "Blockchain activity update."

def send_to_telegram(text):
    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    
    # Add source attribution
    footer = f"\n\n🔗 Data source: twitter.com/{TWITTER_USERNAME}"
    message = text + footer
    
    text_limit = 4096
    if len(message) > text_limit:
        message = message[:text_limit]
    
    for attempt in range(3):
        try:
            data = {
                'chat_id': TARGET_CHAT_ID, 
                'text': message,
                'disable_web_page_preview': False
            }
            resp = requests.post(f"{base_url}/sendMessage", data=data, timeout=30)
            if resp.status_code == 200:
                return True
        except Exception as e:
            print(f"Telegram error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(1)
    
    return False

async def main_async():
    print("Starting Twitter screenshot bot...")
    
    # Initialize random seed for better randomization
    random.seed(int(time.time()))
    
    screenshot_path = None
    
    try:
        screenshot_path = await capture_twitter_screenshot()
        
        if not screenshot_path:
            print("Failed to capture screenshot")
            return
        
        facts_list = extract_tweets_from_screenshot(screenshot_path)
        print(f"Extracted {len(facts_list)} fact sets from screenshot")
        
        if not facts_list:
            print("No facts extracted")
            return
        
        # Load existing processed hashes (deque, max 10, ordered)
        processed_hashes = get_last_processed_hashes()
        print(f"Already processed {len(processed_hashes)} items previously")
        
        published_count = 0
        
        for facts in facts_list[:3]:
            # Validate facts first
            if not validate_facts(facts):
                timestamp = facts.get('timestamp', 'no timestamp')
                print(f"Skipping invalid/old facts: {facts.get('crypto', 'unknown')} (timestamp: {timestamp})")
                continue
            
            # Create deterministic hash
            facts_normalized = normalize_facts_for_hash(facts)
            facts_hash = get_content_hash(facts_normalized)
            
            # Check if already processed
            if facts_hash in processed_hashes:
                print(f"Skipping duplicate (hash: {facts_hash})")
                continue
            
            print(f"Processing facts: {str(facts)[:80]}...")
            
            # Transform facts into original analysis
            ai_analysis = process_with_ai(facts)
            
            if not ai_analysis or len(ai_analysis) < 20:
                print("AI analysis too short, skipping")
                continue
            
            success = send_to_telegram(ai_analysis)
            
            if success:
                # Add to deque (automatically maintains max 10)
                processed_hashes.append(facts_hash)
                published_count += 1
                print(f"✅ Published analysis ({published_count})")
            else:
                print(f"❌ Failed to publish")
                # Still mark as processed to avoid retry loops
                processed_hashes.append(facts_hash)
                continue
            
            # Random delay between posts (human-like)
            if published_count < len(facts_list[:3]):  # Don't delay after last
                delay = random.randint(2, 5)
                print(f"Waiting {delay}s before next post...")
                await asyncio.sleep(delay)
        
        # Save updated hashes (deque automatically kept last 10)
        save_last_processed_hashes(processed_hashes)
        print(f"\n📊 Summary: Published {published_count} items, tracking {len(processed_hashes)} hashes")
    
    finally:
        if screenshot_path:
            try:
                os.remove(screenshot_path)
                print("Cleaned up screenshot")
            except OSError as e:
                print(f"Could not remove screenshot: {e}")

def main():
    asyncio.run(main_async())

if __name__ == '__main__':
    main()
