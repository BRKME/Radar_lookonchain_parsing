import os
import sys
import time
import asyncio
import hashlib
import logging
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from openai import OpenAI
import requests
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# Telegram API для чтения Lookonchain
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')

# OpenAI для анализа
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Telegram Bot для публикации
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TARGET_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID', TARGET_CHAT_ID)  # For error notifications

# Источник данных
LOOKONCHAIN_CHANNEL = 'lookonchainchannel'  # @lookonchainchannel

# Configuration
MAX_INPUT_LENGTH = 2000  # Truncate long messages
MAX_MESSAGES_PER_RUN = 10
OPENAI_TIMEOUT = 15
POST_DELAY = 3  # Seconds between posts

# Проверка переменных
required_vars = {
    'TELEGRAM_API_ID': TELEGRAM_API_ID,
    'TELEGRAM_API_HASH': TELEGRAM_API_HASH,
    'OPENAI_API_KEY': OPENAI_API_KEY,
    'TELEGRAM_BOT_TOKEN': BOT_TOKEN,
    'TELEGRAM_CHAT_ID': TARGET_CHAT_ID
}

for var_name, var_value in required_vars.items():
    if not var_value:
        logger.error(f"{var_name} not set")
        sys.exit(1)

# Конвертировать API_ID в int
try:
    TELEGRAM_API_ID = int(TELEGRAM_API_ID)
except ValueError:
    logger.error("TELEGRAM_API_ID must be a number")
    sys.exit(1)

openai_client = OpenAI(api_key=OPENAI_API_KEY)

def get_last_processed_id():
    """Получить ID последнего обработанного сообщения"""
    try:
        with open('last_message_id.txt', 'r') as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0

def save_last_processed_id(message_id):
    """Сохранить ID последнего обработанного сообщения"""
    with open('last_message_id.txt', 'w') as f:
        f.write(str(message_id))
    logger.info(f"Saved last processed ID: {message_id}")

def get_processed_hashes():
    """Получить хэши обработанных сообщений (для дедупликации)"""
    try:
        with open('processed_hashes.txt', 'r') as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()

def save_processed_hash(content_hash):
    """Сохранить хэш обработанного сообщения"""
    with open('processed_hashes.txt', 'a') as f:
        f.write(f"{content_hash}\n")

def get_content_hash(text):
    """Создать хэш контента для дедупликации"""
    # Normalize: lowercase, remove extra spaces
    normalized = ' '.join(text.lower().split())
    return hashlib.md5(normalized.encode()).hexdigest()

def is_ad_or_spam(text):
    """Проверить является ли сообщение рекламой"""
    ad_keywords = [
        'sponsored', 'advertisement', 'promo code', 'affiliate',
        'discount code', 'use code', 'click here', 'limited offer',
        'join our', 'subscribe to', 'sign up now'
    ]
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in ad_keywords)

def process_with_ai(text):
    """Обработать текст через OpenAI с улучшенной защитой copyright"""
    # Truncate if too long
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH] + "..."
        logger.warning(f"Message truncated to {MAX_INPUT_LENGTH} chars")
    
    for attempt in range(3):
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """Ты криптоаналитик. Создай ПОЛНОСТЬЮ ОРИГИНАЛЬНЫЙ анализ.

КРИТИЧЕСКИЕ ПРАВИЛА (НАРУШЕНИЕ = "SKIP"):
1. НИКОГДА не используй более 5 слов подряд из исходного текста
2. Полностью ПЕРЕПИШИ всю информацию своими словами
3. Анализ должен быть на 80%+ отличен от оригинала
4. Сохрани только: точные цифры, тикеры криптовалют, суммы в USD
5. ВСЁ ОСТАЛЬНОЕ - твои собственные формулировки и выводы

Формат:
- 2-3 предложения МАКСИМУМ
- Краткий, информативный, аналитический
- Без лишних слов

Если не можешь создать достаточно оригинальный текст - ответь ТОЛЬКО слово "SKIP"."""
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
            
            # Проверка на SKIP
            if result == "SKIP" or len(result) < 20:
                logger.warning("AI refused to create original content or result too short")
                return None
            
            return result
            
        except Exception as e:
            logger.error(f"OpenAI error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s
            else:
                return None
    
    return None

def send_to_telegram(text, is_error=False):
    """Отправить в Telegram через бота"""
    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    
    # Определить куда отправлять
    chat_id = ADMIN_CHAT_ID if is_error else TARGET_CHAT_ID
    
    # Добавить источник (только для обычных сообщений)
    if not is_error:
        footer = f"\n\n📊 Источник: @{LOOKONCHAIN_CHANNEL}"
        message = text + footer
    else:
        message = text
    
    # Лимит Telegram
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
    """Отправить уведомление об ошибке админу"""
    try:
        message = f"🚨 BOT ERROR\n\n{error_msg}"
        send_to_telegram(message, is_error=True)
    except Exception as e:
        logger.error(f"Failed to send error notification: {e}")

async def main_async():
    """Основная логика"""
    logger.info("Starting Telegram Lookonchain bot...")
    
    # Создать Telegram клиент
    client = TelegramClient('lookonchain_session', TELEGRAM_API_ID, TELEGRAM_API_HASH)
    
    try:
        # Подключиться
        logger.info("Connecting to Telegram...")
        await client.start()
        logger.info("✅ Connected to Telegram")
        
        # Получить канал
        try:
            channel = await client.get_entity(LOOKONCHAIN_CHANNEL)
            logger.info(f"✅ Found channel: {channel.title}")
        except Exception as e:
            error_msg = f"Could not find channel @{LOOKONCHAIN_CHANNEL}: {e}"
            logger.error(error_msg)
            notify_error(error_msg)
            return
        
        # Получить последний обработанный ID
        last_id = get_last_processed_id()
        logger.info(f"📌 Last processed message ID: {last_id}")
        
        # Получить хэши обработанных сообщений
        processed_hashes = get_processed_hashes()
        logger.info(f"📌 Loaded {len(processed_hashes)} processed content hashes")
        
        # Получить новые сообщения с обработкой FloodWait
        messages = []
        try:
            async for message in client.iter_messages(channel, limit=MAX_MESSAGES_PER_RUN):
                # Фильтрация
                if message.pinned:
                    logger.debug(f"⏭️  Skipping pinned message {message.id}")
                    continue
                
                if not message.text or not message.text.strip():
                    logger.debug(f"⏭️  Skipping empty message {message.id}")
                    continue
                
                if message.id <= last_id:
                    continue
                
                # Проверка на рекламу
                if is_ad_or_spam(message.text):
                    logger.info(f"⏭️  Skipping ad/spam message {message.id}")
                    continue
                
                messages.append(message)
                
        except FloodWaitError as e:
            logger.warning(f"⚠️  Flood wait: {e.seconds} seconds")
            if e.seconds < 120:  # Wait if less than 2 minutes
                logger.info(f"Waiting {e.seconds} seconds...")
                await asyncio.sleep(e.seconds)
                # Could retry here, but for simplicity just continue with what we have
            else:
                error_msg = f"Flood wait too long ({e.seconds}s), skipping this run"
                logger.error(error_msg)
                notify_error(error_msg)
                return
        
        # Обработать в обратном порядке (от старых к новым)
        messages.reverse()
        
        logger.info(f"📨 Found {len(messages)} new messages")
        
        if not messages:
            logger.info("No new messages to process")
            return
        
        # FIRST RUN PROTECTION
        if last_id == 0 and messages:
            latest_id = messages[-1].id
            save_last_processed_id(latest_id)
            logger.warning(f"⚠️  First run: saved latest ID ({latest_id}), no publishing")
            logger.info("Run the bot again to start processing new messages")
            return
        
        published_count = 0
        max_processed_id = last_id
        
        for i, message in enumerate(messages):
            logger.info(f"\n--- Processing message {message.id} ---")
            logger.info(f"Date: {message.date}")
            
            # Safe text preview
            text_preview = message.text[:100] if len(message.text) > 100 else message.text
            logger.info(f"Text: {text_preview}...")
            
            # Дедупликация по content hash
            content_hash = get_content_hash(message.text)
            if content_hash in processed_hashes:
                logger.info(f"⏭️  Duplicate content detected, skipping")
                max_processed_id = max(max_processed_id, message.id)
                continue
            
            # Обработать через AI
            ai_analysis = process_with_ai(message.text)
            
            if not ai_analysis:
                logger.warning("⚠️  AI processing failed or returned SKIP")
                max_processed_id = max(max_processed_id, message.id)
                continue
            
            logger.info(f"AI analysis: {ai_analysis[:100]}...")
            
            # Отправить в свой канал
            success = send_to_telegram(ai_analysis)
            
            if success:
                published_count += 1
                logger.info(f"✅ Published ({published_count})")
                
                # Сохранить hash для дедупликации
                save_processed_hash(content_hash)
                processed_hashes.add(content_hash)
                
                # Обновить max ID
                max_processed_id = max(max_processed_id, message.id)
            else:
                logger.error(f"❌ Failed to publish")
                # Не увеличиваем max_processed_id - попробуем снова в следующий раз
                break
            
            # Задержка между постами (кроме последнего)
            if i < len(messages) - 1:
                await asyncio.sleep(POST_DELAY)
        
        # Сохранить финальный ID (избегаем race condition)
        if max_processed_id > last_id:
            save_last_processed_id(max_processed_id)
        
        logger.info(f"\n📊 Summary: Published {published_count}/{len(messages)} messages")
        
    except Exception as e:
        error_msg = f"Unhandled error: {e}"
        logger.error(error_msg)
        import traceback
        logger.error(traceback.format_exc())
        notify_error(f"{error_msg}\n\n{traceback.format_exc()[:500]}")
        raise
    
    finally:
        await client.disconnect()
        logger.info("✅ Disconnected from Telegram")

def main():
    asyncio.run(main_async())

if __name__ == '__main__':
    main()
