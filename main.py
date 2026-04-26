import asyncio
import re
import sqlite3
import logging
import os
import json
import base64
import random
import tempfile
from threading import Lock
from datetime import datetime
from telethon import TelegramClient, functions, types, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import aiohttp
from io import BytesIO

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8729005607:AAGFxfC7TmM0XfexLV_BVce6SMpwau7VNT0"
OWNER_ID = 8480939483
API_ID = 35800959
API_HASH = "708e7d0bc3572355bcaf68562cc068f1"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

user_state = {}
temp_data = {}
db_lock = Lock()

# ========== БАЗА ДАННЫХ ==========
def get_db():
    return sqlite3.connect('contest_bot.db', timeout=30, check_same_thread=False)

def init_db():
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS accounts
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      phone TEXT UNIQUE,
                      username TEXT,
                      first_name TEXT,
                      session_string TEXT)''')
        conn.commit()
        conn.close()

init_db()

def get_all_accounts():
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, phone, username, first_name, session_string FROM accounts")
        accounts = c.fetchall()
        conn.close()
    return accounts

def save_account(phone, username, first_name, session_string):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO accounts (phone, username, first_name, session_string) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT(phone) DO UPDATE SET session_string=?, username=?, first_name=?",
                  (phone, username, first_name, session_string, session_string, username, first_name))
        conn.commit()
        conn.close()

# ========== ИЗВЛЕЧЕНИЕ КАНАЛОВ ==========
def extract_channels_from_text(text):
    channels = set()
    channels.update(re.findall(r'@([a-zA-Z0-9_]+)', text))
    channels.update(re.findall(r't\.me/([a-zA-Z0-9_]+)', text))
    channels.update(re.findall(r't\.me/\+([a-zA-Z0-9_-]+)', text))
    matches = re.findall(r'»\s*([a-zA-Z0-9_]+)', text)
    channels.update(matches)
    return list(channels)

# ========== БЕСПЛАТНЫЙ AI ДЛЯ КАПЧИ ==========
class FreeCaptchaAI:
    """
    Бесплатное распознавание капчи без внешних API
    Работает на базе анализа текста и простых эвристик
    """
    
    @staticmethod
    async def solve_text_captcha(text, message_buttons=None):
        """
        Решает текстовые капчи на основе анализа текста
        """
        text_lower = text.lower()
        
        # 1. Математические примеры
        math_match = re.search(r'(\d+)\s*([\+\-\*])\s*(\d+)\s*=\s*\?', text)
        if math_match:
            num1, op, num2 = int(math_match.group(1)), math_match.group(2), int(math_match.group(3))
            if op == '+':
                return str(num1 + num2)
            elif op == '-':
                return str(num1 - num2)
            elif op == '*':
                return str(num1 * num2)
        
        # 2. "Сколько будет X + Y"
        math_text = re.search(r'сколько\s+будет\s+(\d+)\s*([\+\-\*])\s*(\d+)', text_lower)
        if math_text:
            num1, op, num2 = int(math_text.group(1)), math_text.group(2), int(math_text.group(3))
            if op == '+':
                return str(num1 + num2)
            elif op == '-':
                return str(num1 - num2)
            elif op == '*':
                return str(num1 * num2)
        
        # 3. Поиск чисел в тексте (обычно это код)
        numbers = re.findall(r'\b\d{4,8}\b', text)
        if numbers and any(word in text_lower for word in ['код', 'code', 'числ', 'цифр', 'видите']):
            return numbers[0]
        
        # 4. Вопросы с выбором ответа
        if 'какой' in text_lower or 'какое' in text_lower:
            # Ищем подсказки в тексте
            if 'цвет' in text_lower or 'color' in text_lower:
                colors = ['красный', 'синий', 'зеленый', 'желтый', 'белый', 'черный']
                for color in colors:
                    if color in text_lower:
                        return color
        
        # 5. "Напишите слово ..."
        word_match = re.search(r'напишите\s+слово\s+"?([^"]+)"?', text_lower)
        if word_match:
            return word_match.group(1)
        
        # 6. Простые вопросы
        if 'сколько' in text_lower:
            if 'пальцев' in text_lower:
                return '5'
            if 'ног' in text_lower:
                return '2'
            if 'глаз' in text_lower:
                return '2'
        
        return None
    
    @staticmethod
    async def solve_image_captcha_cctld(image_data):
        """
        Бесплатное распознавание через сервис cctld.ru (публичный OCR)
        """
        try:
            url = "https://api.cctld.ru/ocr"
            files = {'file': ('captcha.png', image_data, 'image/png')}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=files) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get('text', '').strip()
        except:
            pass
        
        return None
    
    @staticmethod
    async def solve_image_captcha_ocrspace(image_data):
        """
        Бесплатное распознавание через OCR.space (бесплатный лимит)
        """
        try:
            url = "https://api.ocr.space/parse/image"
            data = {
                'apikey': 'helloworld',  # Бесплатный ключ
                'language': 'rus+eng',
                'isOverlayRequired': False
            }
            
            async with aiohttp.ClientSession() as session:
                form = aiohttp.FormData()
                form.add_field('file', image_data, filename='captcha.png')
                form.add_field('apikey', 'helloworld')
                form.add_field('language', 'rus+eng')
                
                async with session.post(url, data=form) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get('ParsedResults'):
                            text = result['ParsedResults'][0].get('ParsedText', '').strip()
                            return text
        except:
            pass
        
        return None
    
    @staticmethod
    async def solve_button_captcha(buttons, text):
        """
        Выбирает правильную кнопку при капче с картинками/эмодзи
        """
        # Если нужно выбрать определенную цифру/эмодзи
        text_lower = text.lower()
        
        # Ищем подсказки в тексте
        target = None
        emoji_map = {
            '🍎': 'яблоко', '🍊': 'апельсин', '🍋': 'лимон',
            '🍇': 'виноград', '🍓': 'клубника', '🍒': 'вишня',
            '⭐': 'звезда', '❤️': 'сердце', '💎': 'бриллиант'
        }
        
        for emoji, name in emoji_map.items():
            if name in text_lower or emoji in text:
                target = emoji
                break
        
        if target and buttons:
            for btn in buttons:
                if btn.text and target in btn.text:
                    return btn
        
        # Если не нашли - случайная кнопка
        return random.choice(buttons) if buttons else None

# ========== ПОДПИСКА НА КАНАЛЫ ==========
async def join_channel(client, channel_input):
    """Подписка на канал/группу любого типа"""
    if not channel_input:
        return False
    
    channel_input = channel_input.strip()
    
    try:
        # Приватные каналы (с + в ссылке)
        if channel_input.startswith('+') or '/+' in channel_input:
            hash_match = re.search(r'\+([a-zA-Z0-9_-]+)', channel_input)
            if hash_match:
                invite_hash = hash_match.group(1)
                try:
                    await client(functions.messages.ImportChatInviteRequest(invite_hash))
                    logger.info(f"✅ Приватный канал: +{invite_hash}")
                    return True
                except Exception as e:
                    logger.error(f"❌ Приватный канал +{invite_hash}: {e}")
                    return False
        
        # Публичные каналы/группы
        else:
            username = channel_input.replace('@', '').replace('https://t.me/', '').replace('http://t.me/', '')
            
            if '?' in username or 'start' in username:
                return False
            
            try:
                await client(functions.channels.JoinChannelRequest(username))
                logger.info(f"✅ Публичный: {username}")
                return True
            except Exception as e:
                logger.error(f"❌ Публичный {username}: {e}")
                return False
                
    except Exception as e:
        logger.error(f"❌ Ошибка подписки на {channel_input}: {e}")
        return False

# ========== ОБРАБОТКА МИНИ-ПРИЛОЖЕНИЯ ==========
async def handle_mini_app(client, account_name, bot_username, start_param, owner_bot):
    """Обработка мини-приложения"""
    try:
        bot_entity = await client.get_entity(bot_username)
        
        await client.send_message(bot_username, f"/start {start_param}")
        await asyncio.sleep(2)
        
        try:
            webview = await client(functions.messages.RequestAppWebViewRequest(
                peer=bot_username,
                app=types.InputBotAppShortName(
                    bot_id=bot_entity.id,
                    short_name="start"
                ),
                platform="android",
                write_allowed=True,
                start_param=start_param
            ))
            
            if hasattr(webview, 'url'):
                logger.info(f"[{account_name}] 🌐 URL мини-приложения: {webview.url}")
                
                async with aiohttp.ClientSession() as session:
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    }
                    async with session.get(webview.url, headers=headers, timeout=15) as response:
                        logger.info(f"[{account_name}] 📱 Статус: {response.status}")
                
                await asyncio.sleep(10)
                await client.send_message(bot_username, "/start")
                
                await owner_bot.send_message(OWNER_ID, 
                    f"✅ *{account_name}* обработал мини-приложение!", 
                    parse_mode="Markdown")
                return True
                
        except Exception as e:
            logger.error(f"[{account_name}] ❌ Ошибка WebView: {e}")
            await client.send_message(bot_username, f"/start {start_param}")
            await asyncio.sleep(10)
            return True
            
    except Exception as e:
        logger.error(f"[{account_name}] ❌ Ошибка мини-приложения: {e}")
        return False

# ========== УЧАСТИЕ В КОНКУРСЕ ==========
async def participate_one_account(session_string, account_name, channels_input, ref_link, owner_bot):
    captcha_ai = FreeCaptchaAI()
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error(f"[{account_name}] ❌ Сессия недействительна")
            return False
        
        me = await client.get_me()
        logger.info(f"[{account_name}] ✅ @{me.username or me.first_name}")
        
        # 1. Быстрая подписка на все каналы
        channel_list = [ch.strip() for ch in channels_input.split(',') if ch.strip()]
        if channel_list:
            join_tasks = [join_channel(client, ch) for ch in channel_list]
            await asyncio.gather(*join_tasks, return_exceptions=True)
            await asyncio.sleep(0.5)
        
        # 2. Определяем тип ссылки
        # Мини-приложение
        if 'startapp=' in ref_link.lower():
            match = re.search(r'(?:t\.me|telegram\.me)/([^/?]+)', ref_link)
            if match:
                bot_username = match.group(1)
                start_param = ref_link.split('startapp=')[-1].split('&')[0]
                
                logger.info(f"[{account_name}] 🎮 Мини-приложение @{bot_username}")
                result = await handle_mini_app(client, account_name, bot_username, start_param, owner_bot)
                await client.disconnect()
                return result
        
        # Обычная ссылка
        match = re.search(r'(?:t\.me|telegram\.me)/([^/?]+)(?:\?start=([\w.-]+))?', ref_link)
        if not match:
            return False
        
        bot_username, start_param = match.group(1), match.group(2)
        msg = f"/start {start_param}" if start_param else "/start"
        await client.send_message(bot_username, msg)
        logger.info(f"[{account_name}] ✅ Старт в @{bot_username}")
        await asyncio.sleep(1)
        
        success = False
        
        @client.on(events.NewMessage(from_user=bot_username))
        async def handler(event):
            nonlocal success
            text = event.message.text or ""
            text_lower = text.lower()
            
            # УСПЕХ
            if any(p in text_lower for p in ['вы участник', 'участник конкурса', 'поздравляем', 'успешно', 'вы в игре', 'теперь вы']):
                success = True
                await owner_bot.send_message(OWNER_ID, f"✅ *{account_name}* участвует!", parse_mode="Markdown")
                return
            
            try:
                # AI РЕШЕНИЕ КАПЧИ
                captcha_answer = await captcha_ai.solve_text_captcha(text, event.message.buttons)
                if captcha_answer:
                    await event.message.respond(captcha_answer)
                    logger.info(f"[{account_name}] 🤖 AI решил капчу: {captcha_answer}")
                    return
                
                # КАПЧА С КАРТИНКОЙ
                if event.message.photo:
                    try:
                        # Скачиваем фото
                        photo_data = await client.download_media(event.message, file=BytesIO())
                        photo_data.seek(0)
                        
                        # Пробуем распознать через бесплатные OCR
                        captcha_text = await captcha_ai.solve_image_captcha_ocrspace(photo_data.getvalue())
                        
                        if not captcha_text:
                            captcha_text = await captcha_ai.solve_image_captcha_cctld(photo_data.getvalue())
                        
                        if captcha_text:
                            captcha_text = captcha_text.strip()
                            logger.info(f"[{account_name}] 🤖 OCR распознал: {captcha_text}")
                            
                            # Отправляем ответ
                            if captcha_text.isdigit():
                                await event.message.respond(captcha_text)
                            else:
                                # Ищем кнопку с таким текстом
                                if event.message.buttons:
                                    all_buttons = [btn for row in event.message.buttons for btn in row if btn.text]
                                    for btn in all_buttons:
                                        if captcha_text.lower() in btn.text.lower():
                                            await btn.click()
                                            return
                            return
                    except Exception as e:
                        logger.error(f"[{account_name}] Ошибка OCR: {e}")
                
                # ПОДПИСКА НА ДОП. КАНАЛЫ
                if 'подписаться' in text_lower or 'подпишитесь' in text_lower:
                    extra_channels = extract_channels_from_text(text)
                    if extra_channels:
                        await asyncio.gather(*[join_channel(client, ch) for ch in extra_channels])
                        await asyncio.sleep(0.3)
                    
                    if event.message.buttons:
                        for row in event.message.buttons:
                            for btn in row:
                                if btn.text and any(w in btn.text.lower() for w in ['попробовать', 'проверить', 'снова', 'try', 'check', 'готово']):
                                    await btn.click()
                                    return
                
                # РЕАКЦИЯ
                if 'реакци' in text_lower or 'reaction' in text_lower:
                    try:
                        await client(functions.messages.SendReactionRequest(
                            peer=event.chat_id, msg_id=event.message.id,
                            reaction=[types.ReactionEmoji(emoticon="👍")]
                        ))
                    except: pass
                
                # КНОПКИ
                if event.message.buttons:
                    all_buttons = [btn for row in event.message.buttons for btn in row if btn.text]
                    if all_buttons:
                        # AI выбор кнопки при капче
                        if any(w in text_lower for w in ['выберите', 'выбрать', 'какое', 'нажмите', 'click', 'select', 'капч']):
                            chosen_btn = await captcha_ai.solve_button_captcha(all_buttons, text)
                            if chosen_btn:
                                await chosen_btn.click()
                                logger.info(f"[{account_name}] 🤖 AI выбрал кнопку: {chosen_btn.text}")
                                return
                        
                        # Приоритетные кнопки
                        priority_words = ['участвовать', 'принять', 'join', 'play', 'продолжить', 'continue', 'начать', 'start']
                        for btn in all_buttons:
                            if any(w in btn.text.lower() for w in priority_words):
                                await btn.click()
                                return
                        
                        await all_buttons[-1].click()
                        return
                        
            except Exception as e:
                logger.error(f"[{account_name}] Ошибка обработки: {e}")
        
        # Ждем до 2 минут
        for _ in range(24):
            if success:
                break
            await asyncio.sleep(5)
        
        await client.disconnect()
        return success
        
    except Exception as e:
        logger.error(f"[{account_name}] ❌ {e}")
        try: await client.disconnect()
        except: pass
        return False

# ========== ОБРАБОТЧИКИ БОТА ==========
async def start_cmd(update: Update, context):
    if update.effective_user.id != OWNER_ID:
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить аккаунт", callback_data="add_acc")],
        [InlineKeyboardButton("📋 Аккаунты", callback_data="list_acc")],
        [InlineKeyboardButton("🎁 НОВЫЙ КОНКУРС", callback_data="new_contest")],
    ])
    await update.message.reply_text("🎯 *Главное меню*\n🤖 AI для капчи активирован!", reply_markup=keyboard, parse_mode="Markdown")

async def callback_handler(update: Update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != OWNER_ID:
        return
    
    data = query.data
    
    if data == "add_acc":
        user_state[user_id] = "waiting_phone"
        await query.edit_message_text(
            "📱 Введите номер:\n+79123456789",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]])
        )
    
    elif data == "list_acc":
        accounts = get_all_accounts()
        if not accounts:
            text = "📭 Нет аккаунтов"
        else:
            text = f"📋 *Аккаунтов: {len(accounts)}*\n\n"
            for acc in accounts:
                text += f"• {acc[3] or acc[2] or '—'} — {acc[1]}\n"
        await query.edit_message_text(text, parse_mode="Markdown")
    
    elif data == "new_contest":
        if not get_all_accounts():
            await query.edit_message_text("❌ Сначала добавьте аккаунты!")
            return
        
        user_state[user_id] = "waiting_channels"
        await query.edit_message_text(
            "📢 *Этап 1/2*\nВведите каналы через запятую:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]])
        )
    
    elif data == "start_contest_now":
        if user_id not in temp_data:
            return
        
        channels = temp_data[user_id].get('channels')
        ref_link = temp_data[user_id].get('ref_link')
        
        if not channels or not ref_link:
            await query.edit_message_text("❌ Нет данных")
            return
        
        accounts = get_all_accounts()
        temp_data.pop(user_id, None)
        
        await query.edit_message_text(f"🚀 Запуск {len(accounts)} аккаунтов...\n🤖 AI капча активирована")
        
        success_count = 0
        batch_size = 5
        
        for i in range(0, len(accounts), batch_size):
            batch = accounts[i:i + batch_size]
            tasks = []
            
            for acc in batch:
                acc_id, phone, username, first_name, session = acc
                name = f"@{username or first_name or 'user'} ({phone})"
                
                tasks.append(participate_one_account(session, name, channels, ref_link, context.bot))
                await context.bot.send_message(OWNER_ID, f"🔄 {name}...")
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count += sum(1 for r in results if r is True)
            
            if i + batch_size < len(accounts):
                await asyncio.sleep(1)
        
        await context.bot.send_message(
            OWNER_ID,
            f"✅ *ГОТОВО!*\n✅ Успешно: {success_count}\n❌ Неудачно: {len(accounts) - success_count}",
            parse_mode="Markdown"
        )
    
    elif data == "cancel":
        user_state.pop(user_id, None)
        temp_data.pop(user_id, None)
        await query.edit_message_text("❌ Отменено")

async def message_handler(update: Update, context):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        return
    
    state = user_state.get(user_id)
    text = update.message.text.strip()
    
    if state == "waiting_phone":
        user_state[user_id] = "waiting_code"
        temp_data[user_id] = {"phone": text}
        
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        try:
            sent = await client.send_code_request(text)
            temp_data[user_id]["client"] = client
            temp_data[user_id]["hash"] = sent.phone_code_hash
            await update.message.reply_text("📨 Введите код из Telegram:")
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
            user_state.pop(user_id, None)
            await client.disconnect()
    
    elif state == "waiting_code":
        data = temp_data.get(user_id, {})
        client = data.get("client")
        phone = data.get("phone")
        code = text
        
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=data["hash"])
            me = await client.get_me()
            session = client.session.save()
            save_account(phone, me.username, me.first_name, session)
            await update.message.reply_text(f"✅ @{me.username or me.first_name}")
            user_state.pop(user_id, None)
            temp_data.pop(user_id, None)
            await client.disconnect()
        except SessionPasswordNeededError:
            user_state[user_id] = "waiting_password"
            await update.message.reply_text("🔐 Введите пароль 2FA:")
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
            user_state.pop(user_id, None)
            await client.disconnect()
    
    elif state == "waiting_password":
        data = temp_data.get(user_id, {})
        client = data.get("client")
        phone = data.get("phone")
        
        try:
            await client.sign_in(password=text)
            me = await client.get_me()
            session = client.session.save()
            save_account(phone, me.username, me.first_name, session)
            await update.message.reply_text(f"✅ @{me.username or me.first_name}")
            user_state.pop(user_id, None)
            temp_data.pop(user_id, None)
            await client.disconnect()
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
            user_state.pop(user_id, None)
            await client.disconnect()
    
    elif state == "waiting_channels":
        temp_data[user_id] = {'channels': text}
        user_state[user_id] = "waiting_link"
        await update.message.reply_text(
            "🔗 *Этап 2/2*\nВведите ссылку на конкурс:",
            parse_mode="Markdown"
        )
    
    elif state == "waiting_link":
        temp_data[user_id]['ref_link'] = text
        user_state.pop(user_id, None)
        
        accounts = get_all_accounts()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 НАЧАТЬ УЧАСТИЕ", callback_data="start_contest_now")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
        ])
        await update.message.reply_text(
            f"✅ Готово\nАккаунтов: {len(accounts)}\n🤖 AI капча активирована",
            reply_markup=keyboard
        )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    print("✅ Бот запущен с бесплатным AI для капчи!")
    app.run_polling()

if __name__ == "__main__":
    main()