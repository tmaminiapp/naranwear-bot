import asyncio
import json
import logging
import threading
import os
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, firestore
from telegram import Update, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

# Загружаем переменные окружения из .env файла (для локальной разработки)
load_dotenv()

# ===== ВСЕ КЛЮЧИ БЕРУТСЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ =====
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY")
MAP_KEY = os.environ.get("MAP_KEY")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://tmaminiapp.github.io/naranwear/")
FIREBASE_CRED_JSON = os.environ.get("FIREBASE_CRED_JSON")  # JSON строка с credentials
FIREBASE_CRED_PATH = os.environ.get("FIREBASE_CRED_PATH", "firebase-key.json")
# =====================================================

# Проверка наличия обязательных переменных
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не установлен в переменных окружения!")
if not ADMIN_ID:
    raise ValueError("❌ ADMIN_ID не установлен в переменных окружения!")
if not IMGBB_API_KEY:
    raise ValueError("❌ IMGBB_API_KEY не установлен в переменных окружения!")
if not MAP_KEY:
    raise ValueError("❌ MAP_KEY не установлен в переменных окружения!")

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ===== FLASK ПРИЛОЖЕНИЕ =====
app = Flask(__name__)
CORS(app)

# Глобальные переменные
db_fs = None
bot_application = None
bot_thread = None


# ===== FIREBASE ИНИЦИАЛИЗАЦИЯ =====
def init_firebase():
    global db_fs
    try:
        # Приоритет 1: JSON строка из переменной окружения
        if FIREBASE_CRED_JSON:
            try:
                cred_dict = json.loads(FIREBASE_CRED_JSON)
                cred = credentials.Certificate(cred_dict)
                firebase_admin.initialize_app(cred)
                db_fs = firestore.client()
                print("✅ Firebase успешно подключен через JSON переменную")
                return
            except json.JSONDecodeError as e:
                print(f"⚠️ Ошибка парсинга FIREBASE_CRED_JSON: {e}")

        # Приоритет 2: файл с credentials
        if os.path.exists(FIREBASE_CRED_PATH):
            cred = credentials.Certificate(FIREBASE_CRED_PATH)
            firebase_admin.initialize_app(cred)
            db_fs = firestore.client()
            print(f"✅ Firebase успешно подключен через файл {FIREBASE_CRED_PATH}")
            return

        print("⚠️ Firebase credentials не найдены. Продолжаем без Firebase...")

    except Exception as e:
        print(f"❌ Ошибка Firebase: {e}")


# ===== ЗАПУСК БОТА В ФОНЕ =====
def run_bot():
    """Запускает Telegram бота в отдельном потоке"""
    global bot_application

    # Создание приложения бота
    bot_application = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_application.add_handler(CommandHandler('start', start))
    bot_application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))

    # Запуск Firebase слушателя (если нужно)
    if db_fs:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            threading.Thread(target=setup_firebase_listener, args=(loop, bot_application), daemon=True).start()
        except Exception as e:
            print(f"❌ Ошибка запуска слушателя: {e}")

    print("🚀 Бот запущен в фоне...")
    bot_application.run_polling()


# ===== ОБРАБОТЧИКИ БОТА =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("Открыть Магазин", web_app=WebAppInfo(url=WEBAPP_URL))]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Добро пожаловать в NARAN!", reply_markup=reply_markup)


async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw_json = update.effective_message.web_app_data.data
        data = json.loads(raw_json)
        user_id = update.effective_user.id

        order_id = data.get('order_id', '???')
        name = data.get('customer_name') or data.get('name') or 'Не указано'
        phone = data.get('customer_phone') or data.get('phone') or 'Не указано'
        address = data.get('address') or data.get('customer_address') or 'Не указан'
        delivery = data.get('delivery') or data.get('delivery_type') or 'Не выбрана'
        total = data.get('order_total') or data.get('total') or 0

        # Формируем список товаров
        items_list = data.get('items_text')
        if not items_list and 'items' in data:
            items = data.get('items', [])
            items_list = "\n".join(
                [f"▫️ {i.get('title')} ({i.get('size') or i.get('selSize') or '-'}) — {i.get('price')} ₽" for i in
                 items])

        if not items_list:
            items_list = "Состав не указан"

        # Сохраняем в Firebase
        if db_fs:
            order_entry = {
                **data,
                'status': 'Новый',
                'user': {'id': user_id},
                'createdAt': firestore.SERVER_TIMESTAMP
            }
            db_fs.collection("orders").add(order_entry)
            print(f"✅ Заказ #{order_id} сохранен в Firebase")

        # Сообщение админу
        admin_message = (
            f"🛍 <b>НОВЫЙ ЗАКАЗ #{order_id}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Клиент:</b> {name}\n"
            f"📞 <b>Телефон:</b> <code>{phone}</code>\n"
            f"🚚 <b>Доставка:</b> {delivery}\n"
            f"📍 <b>Адрес:</b> {address}\n\n"
            f"📋 <b>СОСТАВ ЗАКАЗА:</b>\n{items_list}\n\n"
            f"💰 <b>ИТОГО: {total} ₽</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <a href='tg://user?id={user_id}'>Связаться с клиентом</a>"
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_message,
            parse_mode='HTML',
            disable_web_page_preview=True
        )

        await update.message.reply_text(f"✅ Заказ #{order_id} принят!")

    except Exception as e:
        logging.error(f"Ошибка в web_app_data: {e}")


# ===== СЛУШАТЕЛЬ FIREBASE =====
def setup_firebase_listener(loop, application):
    global db_fs
    if db_fs is None:
        print("⚠️ Firebase не подключен, слушатель не запущен")
        return

    def on_snapshot(col_snapshot, changes, read_time):
        for change in changes:
            if change.type.name == 'MODIFIED':
                order_data = change.document.to_dict()
                status = order_data.get('status')
                order_id = order_data.get('order_id')
                client_id = order_data.get('user', {}).get('id')

                if client_id:
                    if status == 'Отправлен':
                        msg = f"📦 <b>Ваш заказ #{order_id} отправлен!</b>\nСкоро он будет у вас. Спасибо за покупку! ✨"
                    elif status == 'Доставлен':
                        msg = f"✅ <b>Ваш заказ #{order_id} доставлен!</b>\nНадеемся, вам всё понравилось. Будем рады вашему отзыву! ✨"
                    else:
                        return

                    asyncio.run_coroutine_threadsafe(
                        application.bot.send_message(chat_id=client_id, text=msg, parse_mode='HTML'),
                        loop
                    )
                    print(f"📩 Уведомление ({status}) ушло клиенту {client_id}")

    db_fs.collection('orders').on_snapshot(on_snapshot)
    print("👂 Firebase слушатель запущен")


# ===== FLASK МАРШРУТЫ =====
@app.route('/')
def home():
    return "✅ NARAN BOT WORKS! Flask + Telegram Bot"


@app.route('/api/upload', methods=['POST'])
def upload_image():
    """Прокси для загрузки фото на ImgBB"""
    try:
        file = request.files['image']
        response = requests.post(
            f'https://api.imgbb.com/1/upload?key={IMGBB_API_KEY}',
            files={'image': (file.filename, file.stream, file.content_type)}
        )
        return jsonify(response.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/geocode')
def geocode():
    """Прокси для 2GIS карт"""
    try:
        lat = request.args.get('lat')
        lon = request.args.get('lon')
        response = requests.get(
            'https://catalog.api.2gis.com/3.0/items/geocode',
            params={'lat': lat, 'lon': lon, 'key': MAP_KEY}
        )
        return jsonify(response.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/firebase/save', methods=['POST'])
def save_to_firebase():
    """Прокси для сохранения в Firebase"""
    try:
        data = request.json
        # Здесь можно добавить сохранение в Firebase
        return jsonify({'status': 'ok', 'data': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config')
def get_config():
    """Возвращает публичные ключи для фронтенда"""
    return jsonify({
        'MAP_KEY': MAP_KEY,  # 2GIS ключ можно оставить публичным
        # НЕ возвращаем секретные ключи!
        'hasImgBB': bool(IMGBB_API_KEY),
        'webapp_url': WEBAPP_URL
    })


@app.route('/health')
def health():
    """Health check для Koyeb"""
    return jsonify({'status': 'ok'}), 200


# ===== ЗАПУСК =====
if __name__ == '__main__':
    # Инициализация Firebase
    init_firebase()

    # Запуск бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Получаем порт из окружения (Koyeb передаст его)
    port = int(os.environ.get('PORT', 8000))

    print(f"✅ Flask сервер запускается на порту {port}...")
    # Запускаем Flask (это будет основной процесс)
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)