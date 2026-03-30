import random
import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
from telegram.error import TimedOut, NetworkError

TOKEN = "8651605499:AAH-5_guYbiuIeVIlY7bGqL_j_B-OjtXg9A"

# ================= БОЛЬШОЙ СПИСОК ИЗВЕСТНЫХ ПЕРСОНАЖЕЙ =================
CATEGORIES = {
    "default": [
        "Дарт Вейдер", "Йода", "Гарри Поттер", "Гермиона Грейнджер", "Волан-де-Морт", "Джон Сноу",
        "Дейенерис Таргариен", "Тирион Ланнистер", "Шерлок Холмс", "Джек Воробей", "Индиана Джонс",
        "Терминатор", "Нео", "Форрест Гамп", "Мистер Бин", "Доктор Хаус", "Шелдон Купер",
        "Губка Боб", "Патрик Стар", "Шрек", "Осёл", "Эльза", "Симба", "Скар", "Винни-Пух",
        "Пикачу", "Марио", "Луиджи", "Чебурашка", "Крокодил Гена", "Маша", "Медведь",
        "Наполеон", "Клеопатра", "Альберт Эйнштейн", "Леонардо да Винчи", "Никола Тесла",
        "Лев", "Тигр", "Панда", "Смартфон", "Пицца", "Лампочка", "Автомобиль"
    ],
    "кино": [
        "Дарт Вейдер", "Йода", "Джек Воробей", "Индиана Джонс", "Терминатор", "Нео", "Форрест Гамп",
        "Железный человек", "Капитан Америка", "Бэтмен", "Супермен", "Чудо-женщина", "Джокер",
        "Харли Квинн", "Росомаха", "Дэдпул", "Локи", "Тор"
    ],
    "мультики": [
        "Губка Боб", "Патрик Стар", "Шрек", "Осёл", "Эльза", "Анна", "Моана", "Симба", "Винни-Пух",
        "Пикачу", "Марио", "Чебурашка", "Маша", "Медведь", "Карлсон", "Матроскин", "Снежная Королева"
    ],
    "знаменитости": [
        "Тейлор Свифт", "Бейонсе", "Ариана Гранде", "Билли Айлиш", "Илон Маск", "Леонардо ДиКаприо",
        "Скарлетт Йоханссон", "Марго Робби", "Том Круз", "Дженнифер Лоуренс", "Майкл Джексон",
        "Фредди Меркьюри", "Элвис Пресли", "Мэрилин Монро", "Брэд Питт"
    ],
    "вещи": [
        "Смартфон", "Пицца", "Гамбургер", "Лампочка", "Телевизор", "Автомобиль", "Самолёт",
        "Гитара", "Микрофон", "Корона", "Меч", "Зеркало", "Кофе", "Шоколадка", "Мороженое",
        "Наушники", "Книга", "Футбольный мяч"
    ],
    "русские": [
        "Чебурашка", "Крокодил Гена", "Маша", "Медведь", "Карлсон", "Матроскин", "Шарик",
        "Дядя Фёдор", "Волк (Ну погоди)", "Заяц (Ну погоди)", "Ёжик в тумане"
    ]
}

games = {}        # игры на стадии сбора
active_games = {} # активные игры
waiting_for_name = {}  # словарь для отслеживания ожидающих ввода имени

# Декоратор для повторных попыток
async def retry_on_error(func, *args, **kwargs):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except (TimedOut, NetworkError) as e:
            if attempt == max_retries - 1:
                logging.error(f"Ошибка после {max_retries} попыток: {e}")
                return None
            logging.warning(f"Ошибка {e}, попытка {attempt + 1}/{max_retries}")
            await asyncio.sleep(2 ** attempt)
    return None

# ================= /help =================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """🎮 <b>Игра «Кто я?»</b>

<b>Правила игры:</b>
1️⃣ Создатель запускает игру командой /whyme
2️⃣ Участники нажимают кнопку "Присоединиться" и вводят имя
3️⃣ После сбора всех игроков, каждому в ЛС приходит список персонажей ДРУГИХ участников
4️⃣ Твой персонаж НЕИЗВЕСТЕН тебе
5️⃣ Задавай вопросы в группе, чтобы угадать своего персонажа
6️⃣ Когда угадаешь - бот напишет в чате поздравление!

<b>Команды:</b>
/whyme (число) [категория] — запустить игру
/help — показать эту справку

<b>Примеры:</b>
/whyme 6
/whyme 5 кино
/whyme 8 мультики

<b>Доступные категории:</b> кино, мультики, знаменитости, вещи, русские

Приятной игры! 🔥"""

    await retry_on_error(
        update.message.reply_text,
        help_text,
        parse_mode=ParseMode.HTML
    )


async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем, что команда вызвана в группе
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ Игру можно запустить только в группе!")
        return
        
    args = context.args
    if not args:
        await retry_on_error(
            update.message.reply_text,
            "Использование: /whyme (число от 3 до 10) [категория]\nНапример: /whyme 6 кино\nНапиши /help"
        )
        return

    try:
        max_p = int(args[0])
        if not 3 <= max_p <= 10:
            raise ValueError
    except:
        await retry_on_error(
            update.message.reply_text,
            "Число участников должно быть от 3 до 10"
        )
        return

    category = args[1].lower() if len(args) > 1 else "default"
    if category not in CATEGORIES:
        category = "default"

    chat_id = update.effective_chat.id
    creator_id = update.effective_user.id

    # Проверяем, есть ли уже игра в этом чате
    if chat_id in games or chat_id in active_games:
        await retry_on_error(
            update.message.reply_text,
            "⚠️ В этом чате уже есть активная игра!"
        )
        return

    # Получаем username бота для ссылки
    try:
        bot_info = await retry_on_error(context.bot.get_me)
        bot_username = bot_info.username if bot_info else "whyme_bot"
    except:
        bot_username = "whyme_bot"

    keyboard = [
        [InlineKeyboardButton("👥 Присоединиться к игре", url=f"https://t.me/{bot_username}?start=join_{chat_id}")],
        [InlineKeyboardButton("❌ Отменить игру", callback_data=f"cancel_{chat_id}")]
    ]

    try:
        msg = await retry_on_error(
            update.message.reply_text,
            f"🎮 <b>Игра «Кто я?»</b> запущена!\n\n"
            f"Максимум: <b>{max_p}</b>\n"
            f"Категория: <b>{category}</b>\n"
            f"Участников: <b>0/{max_p}</b>\n\n"
            f"⏳ Время на сбор: 10 минут",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await retry_on_error(
            update.message.reply_text,
            "❌ Ошибка при запуске игры. Попробуйте позже."
        )
        logging.error(f"Ошибка при запуске игры: {e}")
        return

    if msg:
        games[chat_id] = {
            "max_players": max_p,
            "players": [],
            "names": {},
            "message_id": msg.message_id,
            "category": category,
            "creator": creator_id,
            "start_time": datetime.now()
        }
        
        # Запускаем таймер на 10 минут
        asyncio.create_task(auto_cancel_game(chat_id, context))


async def auto_cancel_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(600)  # 10 минут
    if chat_id in games:
        try:
            await context.bot.send_message(chat_id, "⏰ Время вышло. Не набралось участников — игра отменена.")
        except:
            pass
        if chat_id in games:
            del games[chat_id]


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not str(context.args[0]).startswith("join_"):
        return
    
    # Проверяем, что это личное сообщение
    if update.effective_chat.type != "private":
        return
        
    try:
        chat_id = int(str(context.args[0]).split("_")[1])
    except:
        await update.message.reply_text("❌ Неверная ссылка для присоединения.")
        return

    if chat_id not in games:
        await retry_on_error(
            update.message.reply_text,
            "❌ Игра уже закончилась или ещё не началась."
        )
        return

    game = games[chat_id]
    user_id = update.effective_user.id

    if user_id in game["players"]:
        await retry_on_error(
            update.message.reply_text,
            "✅ Ты уже участвуешь в игре!"
        )
        return
    if len(game["players"]) >= game["max_players"]:
        await retry_on_error(
            update.message.reply_text,
            "❌ Игра уже заполнена."
        )
        return

    # Сохраняем, что пользователь ожидает ввода имени
    waiting_for_name[user_id] = chat_id
    await retry_on_error(
        update.message.reply_text,
        "📝 Отправь своё имя, которое будет отображаться в игре (от 2 до 20 символов):"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех текстовых сообщений"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    # Проверяем, ожидает ли пользователь ввода имени
    if user_id in waiting_for_name:
        game_chat_id = waiting_for_name[user_id]
        
        # Проверяем, существует ли игра
        if game_chat_id not in games:
            await update.message.reply_text("❌ Игра уже закончилась.")
            del waiting_for_name[user_id]
            return
            
        game = games[game_chat_id]
        
        # Проверяем имя
        if len(text) < 2 or len(text) > 20:
            await update.message.reply_text("❌ Имя должно быть от 2 до 20 символов. Попробуй ещё раз:")
            return
            
        # Проверяем, не заполнена ли игра
        if len(game["players"]) >= game["max_players"]:
            await update.message.reply_text("❌ Игра уже заполнена.")
            del waiting_for_name[user_id]
            return
            
        # Добавляем игрока
        game["players"].append(user_id)
        game["names"][user_id] = text
        
        await update.message.reply_text(
            f"✅ Ты зарегистрирован как <b>{text}</b>! Ожидай начала игры.",
            parse_mode=ParseMode.HTML
        )
        
        # Удаляем из ожидания
        del waiting_for_name[user_id]
        
        # Обновляем сообщение в группе
        await update_group_message(game_chat_id, context)
        
        # Проверяем, набралось ли нужное количество игроков
        if len(game["players"]) == game["max_players"]:
            await start_the_game(game_chat_id, context)
        return
    
    # Если это сообщение в группе и игра активна, проверяем угадывание
    if chat_id in active_games:
        await check_guess(update, context)


async def update_group_message(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    if chat_id not in games:
        return
    game = games[chat_id]
    current = len(game["players"])

    try:
        bot_info = await retry_on_error(context.bot.get_me)
        bot_username = bot_info.username if bot_info else "whyme_bot"
    except:
        bot_username = "whyme_bot"
    
    keyboard = [
        [InlineKeyboardButton("👥 Присоединиться к игре", url=f"https://t.me/{bot_username}?start=join_{chat_id}")],
        [InlineKeyboardButton("❌ Отменить игру", callback_data=f"cancel_{chat_id}")]
    ]

    try:
        await retry_on_error(
            context.bot.edit_message_text,
            chat_id=chat_id,
            message_id=game["message_id"],
            text=f"🎮 <b>Игра «Кто я?»</b>\n\n"
                 f"Максимум: <b>{game['max_players']}</b>\n"
                 f"Категория: <b>{game['category']}</b>\n"
                 f"Участников: <b>{current}/{game['max_players']}</b>\n\n"
                 f"⏳ Время на сбор: 10 минут",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(f"Ошибка обновления сообщения: {e}")


async def start_the_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    game = games.pop(chat_id)
    players = game["players"]
    char_list = CATEGORIES[game["category"]]

    # Выбираем персонажей
    if len(players) <= len(char_list):
        selected = random.sample(char_list, len(players))
    else:
        selected = random.choices(char_list, k=len(players))

    # Создаем словарь: user_id -> его персонаж
    user_characters = dict(zip(players, selected))
    
    # Сохраняем игру
    active_games[chat_id] = {
        "characters": user_characters,
        "names": game["names"],
        "guessed": set()  # для отслеживания угадавших
    }

    # Отправляем сообщение в группу о начале игры
    players_list = ", ".join([game["names"][p] for p in players])
    await retry_on_error(
        context.bot.send_message,
        chat_id,
        f"🎉 <b>Игра «Кто я?» началась!</b>\n\n"
        f"👥 Участники: {players_list}\n"
        f"📚 Категория: {game['category']}\n\n"
        f"💡 <b>Правила:</b>\n"
        f"• Ты НЕ ЗНАЕШЬ своего персонажа\n"
        f"• Задавай вопросы в чате (например: \"Я животное?\", \"Я из мультфильма?\")\n"
        f"• Другие игроки отвечают ДА или НЕТ\n"
        f"• Когда угадаешь - напиши в чате название персонажа!\n\n"
        f"🎯 Удачи всем! 🔥",
        parse_mode=ParseMode.HTML
    )

    # Отправляем каждому игроку список персонажей ДРУГИХ игроков (не его собственного!)
    for user_id in players:
        # Получаем персонажа этого игрока
        my_character = user_characters[user_id]
        
        # Создаем список персонажей других игроков
        other_characters = []
        for other_id, char in user_characters.items():
            if other_id != user_id:
                other_characters.append(f"• {game['names'][other_id]} → {char}")
        
        other_characters_text = "\n".join(other_characters) if other_characters else "Нет других игроков"
        
        name = game["names"][user_id]
        
        try:
            await retry_on_error(
                context.bot.send_message,
                user_id,
                f"🎮 <b>Игра «Кто я?» началась!</b>\n\n"
                f"👤 Твоё имя: <b>{name}</b>\n"
                f"❓ <b>Твой персонаж НЕИЗВЕСТЕН!</b>\n\n"
                f"<b>Персонажи других игроков:</b>\n{other_characters_text}\n\n"
                f"💡 <b>Как играть:</b>\n"
                f"1. Задавай вопросы в группе, чтобы угадать своего персонажа\n"
                f"2. Другие игроки будут отвечать на твои вопросы\n"
                f"3. Когда угадаешь - напиши в чате название персонажа!\n\n"
                f"🎯 Удачи! 🔥",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")


async def cancel_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        chat_id = int(query.data.split("_")[1])
    except:
        return

    if chat_id in games:
        if query.from_user.id == games[chat_id]["creator"]:
            await retry_on_error(
                context.bot.send_message,
                chat_id,
                "❌ Игра отменена создателем."
            )
            games.pop(chat_id, None)
            try:
                await query.message.delete()
            except:
                pass
        else:
            await query.answer("❌ Только создатель игры может её отменить!", show_alert=True)
    else:
        await query.answer("Игра уже завершена!", show_alert=True)


async def check_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет, угадал ли игрок своего персонажа"""
    chat_id = update.effective_chat.id
    if chat_id not in active_games:
        return

    user_id = update.effective_user.id
    text = update.message.text.lower()
    message = update.message

    game_data = active_games[chat_id]
    
    # Проверяем, есть ли такой игрок в игре и не угадал ли он уже
    if user_id in game_data["characters"] and user_id not in game_data["guessed"]:
        my_character = game_data["characters"][user_id].lower()
        
        # Проверяем угадывание (игрок должен написать название персонажа)
        if my_character in text or any(word in text for word in my_character.split()):
            # Помечаем игрока как угадавшего
            game_data["guessed"].add(user_id)
            
            name = game_data["names"][user_id]
            character = game_data["characters"][user_id]
            
            # Отправляем поздравление в чат
            await retry_on_error(
                context.bot.send_message,
                chat_id,
                f"🎉 <b>{name}</b> угадал своего персонажа!\n\n"
                f"✨ Его персонаж: <b>{character}</b>\n\n"
                f"🔥 Поздравляем! 🎊",
                parse_mode=ParseMode.HTML
            )
            
            # Отправляем личное сообщение игроку
            try:
                await retry_on_error(
                    context.bot.send_message,
                    user_id,
                    f"🎉 <b>Поздравляю! Ты угадал!</b>\n\n"
                    f"Твой персонаж: <b>{character}</b>\n\n"
                    f"Отличная игра! 🔥",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            
            # Проверяем, все ли угадали
            if len(game_data["guessed"]) == len(game_data["characters"]):
                await retry_on_error(
                    context.bot.send_message,
                    chat_id,
                    f"🏆 <b>ИГРА ОКОНЧЕНА!</b> 🏆\n\n"
                    f"Все участники угадали своих персонажей!\n\n"
                    f"Спасибо за игру! 🎉",
                    parse_mode=ParseMode.HTML
                )
                # Удаляем игру
                del active_games[chat_id]
            
            # Удаляем сообщение с угадыванием, чтобы не засорять чат
            try:
                await message.delete()
            except:
                pass


def main():
    # Настройка логирования
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    
    # Увеличиваем таймауты для запросов
    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0
    )
    
    app = Application.builder().token(TOKEN).request(request).build()

    # Добавляем обработчики
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("whyme", start_game))
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CallbackQueryHandler(cancel_game, pattern="^cancel_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Бот «Кто я?» успешно запущен!")
    print("📝 Используй /help для справки")
    print("🎮 Для запуска игры используй /whyme в группе")
    
    # Запуск с обработкой ошибок
    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
    except Exception as e:
        logging.error(f"Ошибка при запуске: {e}")


if __name__ == "__main__":
    main()