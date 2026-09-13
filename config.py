import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Config:
    api_id: int = int(os.getenv("API_ID", "0"))
    api_hash: str = os.getenv("API_HASH", "")
    bot_token: str = os.getenv("BOT_TOKEN", "")
    user_session_string: str = os.getenv("USER_SESSION_STRING", "")
    user_session_name: str = os.getenv("USER_SESSION_NAME", "userbot_parser")
    db_path: str = os.getenv("DB_PATH", "global_users.db")
    workers_count: int = int(os.getenv("WORKERS_COUNT", "3"))
    welcome_gif: str = os.getenv(
        "WELCOME_GIF",
        "https://media.giphy.com/media/qgQUggAC3Pfv687qPC/giphy.gif",
    )
    welcome_file: str = os.getenv("WELCOME_FILE", "max.jpg")
    admin_ids: tuple = tuple(int(x) for x in os.getenv("ADMIN_IDS", "").replace(",", " ").split() if x.isdigit())

    def validate(self):
        if not self.api_id or not self.api_hash:
            raise RuntimeError("Заполни API_ID / API_HASH в .env (my.telegram.org)")
        if not self.bot_token:
            raise RuntimeError("Заполни BOT_TOKEN в .env (@BotFather)")

CFG = Config()
