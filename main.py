from os import environ
from os.path import exists

from pys3server import S3Server

from telegram_interface import TelegramInterface

if exists(".env"):
    from dotenv import load_dotenv
    load_dotenv()

app = S3Server(TelegramInterface(
    api_id=int(environ["API_ID"]),
    api_hash=environ["API_HASH"],
    bot_token=environ["BOT_TOKEN"],
    chat_id=environ["CHAT_ID"],
    mongo_url=environ["MONGODB"],
))


if __name__ == "__main__":
    from uvicorn import run
    run('main:app', host="0.0.0.0", port=8000, reload=True, use_colors=False)
