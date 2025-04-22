import os
import logging
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pytz
import json
import time
from functools import lru_cache

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants for column indices
NAME = 2
COLUMN_PHONE = 3
COLUMN_DATE = 4
COLUMN_TIME = 5
COLUMN_LOCATION = 7
COLUMN_HR_CONTACT = 8
COLUMN_DAY_BEFORE = 9
COLUMN_TODAY = 11
COLUMN_HOUR_BEFORE = 10
COLUMN_CHAT_ID = 15
COLUMN_SENT_REMINDERS = 16  # New column for tracking sent reminders
COLUMN_INTERVIEW_RESULT = 12  # Column for interview result


# Check environment variables
CREDENTIALS_FILE = os.getenv('GOOGLE_SHEETS_CREDENTIALS_FILE')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not CREDENTIALS_FILE or not SPREADSHEET_ID:
    logger.critical("GOOGLE_SHEETS_CREDENTIALS_FILE or SPREADSHEET_ID is not set!")
    exit(1)

if not TELEGRAM_TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN is not set!")
    exit(1)

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Initialize Google Sheets client
try:
    credentials = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
    client = gspread.authorize(credentials)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    logger.info("Successfully connected to Google Sheets")
except Exception as e:
    logger.critical(f"Failed to initialize Google Sheets client: {str(e)}", exc_info=True)
    exit(1)

# Set timezone
TZ = pytz.timezone('Asia/Tashkent')

# Добавим кэширование для уменьшения количества запросов
@lru_cache(maxsize=100)
def get_cached_phone_numbers():
    """Cache phone numbers to reduce API calls."""
    time.sleep(1)  # Добавляем задержку в 1 секунду
    return sheet.col_values(COLUMN_PHONE)

def normalize_phone(phone):
    """Normalize phone number by removing all non-digit characters."""
    if not phone:
        return ""
    return re.sub(r'\D', '', phone)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    keyboard = [[KeyboardButton("📲 Отправить контакт", request_contact=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    # Отправляем инструкцию с эмодзи
    instruction = """Добро пожаловать! 👋

Чтобы подтвердить собеседование:

1️⃣ Найдите кнопку "📲 Отправить контакт" внизу экрана
2️⃣ Нажмите на нее
3️⃣ Подтвердите отправку контакта

⬇️ Кнопка находится здесь ⬇️"""
    
    await update.message.reply_text(
        instruction,
        reply_markup=reply_markup
    )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the shared contact information."""
    try:
        logger.info("=== Starting handle_contact function ===")
        
        if not update.message.contact:
            logger.warning("User pressed share button but didn't send contact")
            await update.message.reply_text(
                "Пожалуйста, нажмите кнопку и отправьте свой номер телефона.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
            
        contact = update.message.contact
        phone = contact.phone_number
        chat_id = update.effective_chat.id
        logger.info(f"Received contact from chat_id: {chat_id}")
        
        normalized_phone = normalize_phone(phone)
        logger.info(f"Received phone: {phone}, normalized to: {normalized_phone}")
        
        try:
            # Используем кэшированные номера телефонов
            phone_column = get_cached_phone_numbers()
            logger.info(f"Found {len(phone_column)} phone numbers in sheet")
            
            row = None
            for i, sheet_phone in enumerate(phone_column, 1):
                if sheet_phone and normalize_phone(sheet_phone) == normalized_phone:
                    row = i
                    logger.info(f"Found matching phone in row {i}")
                    break
            
            if not row:
                logger.error(f"Phone number {normalized_phone} not found in sheet")
                raise ValueError("Phone number not found")
            
            try:
                time.sleep(1)  # Добавляем задержку перед обновлением
                sheet.update_cell(row, COLUMN_CHAT_ID, str(chat_id))
                logger.info(f"Successfully updated chat_id to {chat_id}")
            except Exception as e:
                logger.error(f"Failed to update chat_id: {str(e)}", exc_info=True)
                raise
            
            # Get interview details
            name = sheet.cell(row, NAME).value
            date_str = sheet.cell(row, COLUMN_DATE).value
            time_str = sheet.cell(row, COLUMN_TIME).value
            location = sheet.cell(row, COLUMN_LOCATION).value
            hr_contact = sheet.cell(row, COLUMN_HR_CONTACT).value
            
            if not date_str or not time_str:
                logger.error(f"Missing date or time for row {row}")
                await update.message.reply_text(
                    "Информация о дате или времени собеседования отсутствует. Свяжитесь с HR.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            # Parse interview datetime
            try:
                interview_date = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                interview_date = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            
            interview_date = TZ.localize(interview_date)
            
            # Calculate reminder times
            day_before = interview_date - timedelta(days=1)
            hour_before = interview_date - timedelta(hours=1)
            today = interview_date
            after_interview = interview_date + timedelta(hours=2)  # New reminder 2 hours after interview
            
            # Create sent_reminders object with scheduled times
            sent_reminders = {
                'day_before': day_before.strftime("%Y-%m-%d %H:%M:%S"),
                'hour_before': hour_before.strftime("%Y-%m-%d %H:%M:%S"),
                'today': today.strftime("%Y-%m-%d %H:%M:%S"),
                'after_interview': after_interview.strftime("%Y-%m-%d %H:%M:%S")  # New reminder time
            }
            
            # Update sent_reminders in sheet
            sheet.update_cell(row, COLUMN_SENT_REMINDERS, json.dumps(sent_reminders))
            logger.info(f"Updated sent_reminders with scheduled times: {sent_reminders}")
            
            # Format the message with name if available
            greeting = f"Здравствуйте, {name}!\n\n" if name else "Здравствуйте!\n\n"
            message = (
                f"{greeting}"
                "Вы записаны на собеседование\n\n"
                f"📅 Дата: {date_str}\n"
                f"⏰ Время: {time_str}\n"
                f"📍 Место/Ссылка: {location}\n"
                f"📞 Контакт HR: {hr_contact}\n\n"
                "Вам придут напоминания:\n"
                f"- За день до собеседования ({day_before.strftime('%Y-%m-%d %H:%M')})\n"
                f"- За час до собеседования ({hour_before.strftime('%Y-%m-%d %H:%M')})\n"
                f"- В день собеседования ({today.strftime('%Y-%m-%d %H:%M')})\n"
            )
            
            await update.message.reply_text(message, reply_markup=ReplyKeyboardRemove())
            logger.info("Initial message with reminder times sent successfully")
            
        except ValueError as e:
            if str(e) == "Phone number not found":
                logger.error(f"Phone number {normalized_phone} not found in sheet")
                await update.message.reply_text(
                    "Ваш номер телефона не найден в базе данных. Пожалуйста, свяжитесь с HR.",
                    reply_markup=ReplyKeyboardRemove()
                )
            else:
                logger.error(f"Error: {str(e)}", exc_info=True)
                await update.message.reply_text(
                    "Произошла ошибка при поиске ваших данных. Пожалуйста, свяжитесь с HR.",
                    reply_markup=ReplyKeyboardRemove()
                )
    except Exception as e:
        logger.error(f"Unexpected error in handle_contact: {str(e)}", exc_info=True)
        await update.message.reply_text(
            "Произошла неожиданная ошибка. Пожалуйста, свяжитесь с HR.",
            reply_markup=ReplyKeyboardRemove()
        )

async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check the sheet for reminders that need to be sent."""
    try:
        current_time = datetime.now(TZ)
        logger.info(f"Checking reminders at: {current_time}")
        
        # Get all rows with chat_ids
        chat_ids = sheet.col_values(COLUMN_CHAT_ID)
        
        for row, chat_id in enumerate(chat_ids, 1):
            if not chat_id or chat_id == "chat_id":  # Skip empty cells and header
                continue
                
            try:
                # Get reminder times from column N (COLUMN_SENT_REMINDERS)
                reminders_str = sheet.cell(row, COLUMN_SENT_REMINDERS).value
                if not reminders_str:
                    continue
                    
                reminders = json.loads(reminders_str)
                location = sheet.cell(row, COLUMN_LOCATION).value
                
                # Check each reminder type
                for reminder_type in ['day_before', 'hour_before', 'today', 'after_interview']:
                    scheduled_time_str = reminders.get(reminder_type)
                    if not scheduled_time_str:  # Skip if this reminder is already sent
                        continue
                        
                    # Parse scheduled time
                    scheduled_time = datetime.strptime(scheduled_time_str, "%Y-%m-%d %H:%M:%S")
                    scheduled_time = TZ.localize(scheduled_time)
                    
                    # Calculate time difference in seconds
                    time_diff = abs((current_time - scheduled_time).total_seconds())
                    
                    # Get the corresponding reminder column
                    if reminder_type == 'day_before':
                        reminder_column = COLUMN_DAY_BEFORE
                    elif reminder_type == 'hour_before':
                        reminder_column = COLUMN_HOUR_BEFORE
                    elif reminder_type == 'today':
                        reminder_column = COLUMN_TODAY
                    else:  # after_interview
                        reminder_column = COLUMN_INTERVIEW_RESULT
                    
                    # If within 2 minutes of scheduled time
                    if time_diff <= 120:  # 120 seconds = 2 minutes
                        logger.info(f"Sending {reminder_type} reminder to chat {chat_id}")
                        await send_reminder(context, int(chat_id), location, reminder_type, row)
                        
                        # Mark as sent in the reminder column if not after_interview
                        if reminder_type != 'after_interview':
                            sheet.update_cell(row, reminder_column, "Отправлено")
                        
                        # Remove this reminder from JSON by setting it to null
                        reminders[reminder_type] = None
                        sheet.update_cell(row, COLUMN_SENT_REMINDERS, json.dumps(reminders))
                        
                        logger.info(f"Updated reminder status and removed date for {reminder_type} in row {row}")
                        break  # Only send one reminder at a time
                            
            except Exception as e:
                logger.error(f"Error processing row {row}: {str(e)}", exc_info=True)
                continue
                
    except Exception as e:
        logger.error(f"Error in check_reminders: {str(e)}", exc_info=True)

async def send_reminder(context: ContextTypes.DEFAULT_TYPE, chat_id: int, location: str, 
                       reminder_type: str, row: int) -> None:
    """Send reminder message and ask for confirmation."""
    try:
        logger.info(f"Sending {reminder_type} reminder to chat {chat_id}")
        
        # Get interview details
        date_str = sheet.cell(row, COLUMN_DATE).value
        time_str = sheet.cell(row, COLUMN_TIME).value
        
        if reminder_type == 'after_interview':
            message = (
                "Как прошло собеседование? Вы принимаете предложение?"
            )
            keyboard = [
                [
                    InlineKeyboardButton("Да ✅", callback_data=f"result_yes"),
                    InlineKeyboardButton("Нет ❌", callback_data=f"result_no"),
                    InlineKeyboardButton("Думаю 🤔", callback_data=f"result_thinking")
                ]
            ]
        else:
            is_link = 'http' in location.lower()
            if is_link:
                message = (
                    "Добрый день! Напоминаем о предстоящем собеседовании.\n\n"
                    f"📅 Дата: {date_str}\n"
                    f"⏰ Время: {time_str}\n"
                    f"🔗 Ссылка: {location}\n\n"
                    "Планируете ли вы участвовать в собеседовании?"
                )
            else:
                message = (
                    "Добрый день! Напоминаем о предстоящем собеседовании.\n\n"
                    f"📅 Дата: {date_str}\n"
                    f"⏰ Время: {time_str}\n"
                    f"📍 Адрес: {location}\n\n"
                    "Будете ли вы присутствовать на собеседовании?"
                )
            keyboard = [
                [
                    InlineKeyboardButton("Да", callback_data=f"confirm_yes_{reminder_type}"),
                    InlineKeyboardButton("Нет", callback_data=f"confirm_no_{reminder_type}")
                ]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        logger.info(f"Sending message to chat {chat_id}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            reply_markup=reply_markup
        )
        logger.info("Message sent successfully")
    except Exception as e:
        logger.error(f"Error in send_reminder: {str(e)}", exc_info=True)
        raise

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    
    try:
        # Parse callback data
        callback_data = query.data
        
        if callback_data.startswith('result_'):
            # Handle interview result
            result = callback_data.split('_')[1]
            result_text = {
                'yes': 'Принял предложение ✅',
                'no': 'Отказался ❌',
                'thinking': 'Думает 🤔'
            }.get(result, 'Неизвестно')
            
            # Find the user's row
            cell = sheet.find(str(update.effective_chat.id), in_column=COLUMN_CHAT_ID)
            if not cell:
                raise ValueError(f"Chat ID {update.effective_chat.id} not found in sheet")
            
            row = cell.row
            
            # Update the result in column 12
            sheet.update_cell(row, COLUMN_INTERVIEW_RESULT, result_text)
            
            # Send confirmation message
            if result == 'yes':
                message = "Спасибо за ваш ответ! Мы рады, что вы приняли предложение."
            elif result == 'no':
                message = "Спасибо за ваш ответ. Жаль, что вы отказались."
            else:
                message = "Спасибо за ваш ответ. Пожалуйста, сообщите о вашем решении HR."
        else:
            # Handle regular confirmation
            parts = callback_data.split('_')
            if len(parts) < 3:
                raise ValueError(f"Invalid callback data format: {callback_data}")
                
            response = parts[1]  # "yes" or "no"
            reminder_type = '_'.join(parts[2:])  # "day_before", "hour_before", or "today"
            
            # Find the user's row
            cell = sheet.find(str(update.effective_chat.id), in_column=COLUMN_CHAT_ID)
            if not cell:
                raise ValueError(f"Chat ID {update.effective_chat.id} not found in sheet")
            
            row = cell.row
            
            # Determine which column to update based on reminder type
            if reminder_type == 'day_before':
                column = COLUMN_DAY_BEFORE
            elif reminder_type == 'hour_before':
                column = COLUMN_HOUR_BEFORE
            else:  # today
                column = COLUMN_TODAY
                
            # Update the response
            response_text = "Да" if response == "yes" else "Нет"
            sheet.update_cell(row, column, response_text)
            
            # Send confirmation message
            if response == "yes":
                message = "Спасибо за ваш ответ! Ждем вас на собеседовании."
            else:
                message = "Спасибо за ваш ответ. Пожалуйста, свяжитесь с HR для переноса собеседования."
        
        await query.edit_message_text(text=message)
        
    except Exception as e:
        logger.error(f"Error in button_callback: {str(e)}", exc_info=True)
        await query.edit_message_text(
            text="Произошла ошибка при обновлении данных. Пожалуйста, свяжитесь с HR."
        )

def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(CallbackQueryHandler(button_callback))

    # Initialize job queue
    job_queue = application.job_queue
    if job_queue is None:
        logger.error("Job queue is not initialized!")
        return

    # Schedule reminder checks every 15 seconds
    job_queue.run_repeating(check_reminders, interval=15, first=0)

    # Start the Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 