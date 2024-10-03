import json
import os
import time
import logging

import pytz
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настраиваем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальные переменные
subscriptions = {}  # Структура вида: {"chat_id": {"anime_id": episodes_aired}}
last_request_times = {}
request_interval = 2 * 3600  # Интервал между запросами свежей подборки (2 часа)

subscriptions_file = "subscriptions.json"
API_TOKEN = ('@@@@@')  # Замените на ваш токен

cache = {"data": None, "timestamp": 0}
cache_duration = 3600  # 1 час

# Класс для хранения данных об аниме
class Anime:
    def __init__(self, id, name, russian, image, score, episodes, episodes_aired, **kwargs):
        self.id = id
        self.name = name
        self.title = russian  # Используем поле 'russian' для названия на русском
        self.image = image
        self.score = score
        self.episodes_all = episodes  # Количество серий всего
        self.episodes_aired = episodes_aired  # Количество вышедших серий

    def format_anime(self):
        title = self.title if self.title else self.name
        episodes_all = str(self.episodes_all) if self.episodes_all > 0 else "?"
        return f"{title}\nРейтинг: {self.score} ⭐️\nСерии: {self.episodes_aired} из {episodes_all} 📺\nСсылка: https://shikimori.one/animes/{self.id}"

# Загрузка подписок
def load_subscriptions():
    global subscriptions
    if os.path.exists(subscriptions_file):
        with open(subscriptions_file, "r", encoding="utf-8") as file:
            if os.stat(subscriptions_file).st_size == 0:
                subscriptions = {}
            else:
                subscriptions = json.load(file)
    else:
        subscriptions = {}

# Сохранение подписок
def save_subscriptions():
    with open(subscriptions_file, "w", encoding="utf-8") as file:
        json.dump(subscriptions, file, ensure_ascii=False, indent=4)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat.id)
    if chat_id not in subscriptions:
        subscriptions[chat_id] = {}
        save_subscriptions()

    # Создаем клавиатуру с кнопками "Свежая подборка" и "Подписки"
    keyboard = [
        ["Свежая подборка", "Подписки"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    # Ответ на команду /start с клавиатурой
    await update.message.reply_text(
        "Привет! Я бот, который сообщает о новинках аниме и позволяет подписаться на уведомления о выходе новых серий 📺 ✨\n\nНажмите на кнопку 'Свежая подборка', чтобы посмотреть новые аниме, или на 'Подписки', чтобы увидеть список аниме, на которые вы подписаны.",
        reply_markup=reply_markup
    )

# Функция для получения свежих аниме
async def fresh_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat.id)
    current_time = time.time()

    # Проверка времени последнего запроса для предотвращения частого запроса
    if chat_id in last_request_times:
        if current_time - last_request_times[chat_id] < request_interval:
            await update.message.reply_text("Вы можете запросить свежую подборку раз в 2 часа ⏰.\nПопробуйте позже ⌛️")
            return

    last_request_times[chat_id] = current_time
    animes = get_animes_from_shikimori()

    for anime in animes:
        # Проверяем, подписан ли пользователь на это аниме
        if chat_id in subscriptions and str(anime.id) in subscriptions[chat_id]:
            button_text = "Отписаться"
        else:
            button_text = "Подписаться"

        keyboard = [[InlineKeyboardButton(button_text, callback_data=str(anime.id))]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_photo(photo=f"https://shikimori.one{anime.image['original']}", caption=anime.format_anime(), reply_markup=reply_markup)

# Функция для отображения списка подписок
async def show_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat.id)

    if chat_id not in subscriptions or not subscriptions[chat_id]:
        await update.message.reply_text("Вы не подписаны ни на одно аниме.")
        return

    subscribed_anime_ids = subscriptions[chat_id].keys()
    animes = get_animes_from_shikimori()
    subscribed_animes = [anime for anime in animes if str(anime.id) in subscribed_anime_ids]

    if not subscribed_animes:
        await update.message.reply_text("Вы не подписаны ни на одно аниме.")
    else:
        for anime in subscribed_animes:
            keyboard = [[InlineKeyboardButton("Отписаться", callback_data=str(anime.id))]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_photo(photo=f"https://shikimori.one{anime.image['original']}", caption=anime.format_anime(), reply_markup=reply_markup)


# Получение текущего сезона
def get_current_season():
    year, month, _ = time.localtime()[:3]
    seasons = {
        12: "winter", 1: "winter", 2: "winter",
        3: "spring", 4: "spring", 5: "spring",
        6: "summer", 7: "summer", 8: "summer",
        9: "fall", 10: "fall", 11: "fall"
    }
    return f"{seasons[month]}_{year}"

# Получение аниме с Shikimori API
# Глобальный кэш
cache = {"data": None, "timestamp": 0}
cache_duration = 3600  # 1 час (в секундах)

def get_animes_from_shikimori():
    current_time = time.time()

    # Если данные в кэше актуальны, возвращаем их
    if cache["data"] and current_time - cache["timestamp"] < cache_duration:
        return cache["data"]

    season = get_current_season()
    url = f"https://shikimori.one/api/animes?season={season}&kind=tv&limit=99"
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'pythonBot/1.0'
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        animes_data = response.json()

        # Преобразуем данные и сортируем аниме по рейтингу (от большего к меньшему)
        animes = [Anime(**anime) for anime in animes_data]
        sorted_animes = sorted(animes, key=lambda anime: anime.score, reverse=True)

        # Сохраняем результат в кэш
        cache["data"] = sorted_animes
        cache["timestamp"] = current_time

        return sorted_animes
    except requests.RequestException as e:
        logger.error(f"Не удалось получить аниме: {e}")
        return []


# Обработка нажатия кнопки подписки/отписки
async def toggle_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat.id)
    anime_id = query.data

    animes = get_animes_from_shikimori()
    anime = next((a for a in animes if str(a.id) == anime_id), None)

    if not anime:
        await query.answer("Аниме не найдено.")
        return

    if chat_id not in subscriptions:
        subscriptions[chat_id] = {}

    if anime_id in subscriptions[chat_id]:
        del subscriptions[chat_id][anime_id]
        await query.answer(f"Вы отписались от аниме: {anime.title}.")
    else:
        subscriptions[chat_id][anime_id] = anime.episodes_aired  # Сохраняем текущее кол-во серий
        await query.answer(f"Вы подписались на аниме: {anime.title}.")

    save_subscriptions()

    # Обновляем текст кнопки
    button_text = "Отписаться" if anime_id in subscriptions[chat_id] else "Подписаться"
    keyboard = [[InlineKeyboardButton(button_text, callback_data=str(anime_id))]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Проверяем, изменились ли кнопки
    if query.message.reply_markup.inline_keyboard[0][0].text != button_text:
        await query.message.edit_reply_markup(reply_markup)

# Проверка новых серий для подписанных аниме
async def notify_new_episodes(context: ContextTypes.DEFAULT_TYPE):
    animes = get_animes_from_shikimori()

    for chat_id, subscribed_animes in subscriptions.items():
        for anime in animes:
            anime_id = str(anime.id)
            if anime_id in subscribed_animes:
                previous_episodes = subscribed_animes[anime_id]
                if anime.episodes_aired > previous_episodes:
                    # Обновляем кол-во серий и уведомляем пользователя
                    subscriptions[chat_id][anime_id] = anime.episodes_aired
                    save_subscriptions()

                    # Формируем кнопку для подписки/отписки
                    button_text = "Отписаться" if anime_id in subscriptions[chat_id] else "Подписаться"
                    keyboard = [[InlineKeyboardButton(button_text, callback_data=str(anime.id))]]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    # Отправляем уведомление как в подборке, с добавлением текста о новой серии
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=f"https://shikimori.one{anime.image['original']}",
                        caption=f"Вышла новая серия аниме: {anime.format_anime()}",
                        reply_markup=reply_markup
                    )
                    logger.info(f"Уведомление отправлено для {anime.title} ({chat_id})")

# Основная функция
def main():
    load_subscriptions()

    # Инициализация приложения Telegram
    application = ApplicationBuilder().token(API_TOKEN).build()

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex("Свежая подборка"), fresh_anime))
    application.add_handler(MessageHandler(filters.Regex("Подписки"), show_subscriptions))
    application.add_handler(CallbackQueryHandler(toggle_subscription))

    # Планировщик для проверки новых серий
    scheduler = AsyncIOScheduler(timezone=pytz.utc)  # Указываем временную зону
    scheduler.add_job(notify_new_episodes, 'interval', seconds=5, args=[application])
    scheduler.start()

    # Запуск бота
    application.run_polling()

if __name__ == "__main__":
    main()