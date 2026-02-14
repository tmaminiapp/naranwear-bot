import asyncio
import json
import logging
import os
import sqlite3
import threading
from dotenv import load_dotenv  # <-- ДОБАВЛЕНО

import firebase_admin
from firebase_admin import credentials, firestore
from telegram import Update, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# Загружаем переменные из .env
load_dotenv()  # <-- ДОБАВЛЕНО

# 1. Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Читаем переменные из окружения
TOKEN = os.getenv('BOT_TOKEN')  # <-- ИЗМЕНЕНО
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))  # <-- ИЗМЕНЕНО
WEBAPP_URL = os.getenv('WEBAPP_URL')  # <-- ИЗМЕНЕНО
FIREBASE_CRED_PATH = os.getenv('FIREBASE_CRED_PATH', 'firebase_key.json')  # <-- ИЗМЕНЕНО

db_fs = None

# --- ИНИЦИАЛИЗАЦИЯ FIREBASE ---
def init_firebase():
    global db_fs
    try:
        # Используем путь из переменных окружения
        cred = credentials.Certificate(FIREBASE_CRED_PATH)  # <-- ИЗМЕНЕНО
        firebase_admin.initialize_app(cred)
        db_fs = firestore.client()
        print("✅ Firebase успешно подключен")
    except Exception as e:
        print(f"❌ Ошибка Firebase: {e}")

# --- ОСТАЛЬНОЙ КОД БЕЗ ИЗМЕНЕНИЙ ---
# (все функции остаются такими же)

# --- ФОНОВОЕ СЛУШАНИЕ ИЗМЕНЕНИЙ (УВЕДОМЛЕНИЕ КЛИЕНТУ) ---
def setup_firebase_listener(loop, application):
    global db_fs
    if db_fs is None: return

    def on_snapshot(col_snapshot, changes, read_time):
        for change in changes:
            # Срабатывает, когда вы нажимаете "Отправить" в админке на сайте
            if change.type.name == 'MODIFIED':
                order_data = change.document.to_dict()
                status = order_data.get('status')
                order_id = order_data.get('order_id')
                client_id = order_data.get('user', {}).get('id')

                # Внутри функции on_snapshot, где проверяется статус:
                if client_id:
                    if status == 'Отправлен':
                        msg = f"📦 <b>Ваш заказ #{order_id} отправлен!</b>\nСкоро он будет у вас. Спасибо за покупку! ✨"
                    elif status == 'Доставлен':
                        msg = f"✅ <b>Ваш заказ #{order_id} доставлен!</b>\nНадеемся, вам всё понравилось. Будем рады вашему отзыву! ✨"
                    else:
                        return  # Если статус другой (например, "Новый"), ничего не отправляем

                    # Отправка сообщения
                    asyncio.run_coroutine_threadsafe(
                        application.bot.send_message(chat_id=client_id, text=msg, parse_mode='HTML'),
                        loop
                    )
                    print(f"📩 Уведомление ({status}) ушло клиенту {client_id}")

    db_fs.collection('orders').on_snapshot(on_snapshot)


# --- ОБРАБОТКА НОВОГО ЗАКАЗА ---
async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw_json = update.effective_message.web_app_data.data
        data = json.loads(raw_json)
        user_id = update.effective_user.id

        # Извлекаем все данные (с проверкой разных вариантов ключей)
        order_id = data.get('order_id', '???')
        name = data.get('customer_name') or data.get('name') or 'Не указано'
        phone = data.get('customer_phone') or data.get('phone') or 'Не указано'
        address = data.get('address') or data.get('customer_address') or 'Не указан'
        delivery = data.get('delivery') or data.get('delivery_type') or 'Не выбрана'
        total = data.get('order_total') or data.get('total') or 0

        # Получаем состав заказа (из текста или массива)
        items_list = data.get('items_text')
        if not items_list and 'items' in data:
            items = data.get('items', [])
            items_list = "\n".join(
                [f"▫️ {i.get('title')} ({i.get('size') or i.get('selSize') or '-'}) — {i.get('price')} ₽" for i in
                 items])

        if not items_list: items_list = "Состав не указан"

        # 1. Сохраняем в Firebase для админки
        if db_fs:
            order_entry = {
                **data,
                'status': 'Новый',
                'user': {'id': user_id},
                'createdAt': firestore.SERVER_TIMESTAMP
            }
            db_fs.collection("orders").add(order_entry)

        # 2. Формируем ПОЛНЫЕ ДАННЫЕ для сообщения админу
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

        # Отправляем сообщение админу БЕЗ КНОПОК
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_message,
            parse_mode='HTML',
            disable_web_page_preview=True
        )

        await update.message.reply_text(f"✅ Заказ #{order_id} принят!")

    except Exception as e:
        logging.error(f"Ошибка в web_app_data: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("Открыть Магазин", web_app=WebAppInfo(url=WEBAPP_URL))]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Добро пожаловать!", reply_markup=reply_markup)


if __name__ == '__main__':
    init_firebase()
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))

    loop = asyncio.get_event_loop()
    threading.Thread(target=setup_firebase_listener, args=(loop, application), daemon=True).start()

    print("🚀 Бот запущен...")
    application.run_polling()