import json
import os
import time
import logging
import requests
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Настраиваем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальные переменные
episode_tracker = {}
chat_ids = []
last_request_times = {}
request_interval = 12 * 3600  # Интервал между запросами свежей подборки (12 часов)
episode_tracker_file = "episode_tracker.json"
chat_ids_file = "chat_ids.json"

API_TOKEN = '5160413773:AAGyjpQbrAL-1hR6bnV8GwDY3ioIjxBVRzk'  # Замените на ваш токен


# Класс для хранения данных об аниме
class Anime:
    def __init__(self, id, name, russian, image, score, episodes, episodes_aired, url, status, **kwargs):
        self.id = id
        self.name = name
        self.title = russian  # Используем поле 'russian' для названия на русском
        self.image = f"https://shikimori.one{image['original']}"  # Строим полный URL изображения
        self.score = score
        self.episodes_all = episodes  # Количество серий всего
        self.episode = episodes_aired  # Количество вышедших серий
        self.url = f"https://shikimori.one{url}"  # Строим полный URL страницы аниме
        self.status = status  # Статус аниме (например, анонс)

    def format_anime(self):
        title = self.title if self.title else self.name
        episodes_all = str(self.episodes_all) if self.episodes_all > 0 else "?"
        return f"{title}\nРейтинг: {self.score} ⭐️\nСерии: {self.episode} из {episodes_all} 📺\nСсылка: {self.url}"


# Загрузка трекера эпизодов
def load_episode_tracker():
    global episode_tracker
    if os.path.exists(episode_tracker_file):
        try:
            with open(episode_tracker_file, "r", encoding="utf-8") as file:
                if os.stat(episode_tracker_file).st_size == 0:
                    episode_tracker = {}
                else:
                    episode_tracker = json.load(file)
        except json.JSONDecodeError:
            logger.error("Ошибка: файл episode_tracker.json поврежден или пуст. Создан новый файл.")
            episode_tracker = {}
    else:
        logger.info("Файл episode_tracker.json не найден. Создан новый файл.")
        episode_tracker = {}


# Загрузка chat_ids
def load_chat_ids():
    if os.path.exists(chat_ids_file):
        with open(chat_ids_file, 'r') as file:
            global chat_ids
            chat_ids = json.load(file)


# Сохранение трекера эпизодов
def save_episode_tracker():
    with open(episode_tracker_file, "w", encoding="utf-8") as file:
        json.dump(episode_tracker, file, ensure_ascii=False, indent=4)


# Сохранение chat_ids
def save_chat_ids():
    with open(chat_ids_file, 'w') as file:
        json.dump(chat_ids, file)


# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    if chat_id not in chat_ids:
        chat_ids.append(chat_id)
        save_chat_ids()

    keyboard = [['Свежая подборка']]  # Клавиатура с одной кнопкой
    await update.message.reply_text(
        "Привет! Я бот, который сообщает о новинках аниме и позволяет подписаться на уведомления о выходе новых серий 📺 ✨",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )


# Функция для получения свежих аниме
async def fresh_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    current_time = time.time()

    # Проверка времени последнего запроса для предотвращения частого запроса
    if chat_id in last_request_times:
        if current_time - last_request_times[chat_id] < request_interval:
            await update.message.reply_text("Вы можете запросить свежую подборку раз в 12 часов ⏰.\nПопробуйте позже ⌛️")
            return

    last_request_times[chat_id] = current_time
    animes = get_animes_from_shikimori()

    for anime in animes:
        # Проверяем, что аниме не в статусе "анонс" и у него вышло хотя бы 1 эпизод
        if anime.status == "anons" or anime.episode == 0:
            continue

        # Проверка на дублирование
        if anime.id in episode_tracker and anime.episode <= episode_tracker[anime.id]:
            continue  # Пропускаем уже отправленные эпизоды

        episode_tracker[anime.id] = anime.episode
        save_episode_tracker()

        # Проверка наличия и корректности URL изображения
        if anime.image:
            try:
                await context.bot.send_photo(chat_id, photo=anime.image, caption=anime.format_anime())
            except Exception as e:
                # Отправляем только текст, если что-то пошло не так с изображением
                await context.bot.send_message(chat_id, text=anime.format_anime())
                logger.error(f"Ошибка отправки изображения для аниме {anime.name}: {e}")
        else:
            # Если нет изображения, отправляем только текст
            await context.bot.send_message(chat_id, text=anime.format_anime())


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
def get_animes_from_shikimori():
    season = get_current_season()
    url = f"https://shikimori.one/api/animes?season={season}&kind=tv&limit=99"
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'pythonBot/1.0'  # Убедитесь, что вы изменили на имя вашего бота
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        animes_data = response.json()
        return [Anime(**anime) for anime in animes_data]
    except requests.RequestException as e:
        logger.error(f"Не удалось получить аниме: {e}")
        return []


# Основная функция для запуска бота
def main():
    load_episode_tracker()
    load_chat_ids()

    application = ApplicationBuilder().token(API_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fresh_anime))

    application.run_polling()


if __name__ == '__main__':
    main()
