from pyrogram import Client, filters
from os import environ
from os.path import exists
if exists(".env"):
    from dotenv import load_dotenv
    load_dotenv()

bot = Client(
    "S3_Bot",
    api_id=int(environ.get("API_ID", 0)),
    api_hash=environ.get("API_HASH"),
    bot_token=environ.get("BOT_TOKEN")
)

@bot.on_message(filters.text)
async def get_id(_cl, message):
	if message.text.startswith("/id") or message.text.startswith("/channel"):
		await message.reply(str(message.chat.id))

if __name__ == "__main__":
    try:
        print('Press Ctrl+C to stop.')
        bot.run()
    except KeyboardInterrupt:
        exit()
        pass