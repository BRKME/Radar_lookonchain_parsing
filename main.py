import os
import sys
import time
import tweepy
import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

TWITTER_BEARER_TOKEN = os.getenv('TWITTER_BEARER_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

required_vars = {
    'TWITTER_BEARER_TOKEN': TWITTER_BEARER_TOKEN,
    'OPENAI_API_KEY': OPENAI_API_KEY,
    'TELEGRAM_BOT_TOKEN': TELEGRAM_BOT_TOKEN,
    'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID
}

for var_name, var_value in required_vars.items():
    if not var_value:
        print(f"ERROR: {var_name} not set")
        sys.exit(1)

client = tweepy.Client(bearer_token=TWITTER_BEARER_TOKEN)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

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
    try:
        user = client.get_user(username='lookonchain')
        user_id = user.data.id
        
        params = {
            'max_results': 20,
            'tweet_fields': ['created_at', 'attachments', 'referenced_tweets'],
            'media_fields': ['url', 'preview_image_url'],
            'expansions': ['attachments.media_keys']
        }
        
        if since_id:
            params['since_id'] = since_id
        
        response = client.get_users_tweets(user_id, **params)
        
        tweets_data = []
        if response.data:
            media_dict = {}
            if response.includes and 'media' in response.includes:
                for media in response.includes['media']:
                    media_dict[media.media_key] = media
            
            for tweet in response.data:
                if hasattr(tweet, 'referenced_tweets'):
                    continue
                
                tweet_info = {
                    'id': tweet.id,
                    'text': tweet.text,
                    'media_urls': []
                }
                
                if hasattr(tweet, 'attachments') and 'media_keys' in tweet.attachments:
                    for media_key in tweet.attachments['media_keys']:
                        if media_key in media_dict:
                            media = media_dict[media_key]
                            if hasattr(media, 'url'):
                                tweet_info['media_urls'].append(media.url)
                
                tweets_data.append(tweet_info)
        
        return tweets_data
    except tweepy.errors.TweepyException as e:
        print(f"Twitter API error: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error fetching tweets: {e}")
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
    tweets = get_lookonchain_tweets(since_id=last_id)
    
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
