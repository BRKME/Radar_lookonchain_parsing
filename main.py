import os
import sys
import time
import requests
import feedparser
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

required_vars = {
    'OPENAI_API_KEY': OPENAI_API_KEY,
    'TELEGRAM_BOT_TOKEN': TELEGRAM_BOT_TOKEN,
    'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID
}

for var_name, var_value in required_vars.items():
    if not var_value:
        print(f"ERROR: {var_name} not set")
        sys.exit(1)

openai_client = OpenAI(api_key=OPENAI_API_KEY)

NITTER_INSTANCES = [
    "https://nitter.poast.org/lookonchain/rss",
    "https://nitter.privacydev.net/lookonchain/rss",
    "https://nitter.net/lookonchain/rss",
    "https://nitter.it/lookonchain/rss",
    "https://nitter.woodland.cafe/lookonchain/rss",
    "https://bird.habedieeh.re/lookonchain/rss",
    "https://nitter.d420.de/lookonchain/rss",
]

def get_last_tweet_id():
    try:
        with open('last_tweet_id.txt', 'r') as f:
            content = f.read().strip()
            return content if content else None
    except FileNotFoundError:
        return None

def save_last_tweet_id(tweet_id):
    with open('last_tweet_id.txt', 'w') as f:
        f.write(tweet_id)

def get_lookonchain_tweets(since_id=None):
    for rss_url in NITTER_INSTANCES:
        try:
            print(f"Trying RSS from: {rss_url}")
            response = requests.get(rss_url, timeout=30)
            print(f"RSS response status: {response.status_code}")
            
            if response.status_code != 200:
                print(f"Failed, trying next instance...")
                continue
            
            feed = feedparser.parse(response.content)
            print(f"RSS feed entries count: {len(feed.entries)}")
            
            if len(feed.entries) == 0:
                print(f"No entries, trying next instance...")
                continue
            
            tweets_data = []
            for entry in feed.entries[:20]:
                tweet_id = entry.link.split('/')[-1].split('#')[0]
                
                if since_id and tweet_id <= since_id:
                    print(f"Skipping tweet {tweet_id} (older than {since_id})")
                    continue
                
                tweet_info = {
                    'id': tweet_id,
                    'text': entry.title,
                    'link': entry.link,
                    'media_urls': []
                }
                
                if hasattr(entry, 'media_content'):
                    for media in entry.media_content:
                        if 'url' in media:
                            tweet_info['media_urls'].append(media['url'])
                
                tweets_data.append(tweet_info)
            
            print(f"Successfully collected {len(tweets_data)} new tweets from {rss_url}")
            return tweets_data
            
        except Exception as e:
            print(f"Error with {rss_url}: {e}")
            continue
    
    print("All Nitter instances failed")
    return []

def process_with_ai(text):
    for attempt in range(3):
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a crypto analyst assistant."},
                    {"role": "user", "content": f"Analyze this tweet:\n\n{text}"}
                ],
                max_tokens=500,
                timeout=30
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"OpenAI error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2)
    return text

def send_to_telegram(text, media_urls=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    text = text[:1024] if len(text) > 1024 else text
    
    if media_urls:
        sent = False
        for i, media_url in enumerate(media_urls[:10]):
            for attempt in range(3):
                try:
                    img_response = requests.get(media_url, timeout=15)
                    if img_response.status_code == 200:
                        files = {'photo': img_response.content}
                        caption = text if i == 0 else ""
                        data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': caption}
                        resp = requests.post(f"{base_url}/sendPhoto", data=data, files=files, timeout=30)
                        if resp.status_code == 200:
                            sent = True
                            break
                except Exception as e:
                    print(f"Telegram photo error (attempt {attempt+1}/3): {e}")
                    if attempt < 2:
                        time.sleep(1)
        if sent:
            return True
    
    text_limit = 4096
    if len(text) > text_limit:
        text = text[:text_limit]
    
    for attempt in range(3):
        try:
            data = {'chat_id': TELEGRAM_CHAT_ID, 'text': text}
            resp = requests.post(f"{base_url}/sendMessage", data=data, timeout=30)
            if resp.status_code == 200:
                return True
        except Exception as e:
            print(f"Telegram message error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(1)
    
    return False

def main():
    last_id = get_last_tweet_id()
    print(f"Last tweet ID from file: {last_id}")
    
    tweets = get_lookonchain_tweets(since_id=last_id)
    print(f"Fetched {len(tweets)} tweets from RSS")
    
    if tweets:
        for i, tweet in enumerate(tweets[:3]):
            print(f"Tweet {i+1} ID: {tweet['id']}, Text preview: {tweet['text'][:50]}...")
    
    if not tweets:
        print("No new tweets")
        return
    
    tweets.reverse()
    
    processed_ids = []
    for tweet in tweets:
        try:
            ai_text = process_with_ai(tweet['text'])
            message = f"{ai_text}\n\nSource: @lookonchain"
            
            success = send_to_telegram(message, tweet['media_urls'] if tweet['media_urls'] else None)
            
            if success:
                processed_ids.append(str(tweet['id']))
                print(f"Published tweet {tweet['id']}")
            else:
                print(f"Failed to publish tweet {tweet['id']}")
                break
        except Exception as e:
            print(f"Error processing tweet {tweet['id']}: {e}")
            break
    
    if processed_ids:
        save_last_tweet_id(processed_ids[-1])
        print(f"Processed {len(processed_ids)} tweets, last ID: {processed_ids[-1]}")
    else:
        print("No tweets processed successfully")

if __name__ == '__main__':
    main()
