#!/usr/bin/env python3
"""
Lookonchain Bot - Interactive Session Setup
Простая настройка Telegram авторизации
"""

import os
import sys

# Цвета для консоли
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text.center(60)}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.END}\n")

def print_success(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.CYAN}ℹ️  {text}{Colors.END}")

def check_dependencies():
    """Проверить что telethon установлен"""
    try:
        from telethon import TelegramClient
        return True
    except ImportError:
        return False

def install_telethon():
    """Установить telethon"""
    print_warning("Telethon не установлен")
    print_info("Пытаюсь установить...")
    
    import subprocess
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "telethon"])
        print_success("Telethon установлен!")
        return True
    except:
        print_error("Не удалось установить telethon")
        print_info("Попробуй вручную: pip install telethon")
        return False

def get_credentials():
    """Получить API credentials от пользователя"""
    print_header("ШАГ 1: API CREDENTIALS")
    
    print(f"{Colors.CYAN}Если у тебя нет API credentials:{Colors.END}")
    print(f"  1. Открой: {Colors.BOLD}https://my.telegram.org{Colors.END}")
    print(f"  2. Войди через свой номер телефона")
    print(f"  3. API development tools → Create application")
    print(f"  4. Скопируй api_id и api_hash\n")
    
    while True:
        api_id_input = input(f"{Colors.BOLD}Введи TELEGRAM_API_ID (число): {Colors.END}").strip()
        
        if not api_id_input:
            print_error("API_ID не может быть пустым!")
            continue
        
        try:
            api_id = int(api_id_input)
            break
        except ValueError:
            print_error("API_ID должен быть числом (например: 12345678)")
    
    while True:
        api_hash = input(f"{Colors.BOLD}Введи TELEGRAM_API_HASH (строка): {Colors.END}").strip()
        
        if not api_hash:
            print_error("API_HASH не может быть пустым!")
            continue
        
        if len(api_hash) < 20:
            print_warning("API_HASH выглядит слишком коротким, ты уверен?")
            confirm = input("Продолжить? (y/n): ").lower()
            if confirm != 'y':
                continue
        
        break
    
    return api_id, api_hash

def create_session(api_id, api_hash):
    """Создать Telegram session"""
    from telethon import TelegramClient
    import asyncio
    
    print_header("ШАГ 2: TELEGRAM АВТОРИЗАЦИЯ")
    
    print(f"{Colors.CYAN}Сейчас тебе нужно будет ввести:{Colors.END}")
    print(f"  1. Номер телефона (формат: +79123456789)")
    print(f"  2. Код подтверждения из Telegram")
    print(f"  3. Пароль 2FA (если включен)\n")
    
    client = TelegramClient('lookonchain_session', api_id, api_hash)
    
    async def auth():
        try:
            await client.start()
            return True
        except Exception as e:
            print_error(f"Ошибка авторизации: {e}")
            return False
    
    success = asyncio.run(auth())
    
    if success:
        asyncio.run(client.disconnect())
    
    return success

def verify_session_file():
    """Проверить что session файл создался"""
    session_file = 'lookonchain_session.session'
    
    if os.path.exists(session_file):
        file_size = os.path.getsize(session_file)
        print_success(f"Session файл создан: {session_file}")
        print_info(f"Размер: {file_size} байт")
        return True
    else:
        print_error("Session файл не создался!")
        return False

def print_next_steps():
    """Показать следующие шаги"""
    print_header("ШАГ 3: ЗАГРУЗКА В GITHUB")
    
    print(f"{Colors.BOLD}Теперь загрузи session файл в GitHub:{Colors.END}\n")
    
    print(f"{Colors.CYAN}Вариант A: Через Git{Colors.END}")
    print(f"  git add lookonchain_session.session")
    print(f"  git commit -m 'Add Telegram session'")
    print(f"  git push\n")
    
    print(f"{Colors.CYAN}Вариант B: Через веб-интерфейс{Colors.END}")
    print(f"  1. Открой GitHub → твой репозиторий")
    print(f"  2. Add file → Upload files")
    print(f"  3. Перетащи файл lookonchain_session.session")
    print(f"  4. Commit changes\n")
    
    print_header("ШАГ 4: GITHUB SECRETS")
    
    print(f"{Colors.BOLD}Убедись что эти secrets добавлены:{Colors.END}\n")
    print(f"  ✓ TELEGRAM_API_ID")
    print(f"  ✓ TELEGRAM_API_HASH")
    print(f"  ✓ OPENAI_API_KEY")
    print(f"  ✓ TELEGRAM_BOT_TOKEN")
    print(f"  ✓ TELEGRAM_CHAT_ID\n")
    
    print(f"{Colors.CYAN}Добавить/проверить:{Colors.END}")
    print(f"  Settings → Secrets and variables → Actions\n")
    
    print_header("ШАГ 5: ЗАПУСК")
    
    print(f"{Colors.BOLD}Запусти workflow:{Colors.END}")
    print(f"  Actions → Lookonchain Bot → Run workflow\n")
    
    print_success("ГОТОВО! Бот должен заработать! 🚀")

def main():
    """Основная логика"""
    os.system('clear' if os.name != 'nt' else 'cls')
    
    print_header("LOOKONCHAIN BOT - SETUP")
    
    print(f"{Colors.BOLD}Этот скрипт создаст Telegram session файл.{Colors.END}")
    print(f"{Colors.BOLD}Нужно запустить ОДИН РАЗ на своём компьютере.{Colors.END}\n")
    
    # Проверка dependencies
    if not check_dependencies():
        if not install_telethon():
            print_error("Установи telethon вручную и запусти скрипт снова")
            print_info("Команда: pip install telethon")
            sys.exit(1)
    
    print_success("Telethon установлен")
    
    # Получить credentials
    try:
        api_id, api_hash = get_credentials()
    except KeyboardInterrupt:
        print_error("\nОтменено пользователем")
        sys.exit(1)
    
    # Создать session
    try:
        if not create_session(api_id, api_hash):
            print_error("Не удалось создать session")
            sys.exit(1)
    except KeyboardInterrupt:
        print_error("\nОтменено пользователем")
        sys.exit(1)
    
    # Проверка
    if not verify_session_file():
        print_error("Что-то пошло не так")
        sys.exit(1)
    
    # Следующие шаги
    print_next_steps()
    
    print(f"\n{Colors.BOLD}{Colors.GREEN}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.GREEN}{'SETUP ЗАВЕРШЕН УСПЕШНО!'.center(60)}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.GREEN}{'='*60}{Colors.END}\n")

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print_error(f"Неожиданная ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
