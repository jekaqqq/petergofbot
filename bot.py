# bot.py
import os
import logging
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from database import get_connection, init_db

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# STATES
SHOP_CATEGORY, SHOP_BRAND, SHOP_VARIANT = range(3)
ADMIN_MENU = 100
ADD_BRAND_CAT, ADD_BRAND_INPUT, ADD_BRAND_CONFIRM = range(101, 104)
ADD_VAR_CAT, ADD_VAR_BRAND, ADD_VAR_OPTION, ADD_VAR_PRICE, ADD_VAR_STOCK, ADD_VAR_PHOTO = range(104, 110)
DEL_ACTION, DEL_CAT_SELECT, DEL_BRAND_SELECT, DEL_VAR_SELECT, DEL_CONFIRM = range(110, 115)

# ---------------- helpers ----------------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        uid = user.id if user else None
        if uid not in ADMIN_USER_IDS:
            # корректно ответим в зависимости от типа апдейта
            if update.callback_query:
                await update.callback_query.answer("❌ У вас нет доступа.", show_alert=True)
            elif update.message:
                await update.message.reply_text("❌ У вас нет доступа.")
            return ConversationHandler.END
        return await func(update, context)
    return wrapper

async def send_or_edit(update: Update, text: str, reply_markup=None, parse_mode=None):
    """
    Безопасно отправляет или редактирует сообщение в зависимости от того,
    пришёл ли апдейт как Message или CallbackQuery.
    """
    if update.callback_query:
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            pass
        # edit message if possible
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except Exception:
            # если редактирование не удалось (например, потому что сообщение уже другое),
            # попробуем отправить новое сообщение в чат
            chat_id = query.message.chat_id
            await query.message.bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
    elif update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    else:
        # безопасный fallback
        logger.warning("send_or_edit: ни callback_query, ни message отсутствуют в update")

# ---------------- Магазин ----------------
async def start_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_connection()
    cats = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
    conn.close()

    keyboard = [[InlineKeyboardButton(c['name'], callback_data=f"cat_{c['id']}")] for c in cats]
    reply = InlineKeyboardMarkup(keyboard)
    await send_or_edit(update, "🛍 Выберите категорию:", reply_markup=reply)
    return SHOP_CATEGORY

async def shop_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # обработаем разные форматы callback: cat_{id} или back_cat_{id}
    if update.callback_query:
        data = update.callback_query.data
    else:
        # если вызван как message (маловероятно), прервём
        await send_or_edit(update, "Ошибка: неверный формат вызова.")
        return ConversationHandler.END

    if data.startswith("cat_"):
        cat_id = int(data.split("_", 1)[1])
    elif data.startswith("back_cat_"):
        cat_id = int(data.split("_", 2)[2])
    else:
        await update.callback_query.answer()
        return ConversationHandler.END

    conn = get_connection()
    category = conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
    products = conn.execute("""
        SELECT p.id, p.brand, COALESCE(SUM(v.stock),0) as total_stock
        FROM products p
        LEFT JOIN variants v ON v.product_id = p.id
        WHERE p.category_id = ?
        GROUP BY p.id
        HAVING total_stock > 0
        ORDER BY p.brand
    """, (cat_id,)).fetchall()
    conn.close()

    if not products:
        kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_categories")]]
        await send_or_edit(update, "❌ Нет товаров в наличии.", reply_markup=InlineKeyboardMarkup(kb))
        return SHOP_CATEGORY

    keyboard = [[InlineKeyboardButton(f"{p['brand']} ({p['total_stock']} шт)", callback_data=f"brand_{p['id']}")] for p in products]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_categories")])
    await send_or_edit(update, f"📦 Товары в категории «{category['name']}»:",
                       reply_markup=InlineKeyboardMarkup(keyboard))
    return SHOP_BRAND

async def shop_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith("brand_"):
        prod_id = int(data.split("_", 1)[1])
    elif data.startswith("back_brand_"):
        prod_id = int(data.split("_", 2)[2])
    else:
        await update.callback_query.answer()
        return ConversationHandler.END

    conn = get_connection()
    product = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    if not product:
        conn.close()
        await update.callback_query.edit_message_text("❌ Продукт не найден.")
        return SHOP_CATEGORY
    category = conn.execute("SELECT * FROM categories WHERE id=?", (product['category_id'],)).fetchone()
    variants = conn.execute("SELECT * FROM variants WHERE product_id=? AND stock>0 ORDER BY option", (prod_id,)).fetchall()
    conn.close()

    if not variants:
        kb = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"back_cat_{product['category_id']}")]]
        await update.callback_query.edit_message_text("❌ Нет доступных вариантов.", reply_markup=InlineKeyboardMarkup(kb))
        return SHOP_BRAND

    option_label = "Цвет" if category["option_type"] == "color" else "Крепость"
    keyboard = [[InlineKeyboardButton(f"{v['option']} — {int(v['price'])}₽ ({v['stock']} шт)", callback_data=f"var_{v['id']}")] for v in variants]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"back_cat_{product['category_id']}")])
    await update.callback_query.edit_message_text(f"🔹 {product['brand']} — {option_label}:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SHOP_VARIANT

async def shop_variant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    var_id = int(query.data.split("_")[1])

    conn = get_connection()
    variant = conn.execute("""
        SELECT v.*, p.brand, p.category_id 
        FROM variants v 
        JOIN products p ON v.product_id = p.id 
        WHERE v.id=?
    """, (var_id,)).fetchone()
    conn.close()

    option_label = "Цвет" if context.user_data.get("option_type") == "color" else "Крепость"

    caption = (
        f"📦 {variant['brand']}\n"
        f"🔹 {option_label}: {variant['option']}\n"
        f"💰 Цена: {variant['price']}₽\n"
        f"📦 В наличии: {variant['stock']}"
    )

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"back_brand_{variant['product_id']}")]]

    if variant["image_id"]:
        try:
            await query.edit_message_media(
                InputMediaPhoto(media=variant["image_id"], caption=caption),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except:
            await query.edit_message_caption(caption, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(keyboard))

    return SHOP_VARIANTS


# ---------------- Админка ----------------
@admin_only
async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("➕ Добавить марку", callback_data="admin_add_brand")],
        [InlineKeyboardButton("➕ Добавить товар", callback_data="admin_add_variant")],
        [InlineKeyboardButton("🗑️ Удалить", callback_data="admin_delete")],
        [InlineKeyboardButton("⬅️ В магазин", callback_data="back_to_shop")]
    ]
    await send_or_edit(update, "🛠️ Панель администратора:", reply_markup=InlineKeyboardMarkup(kb))
    return ADMIN_MENU

# --- Добавление марки ---
@admin_only
async def admin_add_brand_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # показать категории
    conn = get_connection()
    cats = conn.execute("SELECT id, name FROM categories ORDER BY id").fetchall()
    conn.close()
    keyboard = [[InlineKeyboardButton(c['name'], callback_data=f"admin_addbrand_cat_{c['id']}")] for c in cats]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_menu")])
    await update.callback_query.edit_message_text("Выберите категорию для новой марки:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_BRAND_CAT

@admin_only
async def admin_add_brand_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cat_id = int(update.callback_query.data.split("_")[-1])
    context.user_data['admin_new_brand_cat_id'] = cat_id
    # попросим ввести название марки
    await update.callback_query.message.reply_text("Введите название марки (текст):")
    return ADD_BRAND_INPUT

async def admin_add_brand_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    brand = update.message.text.strip()
    if not brand:
        await update.message.reply_text("Название не может быть пустым. Попробуйте снова:")
        return ADD_BRAND_INPUT
    context.user_data['admin_new_brand_name'] = brand
    # подтвердим через кнопки
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="admin_addbrand_confirm_yes")],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_addbrand_confirm_no")]
    ]
    await update.message.reply_text(f"Подтвердите добавление марки:\n\n{brand}", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_BRAND_CONFIRM

@admin_only
async def admin_add_brand_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.callback_query.data.endswith("_no"):
        await update.callback_query.edit_message_text("Добавление марки отменено.")
        return await admin_start(update, context)
    # yes
    brand = context.user_data.get('admin_new_brand_name')
    cat_id = context.user_data.get('admin_new_brand_cat_id')
    if not brand or not cat_id:
        await update.callback_query.edit_message_text("Ошибка: отсутствуют данные.")
        return await admin_start(update, context)
    conn = get_connection()
    try:
        conn.execute("INSERT INTO products (brand, category_id) VALUES (?, ?)", (brand, cat_id))
        conn.commit()
        await update.callback_query.edit_message_text(f"✅ Марка '{brand}' добавлена.")
    except Exception as e:
        logger.exception("Ошибка при добавлении марки")
        await update.callback_query.edit_message_text("❌ Ошибка при добавлении марки (возможно уже существует).")
    finally:
        conn.close()
    return await admin_start(update, context)

# --- Добавление варианта ---
@admin_only
async def admin_add_variant_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_connection()
    cats = conn.execute("SELECT id, name FROM categories ORDER BY id").fetchall()
    conn.close()
    keyboard = [[InlineKeyboardButton(c['name'], callback_data=f"admin_addvar_cat_{c['id']}")] for c in cats]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_menu")])
    await update.callback_query.edit_message_text("Выберите категорию для товара:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_VAR_CAT

@admin_only
async def admin_addvar_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cat_id = int(update.callback_query.data.split("_")[-1])
    context.user_data['admin_var_cat_id'] = cat_id
    conn = get_connection()
    brands = conn.execute("SELECT id, brand FROM products WHERE category_id=?", (cat_id,)).fetchall()
    conn.close()
    if not brands:
        await update.callback_query.edit_message_text("Нет марок в этой категории. Сначала добавьте марку.")
        return await admin_start(update, context)
    kb = [[InlineKeyboardButton(b['brand'], callback_data=f"admin_addvar_brand_{b['id']}")] for b in brands]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_menu")])
    await update.callback_query.edit_message_text("Выберите марку:", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_VAR_BRAND

@admin_only
async def admin_addvar_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    prod_id = int(update.callback_query.data.split("_")[-1])
    context.user_data['admin_var_prod_id'] = prod_id
    await update.callback_query.message.reply_text("Введите вариант (цвет/крепость):")
    return ADD_VAR_OPTION

async def admin_addvar_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['admin_var_option'] = update.message.text.strip()
    await update.message.reply_text("Введите цену (число):")
    return ADD_VAR_PRICE

async def admin_addvar_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['admin_var_price'] = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Неверная цена. Введите число:")
        return ADD_VAR_PRICE
    await update.message.reply_text("Введите количество (целое число):")
    return ADD_VAR_STOCK

async def admin_addvar_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['admin_var_stock'] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Неверное количество. Введите целое число:")
        return ADD_VAR_STOCK
    await update.message.reply_text("Отправьте фото (лучше) или ссылку на изображение, либо '-' для пропуска:")
    return ADD_VAR_PHOTO

async def admin_addvar_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_val = None
    if update.message.photo:
        photo_val = update.message.photo[-1].file_id
    elif update.message.text and update.message.text.strip() == "-":
        photo_val = None
    elif update.message.text and update.message.text.strip().startswith("http"):
        photo_val = update.message.text.strip()
    else:
        await update.message.reply_text("Неверный формат. Отправьте фото, URL или '-'")
        return ADD_VAR_PHOTO

    conn = get_connection()
    try:
        # если продукт не существует — создадим (на случай)
        brand_prod_id = context.user_data.get('admin_var_prod_id')
        if not brand_prod_id:
            await update.message.reply_text("Ошибка: не выбран продукт.")
            return await admin_start(update, context)

        conn.execute("""
            INSERT INTO variants (product_id, option, price, stock, image_id)
            VALUES (?, ?, ?, ?, ?)
        """, (
            brand_prod_id,
            context.user_data.get('admin_var_option'),
            context.user_data.get('admin_var_price'),
            context.user_data.get('admin_var_stock'),
            photo_val
        ))
        conn.commit()
        await update.message.reply_text("✅ Вариант добавлен.")
    except Exception:
        logger.exception("Ошибка при добавлении варианта")
        await update.message.reply_text("❌ Ошибка при добавлении варианта.")
    finally:
        conn.close()
    return await admin_start(update, context)

# --- Удаление ---
@admin_only
async def admin_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Марку", callback_data="admin_del_brand")],
        [InlineKeyboardButton("Товар", callback_data="admin_del_variant")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_menu")]
    ]
    await update.callback_query.edit_message_text("Что хотите удалить?", reply_markup=InlineKeyboardMarkup(kb))
    return DEL_ACTION

@admin_only
async def admin_del_brand_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_connection()
    cats = conn.execute("SELECT id, name FROM categories ORDER BY id").fetchall()
    conn.close()
    kb = [[InlineKeyboardButton(c['name'], callback_data=f"admin_delbrand_cat_{c['id']}")] for c in cats]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_menu")])
    await update.callback_query.edit_message_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(kb))
    return DEL_CAT_SELECT

@admin_only
async def admin_delbrand_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cat_id = int(update.callback_query.data.split("_")[-1])
    conn = get_connection()
    brands = conn.execute("""
        SELECT p.id, p.brand, COUNT(v.id) as cnt
        FROM products p
        LEFT JOIN variants v ON v.product_id = p.id
        WHERE p.category_id = ?
        GROUP BY p.id
        ORDER BY p.brand
    """, (cat_id,)).fetchall()
    conn.close()
    if not brands:
        await update.callback_query.edit_message_text("Нет марок в этой категории.")
        return await admin_start(update, context)
    kb = []
    for b in brands:
        warn = " ⚠️" if b['cnt'] > 0 else ""
        kb.append([InlineKeyboardButton(f"{b['brand']}{warn}", callback_data=f"admin_delbrand_confirm_{b['id']}")])
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_menu")])
    await update.callback_query.edit_message_text("Выберите марку для удаления:", reply_markup=InlineKeyboardMarkup(kb))
    return DEL_BRAND_SELECT

@admin_only
async def admin_delbrand_confirm_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    prod_id = int(update.callback_query.data.split("_")[-1])
    conn = get_connection()
    cnt = conn.execute("SELECT COUNT(*) as cnt FROM variants WHERE product_id = ?", (prod_id,)).fetchone()['cnt']
    brand = conn.execute("SELECT brand FROM products WHERE id = ?", (prod_id,)).fetchone()
    conn.close()
    brand_name = brand['brand'] if brand else 'Неизвестно'
    # попросим подтверждение (особенно если есть варианты)
    kb = [
        [InlineKeyboardButton("✅ Удалить", callback_data=f"admin_delbrand_final_yes_{prod_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_back_menu")]
    ]
    msg = f"Вы уверены, что хотите удалить марку '{brand_name}'?"
    if cnt > 0:
        msg += f"\n\n⚠️ У этой марки {cnt} вариантов — они тоже будут удалены (ON DELETE CASCADE)."
    await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
    return DEL_CONFIRM

@admin_only
async def admin_delbrand_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    if data.startswith("admin_delbrand_final_yes_"):
        prod_id = int(data.split("_")[-1])
        conn = get_connection()
        try:
            conn.execute("DELETE FROM products WHERE id = ?", (prod_id,))
            conn.commit()
            await update.callback_query.edit_message_text("✅ Марка удалена.")
        except Exception:
            logger.exception("Ошибка при удалении марки")
            await update.callback_query.edit_message_text("❌ Ошибка при удалении марки.")
        finally:
            conn.close()
    else:
        await update.callback_query.edit_message_text("Отменено.")
    return await admin_start(update, context)

# --- Удаление варианта ---
@admin_only
async def admin_del_variant_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_connection()
    cats = conn.execute("SELECT id, name FROM categories ORDER BY id").fetchall()
    conn.close()
    kb = [[InlineKeyboardButton(c['name'], callback_data=f"admin_delvar_cat_{c['id']}")] for c in cats]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_menu")])
    await update.callback_query.edit_message_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(kb))
    return DEL_CAT_SELECT

@admin_only
async def admin_delvar_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cat_id = int(update.callback_query.data.split("_")[-1])
    conn = get_connection()
    brands = conn.execute("SELECT id, brand FROM products WHERE category_id = ? ORDER BY brand", (cat_id,)).fetchall()
    conn.close()
    if not brands:
        await update.callback_query.edit_message_text("Нет марок.")
        return await admin_start(update, context)
    kb = [[InlineKeyboardButton(b['brand'], callback_data=f"admin_delvar_brand_{b['id']}")] for b in brands]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_menu")])
    await update.callback_query.edit_message_text("Выберите марку:", reply_markup=InlineKeyboardMarkup(kb))
    return DEL_BRAND_SELECT

@admin_only
async def admin_delvar_variants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    prod_id = int(update.callback_query.data.split("_")[-1])
    conn = get_connection()
    variants = conn.execute("SELECT id, option, price, stock FROM variants WHERE product_id = ?", (prod_id,)).fetchall()
    conn.close()
    if not variants:
        await update.callback_query.edit_message_text("Нет вариантов у этой марки.")
        return await admin_start(update, context)
    kb = [[InlineKeyboardButton(f"{v['option']} — {int(v['price'])}₽ ({v['stock']}шт)", callback_data=f"admin_delvar_confirm_{v['id']}")] for v in variants]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_menu")])
    await update.callback_query.edit_message_text("Выберите вариант для удаления:", reply_markup=InlineKeyboardMarkup(kb))
    return DEL_VAR_SELECT

@admin_only
async def admin_delvar_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    var_id = int(update.callback_query.data.split("_")[-1])
    conn = get_connection()
    try:
        opt = conn.execute("SELECT option FROM variants WHERE id=?", (var_id,)).fetchone()
        conn.execute("DELETE FROM variants WHERE id = ?", (var_id,))
        conn.commit()
        name = opt['option'] if opt else "вариант"
        await update.callback_query.edit_message_text(f"✅ Вариант '{name}' удалён.")
    except Exception:
        logger.exception("Ошибка при удалении варианта")
        await update.callback_query.edit_message_text("❌ Ошибка при удалении.")
    finally:
        conn.close()
    return await admin_start(update, context)

# --- Возвраты ---
@admin_only
async def admin_back_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await admin_start(update, context)

async def back_to_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await start_shop(update, context)

# ---------------- MAIN ----------------
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    shop_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start_shop)],
        states={
            SHOP_CATEGORY: [CallbackQueryHandler(shop_category, pattern=r"^cat_|^back_cat_")],
            SHOP_BRAND: [
                CallbackQueryHandler(shop_brand, pattern=r"^brand_|^back_brand_"),
                CallbackQueryHandler(start_shop, pattern=r"^back_categories$")
            ],
            SHOP_VARIANT: [
                CallbackQueryHandler(shop_variant, pattern=r"^var_"),
                CallbackQueryHandler(shop_category, pattern=r"^back_cat_"),
                CallbackQueryHandler(shop_brand, pattern=r"^back_brand_")
            ],
        },
        fallbacks=[CommandHandler("start", start_shop)],
        allow_reentry=True,
        per_chat=True
    )

    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_start)],
        states={
            ADMIN_MENU: [
                CallbackQueryHandler(admin_add_brand_start, pattern=r"^admin_add_brand$"),
                CallbackQueryHandler(admin_add_variant_start, pattern=r"^admin_add_variant$"),
                CallbackQueryHandler(admin_delete_start, pattern=r"^admin_delete$"),
                CallbackQueryHandler(back_to_shop, pattern=r"^back_to_shop$")
            ],
            ADD_BRAND_CAT: [CallbackQueryHandler(admin_add_brand_cat, pattern=r"^admin_addbrand_cat_")],
            ADD_BRAND_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_brand_input)],
            ADD_BRAND_CONFIRM: [CallbackQueryHandler(admin_add_brand_confirm, pattern=r"^admin_addbrand_confirm_")],

            ADD_VAR_CAT: [CallbackQueryHandler(admin_addvar_cat, pattern=r"^admin_addvar_cat_")],
            ADD_VAR_BRAND: [CallbackQueryHandler(admin_addvar_brand, pattern=r"^admin_addvar_brand_")],
            ADD_VAR_OPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_addvar_option)],
            ADD_VAR_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_addvar_price)],
            ADD_VAR_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_addvar_stock)],
            ADD_VAR_PHOTO: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), admin_addvar_photo)],

            DEL_ACTION: [CallbackQueryHandler(admin_del_brand_cat, pattern=r"^admin_del_brand$"),
                         CallbackQueryHandler(admin_del_variant_cat, pattern=r"^admin_del_variant$")],
            DEL_CAT_SELECT: [CallbackQueryHandler(admin_delbrand_brand, pattern=r"^admin_delbrand_cat_"),
                             CallbackQueryHandler(admin_delvar_brand, pattern=r"^admin_delvar_cat_")],
            DEL_BRAND_SELECT: [CallbackQueryHandler(admin_delbrand_confirm_choice, pattern=r"^admin_delbrand_confirm_"),
                               CallbackQueryHandler(admin_delvar_variants, pattern=r"^admin_delvar_brand_")],
            DEL_VAR_SELECT: [CallbackQueryHandler(admin_delvar_confirm, pattern=r"^admin_delvar_confirm_")],
            DEL_CONFIRM: [CallbackQueryHandler(admin_delbrand_final, pattern=r"^admin_delbrand_final_")],
        },
        fallbacks=[CommandHandler("admin", admin_start)],
        allow_reentry=True,
        per_chat=True
    )

    app.add_handler(shop_conv)
    app.add_handler(admin_conv)

    # Удобная команда /myid для получения своего id (используй, чтобы стать админом)
    async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        await send_or_edit(update, f"Ваш ID: `{uid}`", reply_markup=None, parse_mode="Markdown")

    app.add_handler(CommandHandler("myid", myid))

    app.run_polling()

if __name__ == "__main__":
    main()
