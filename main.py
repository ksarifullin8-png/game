import asyncio
import os
import re
import sqlite3
import logging
from threading import Lock
from telethon import TelegramClient, functions, types, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8729005607:AAGFxfC7TmM0XfexLV_BVce6SMpwau7VNT0"
OWNER_ID = 8480939483
API_ID = 35800959
API_HASH = "708e7d0bc3572355bcaf68562cc068f1"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Состояния и блокировка БД
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
        c.execute('''CREATE TABLE IF NOT EXISTS settings
                     (key TEXT PRIMARY KEY,
                      value TEXT)''')
        conn.commit()
        conn.close()

init_db()

def save_channels(channels):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('channels', ?)", (channels,))
        conn.commit()
        conn.close()

def save_ref_link(link):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ref_link', ?)", (link,))
        conn.commit()
        conn.close()

def get_settings():
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT key, value FROM settings")
        data = dict(c.fetchall())
        conn.close()
    return data

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

# ========== УЧАСТИЕ В КОНКУРСЕ ==========

async def participate_one_account(session_string, account_name, channels, ref_link, owner_bot):
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error(f"[{account_name}] ❌ Сессия недействительна")
            return False
        
        # 1. Подписка на каналы
        for ch in channels.split(','):
            ch = ch.strip().replace('@', '').replace('https://t.me/', '')
            if ch:
                try:
                    await client(functions.channels.JoinChannelRequest(ch))
                    logger.info(f"[{account_name}] ✅ Подписался на {ch}")
                    await asyncio.sleep(3)
                except Exception as e:
                    logger.error(f"[{account_name}] ❌ {ch}: {e}")
        
        # 2. Переход по ссылке
        match = re.search(r'(?:t\.me|telegram\.me)/([^/?]+)(?:\?start=(\w+))?', ref_link)
        if match:
            bot_username = match.group(1)
            start_param = match.group(2)
            
            if start_param:
                await client.send_message(bot_username, f"/start {start_param}")
            else:
                await client.send_message(bot_username, "/start")
            
            logger.info(f"[{account_name}] ✅ Перешёл в @{bot_username}")
        else:
            logger.error(f"[{account_name}] ❌ Неверная ссылка")
            return False
        
        await asyncio.sleep(3)
        
        # 3. Обработка ответов бота
        success = False
        
        @client.on(events.NewMessage(from_user=bot_username))
        async def handle(event):
            nonlocal success
            text = event.message.text.lower() if event.message.text else ""
            
            if any(w in text for w in ['участник', 'participant', 'поздравля', 'успешно', 'вы участник', 'теперь вы']):
                success = True
                logger.info(f"[{account_name}] 🎉 УЧАСТВУЕТ!")
                await owner_bot.send_message(OWNER_ID, f"✅ *{account_name}* участвует в конкурсе!", parse_mode="Markdown")
                await client.disconnect()
                return
            
            if 'реакци' in text or 'reaction' in text or 'поставь' in text:
                try:
                    await client(functions.messages.SendReactionRequest(
                        peer=event.chat_id, msg_id=event.message.id,
                        reaction=[types.ReactionEmoji(emoticon="👍")]
                    ))
                    logger.info(f"[{account_name}] 👍 Реакция")
                    await asyncio.sleep(1)
                except:
                    pass
            
            nums = re.findall(r'\b\d{3,6}\b', text)
            if nums and ('числ' in text or 'цифр' in text or 'код' in text or 'видите' in text):
                await event.message.respond(nums[0])
                logger.info(f"[{account_name}] 📤 Код: {nums[0]}")
                await asyncio.sleep(1)
            
            if event.message.buttons:
                rows = event.message.buttons
                if rows:
                    row = rows[len(rows)//2]
                    btn = row[len(row)//2]
                    try:
                        await btn.click()
                        logger.info(f"[{account_name}] 🖱️ {btn.text}")
                        await asyncio.sleep(1)
                    except:
                        pass
        
        for _ in range(36):
            if success:
                break
            await asyncio.sleep(5)
        
        if not success:
            logger.warning(f"[{account_name}] ⏱️ Таймаут")
        
        await client.disconnect()
        return success
        
    except Exception as e:
        logger.error(f"[{account_name}] ❌ {e}")
        try:
            await client.disconnect()
        except:
            pass
        return False

# ========== ОБРАБОТЧИКИ ==========

async def start_cmd(update: Update, context):
    if update.effective_user.id != OWNER_ID:
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить аккаунт", callback_data="add_acc")],
        [InlineKeyboardButton("📋 Аккаунты", callback_data="list_acc")],
        [InlineKeyboardButton("🚀 НАЧАТЬ УЧАСТВОВАТЬ", callback_data="start_go")],
    ])
    await update.message.reply_text("🎯 *Главное меню*", reply_markup=keyboard, parse_mode="Markdown")

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
            "📱 Введите номер телефона:\nПример: +79123456789",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]])
        )
    
    elif data == "list_acc":
        accounts = get_all_accounts()
        if not accounts:
            text = "📭 Нет аккаунтов"
        else:
            text = f"📋 *Аккаунтов: {len(accounts)}*\n\n"
            for acc in accounts:
                text += f"• {acc[3] or acc[2] or 'Без имени'} — {acc[1]}\n"
        await query.edit_message_text(text, parse_mode="Markdown")
    
    elif data == "start_go":
        settings = get_settings()
        if 'channels' not in settings or 'ref_link' not in settings:
            user_state[user_id] = "waiting_channels"
            await query.edit_message_text(
                "📢 *Этап 1/2*\nВведите каналы через запятую:\nПример: @chan1, @chan2, @chan3",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]])
            )
        else:
            await start_participation(update, context)
    
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
            await update.message.reply_text(f"❌ Ошибка: {e}")
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
            
            await update.message.reply_text(f"✅ Аккаунт @{me.username or me.first_name} добавлен!")
            user_state.pop(user_id, None)
            temp_data.pop(user_id, None)
            await client.disconnect()
            
        except SessionPasswordNeededError:
            user_state[user_id] = "waiting_password"
            await update.message.reply_text("🔐 Введите пароль 2FA:")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
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
            
            await update.message.reply_text(f"✅ Аккаунт @{me.username or me.first_name} добавлен!")
            user_state.pop(user_id, None)
            temp_data.pop(user_id, None)
            await client.disconnect()
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
            user_state.pop(user_id, None)
            await client.disconnect()
    
    elif state == "waiting_channels":
        save_channels(text)
        user_state[user_id] = "waiting_link"
        await update.message.reply_text(
            "🔗 *Этап 2/2*\nВведите ссылку на конкурс:\nПример: https://t.me/bot?start=ref123",
            parse_mode="Markdown"
        )
    
    elif state == "waiting_link":
        save_ref_link(text)
        user_state.pop(user_id, None)
        await update.message.reply_text("✅ Данные сохранены! Нажмите *НАЧАТЬ УЧАСТВОВАТЬ*", parse_mode="Markdown")

async def start_participation(update: Update, context):
    query = update.callback_query
    settings = get_settings()
    accounts = get_all_accounts()
    
    if not accounts:
        await query.edit_message_text("❌ Нет аккаунтов!")
        return
    
    channels = settings.get('channels', '')
    ref_link = settings.get('ref_link', '')
    
    await query.edit_message_text(f"🚀 Запуск {len(accounts)} аккаунтов...")
    
    success_count = 0
    for acc in accounts:
        acc_id, phone, username, first_name, session = acc
        name = f"@{username or first_name or 'user'} ({phone})"
        
        await context.bot.send_message(OWNER_ID, f"🔄 {name}...")
        
        result = await participate_one_account(session, name, channels, ref_link, context.bot)
        if result:
            success_count += 1
        
        await asyncio.sleep(5)
    
    await context.bot.send_message(OWNER_ID, f"✅ *Завершено!*\nУспешно: {success_count}/{len(accounts)}", parse_mode="Markdown")

# ========== ЗАПУСК ==========

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("✅ Бот запущен (polling)")
    app.run_polling()

if __name__ == "__main__":
    main()
