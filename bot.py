import json
import logging
import mimetypes
import os
from pathlib import Path

from pydub import AudioSegment
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNELS_FILE = Path("channels.json")
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

fixed_audio_segments = {}
fixed_audio_labels = {}
fixed_audio_suffixes = {}
audio_batch = []
batch_message_id = None
batch_active = False

try:
    with CHANNELS_FILE.open("r", encoding="utf-8") as f:
        CHANNELS = json.load(f)
except FileNotFoundError:
    CHANNELS = {}


def save_channels():
    with CHANNELS_FILE.open("w", encoding="utf-8") as f:
        json.dump(CHANNELS, f, indent=2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Welcome to AUTO FILE MERGE Bot!\n\n"
        "Commands:\n"
        "• /setfixed slot_name label → set a fixed audio slot (fixed1–fixed4)\n"
        "• /batch → activate batch mode\n"
        "• /batch off → deactivate batch mode\n"
        "• /newchannel NAME CHANNEL_ID → add a new channel dynamically\n\n"
        "👉 Workflow:\n"
        "1. Use /setfixed to register intro/outro audios.\n"
        "2. Send the fixed audio, then send its suffix.\n"
        "3. Use /newchannel to add channels.\n"
        "4. Activate /batch and upload episodes.\n"
        "5. Choose fixed audio first, then choose channel.\n"
        "6. Bot merges and posts automatically."
    )
    await update.message.reply_text(msg)


async def new_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /newchannel NAME CHANNEL_ID")
        return

    name = context.args[0].upper()
    try:
        channel_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Channel ID must be a number.")
        return

    CHANNELS[name] = channel_id
    save_channels()
    await update.message.reply_text(f"✅ Channel {name} added with ID {channel_id}")


async def set_fixed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setfixed slot_name label")
        return

    slot = context.args[0].lower()
    label = " ".join(context.args[1:])

    if slot not in ["fixed1", "fixed2", "fixed3", "fixed4"]:
        await update.message.reply_text("❌ Invalid slot. Use fixed1–fixed4")
        return

    context.user_data["awaiting_fixed_audio"] = slot
    fixed_audio_labels[slot] = label
    await update.message.reply_text(f"📥 Send audio for {slot} (label: {label}).")


async def handle_suffix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "awaiting_fixed_suffix" not in context.user_data:
        return

    slot = context.user_data.pop("awaiting_fixed_suffix")
    fixed_audio_suffixes[slot] = update.message.text.strip() or "merged"
    await update.message.reply_text(f"✏️ Suffix for {slot} set to: {fixed_audio_suffixes[slot]}")


async def batch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global batch_active

    if context.args and context.args[0].lower() == "off":
        batch_active = False
        await update.message.reply_text("🚫 Batch mode deactivated.")
    else:
        batch_active = True
        await update.message.reply_text("🎲 Batch mode activated. Upload audios to build your batch.")


async def _download_audio(update: Update):
    file_obj = None
    file_name = None

    if update.message.audio:
        file_obj = await update.message.audio.get_file()
        file_name = update.message.audio.file_name or f"{file_obj.file_id}.mp3"
    elif update.message.document:
        file_obj = await update.message.document.get_file()
        file_name = update.message.document.file_name or f"{file_obj.file_id}"
        if update.message.document.mime_type:
            ext = mimetypes.guess_extension(update.message.document.mime_type)
            if ext and not file_name.endswith(ext):
                file_name += ext
    elif update.message.voice:
        file_obj = await update.message.voice.get_file()
        file_name = f"{file_obj.file_id}.ogg"

    if not file_obj or not file_name:
        return None

    path = DOWNLOAD_DIR / Path(file_name).name
    await file_obj.download_to_drive(path)
    return path


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global batch_message_id

    path = await _download_audio(update)
    if not path:
        return

    if "awaiting_fixed_audio" in context.user_data:
        slot = context.user_data.pop("awaiting_fixed_audio")
        fixed_audio_segments[slot] = AudioSegment.from_file(path)
        context.user_data["awaiting_fixed_suffix"] = slot
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to delete temporary fixed audio file")
        await update.message.reply_text(
            f"✅ Fixed audio {slot} updated (label: {fixed_audio_labels[slot]}). Now send the suffix text."
        )
        return

    if not batch_active:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to delete temporary audio file")
        await update.message.reply_text("ℹ️ Batch mode is not active. Use /batch to activate.")
        return

    audio_batch.append(str(path))

    keyboard = []
    for slot, label in fixed_audio_labels.items():
        keyboard.append([InlineKeyboardButton(f"{slot.upper()} - {label}", callback_data=f"fixed:{slot}")])
    keyboard.append([InlineKeyboardButton("❌ Clear Batch", callback_data="clear")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if batch_message_id:
        await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=batch_message_id,
            text="🎛 Batch mode: choose fixed audio:",
            reply_markup=reply_markup,
        )
    else:
        sent = await update.message.reply_text(
            "🎛 Batch mode: choose fixed audio:",
            reply_markup=reply_markup,
        )
        batch_message_id = sent.message_id


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global batch_message_id

    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "clear":
        for path in audio_batch:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to delete batch file")
        audio_batch.clear()
        batch_message_id = None
        await query.edit_message_text("🗑 Batch cleared!")
        return

    if data.startswith("fixed:"):
        slot = data.split(":", 1)[1]
        if slot not in fixed_audio_segments:
            await query.edit_message_text("❌ That fixed audio is not set yet.")
            return

        context.user_data["chosen_fixed"] = slot
        keyboard = []
        for name in CHANNELS.keys():
            keyboard.append([InlineKeyboardButton(name, callback_data=f"channel:{name}")])
        keyboard.append([InlineKeyboardButton("❌ Clear Batch", callback_data="clear")])

        await query.edit_message_text(
            "🎛 Now choose channel to send merged audios:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data.startswith("channel:"):
        channel_name = data.split(":", 1)[1]
        target_channel = CHANNELS[channel_name]
        slot = context.user_data.get("chosen_fixed")

        if not slot:
            await query.edit_message_text("❌ No fixed audio chosen.")
            return

        fixed_audio = fixed_audio_segments[slot]
        suffix = fixed_audio_suffixes.get(slot, "merged")
        await query.edit_message_text(
            f"⚙️ Processing batch with {fixed_audio_labels[slot]} → {channel_name} channel..."
        )

        for path in list(audio_batch):
            input_path = Path(path)
            incoming = AudioSegment.from_file(input_path)
            combined = fixed_audio + incoming
            output_path = input_path.with_name(f"{input_path.stem}_{suffix}.mp3")
            combined.export(output_path, format="mp3")

            with output_path.open("rb") as audio_file:
                await context.bot.send_audio(
                    chat_id=target_channel,
                    audio=audio_file,
                    caption=output_path.name,
                )

            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

        audio_batch.clear()
        batch_message_id = None
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ Completed: All audios merged with {fixed_audio_labels[slot]} and sent to {channel_name}!",
        )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newchannel", new_channel))
    app.add_handler(CommandHandler("setfixed", set_fixed))
    app.add_handler(CommandHandler("batch", batch_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_suffix))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.ALL | filters.VOICE, handle_audio))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
