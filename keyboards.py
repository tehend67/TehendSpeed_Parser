from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Запустить парсинг чатов", callback_data="parse")],
        [InlineKeyboardButton(text="⚙️ Настройки фильтров", callback_data="filters")],
        [InlineKeyboardButton(text="🗄 База данных и статистика", callback_data="db")],
        [InlineKeyboardButton(text="📈 Аналитика", callback_data="analytics")],
        [InlineKeyboardButton(text="💎 Создать своего бота или сайт? 🚀", callback_data="portfolio")],
    ])

def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="home")]
    ])

def filters_menu(only_phone: bool) -> InlineKeyboardMarkup:
    state = "ВКЛ 🟢" if only_phone else "ВЫКЛ 🔴"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📱 Только с номером телефона: {state}", callback_data="toggle_phone")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="home")],
    ])

def db_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📥 XLSX", callback_data="exp_xlsx"),
         InlineKeyboardButton(text="📥 CSV", callback_data="exp_csv")],
        [InlineKeyboardButton(text="📥 JSON", callback_data="exp_json"),
         InlineKeyboardButton(text="📥 TXT", callback_data="exp_txt")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="home")],
    ])

def portfolio_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Написать разработчику", url="https://t.me/codepatche")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="home")],
    ])
