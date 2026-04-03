"""
Единое место для callback_data inline-кнопок (главное меню, оплата, генерация).

Строки не дублируем в хендлерах — импортируем отсюда, чтобы не разъезжались фильтры и клавиатуры.
"""

# --- Главный экран / навигация (handlers/commands.py) ---
CB_MENU_REF = "ref_menu"
CB_MENU_REF_LEGACY = "menu:ref"
CB_MENU_PROFILE = "menu:profile"
CB_MENU_ABOUT = "menu:about"
CB_MENU_PAY = "menu:pay"
CB_MENU_SUPPORT = "menu:support"

# --- Генерация изображений (handlers/img_commands.py) ---
CB_CREATE_IMAGE = "menu:create_image"
CB_MENU_BACK_START = "menu:back_start"
CB_BACK_IMAGE_MODELS = "img:back_models"
CB_GEN_TEXT = "img:mode:text"
CB_GEN_EDIT = "img:mode:edit"
CB_PICK_NANO = "img:pick_nano"
CB_PICK_NANO_2 = "img:pick_nano2"
CB_PICK_QWEN = "img:pick_qwen"
CB_PICK_FLUX = "img:pick_flux"
CB_READY_IDEAS = "menu:ready_ideas"
CB_APPLY_READY_PREFIX = "img:idea:"
CB_REGEN = "img:regen"
CB_IMG_CANCEL = "img:cancel"

# --- Оплата (handlers/payments.py) ---
CB_PAY_MENU = "pay:menu"
CB_PAY_BONUS_MENU = "pay:bonus_menu"
CB_PAY_PLAN_PREFIX = "pay:p:"
CB_PAY_PACK_PREFIX = "pay:b:"
CB_PAY_STARS_PREFIX = "pay:s:"
CB_PAY_RUB_PREFIX = "pay:r:"
CB_PAY_INTL_PREFIX = "pay:i:"
CB_PAY_CRYPTO_PREFIX = "pay:c:"

__all__ = [
    "CB_APPLY_READY_PREFIX",
    "CB_BACK_IMAGE_MODELS",
    "CB_CREATE_IMAGE",
    "CB_GEN_EDIT",
    "CB_GEN_TEXT",
    "CB_IMG_CANCEL",
    "CB_MENU_ABOUT",
    "CB_MENU_BACK_START",
    "CB_MENU_PAY",
    "CB_MENU_PROFILE",
    "CB_MENU_REF",
    "CB_MENU_REF_LEGACY",
    "CB_MENU_SUPPORT",
    "CB_PAY_BONUS_MENU",
    "CB_PAY_CRYPTO_PREFIX",
    "CB_PAY_INTL_PREFIX",
    "CB_PAY_MENU",
    "CB_PAY_PACK_PREFIX",
    "CB_PAY_PLAN_PREFIX",
    "CB_PAY_RUB_PREFIX",
    "CB_PAY_STARS_PREFIX",
    "CB_PICK_NANO",
    "CB_PICK_NANO_2",
    "CB_PICK_QWEN",
    "CB_PICK_FLUX",
    "CB_READY_IDEAS",
    "CB_REGEN",
]
