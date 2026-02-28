import asyncio
import logging
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from app.config import *
import app.config as config
from app import database as db
from app.services import locket, nextdns

logger = logging.getLogger(__name__)

request_queue = asyncio.PriorityQueue()
pending_items = []
queue_lock = asyncio.Lock()

class Clr:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

async def update_pending_positions(app):
    for i, item in enumerate(pending_items):
        position = i + 1
        ahead = i
        try:
            # Update position text
            await app.bot.edit_message_text(
                chat_id=item['chat_id'],
                message_id=item['message_id'],
                text=T("queued", item['lang']).format(item['username'], position, ahead),
                parse_mode=ParseMode.HTML
            )
            
            # Notify if almost turn (ahead == 2)
            if ahead == 2:
                try:
                    await app.bot.send_message(
                        chat_id=item['chat_id'],
                        text=T("queue_almost", item['lang']),
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
        except:
            pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = await db.get_lang(user_id) or DEFAULT_LANG
    
    if not await db.get_user_usage(user_id):
        pass 

    await update.message.reply_text(
        T("welcome", lang),
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu_keyboard(lang)
    )

async def setlang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_language_select(update)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = await db.get_lang(user_id) or DEFAULT_LANG
    
    help_text = T("help_msg", lang)
    if user_id == ADMIN_ID:
        help_text += (
            f"\n\n<b>👑 Admin Control:</b>\n"
            f"/noti [msg] - Broadcast message\n"
            f"/rs [id] - Reset usage limit\n"
            f"/setdonate - Set success photo\n"
            f"/stats - View detailed statistics"
        )
        
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return

    stats = await db.get_stats()
    msg = (
        f"{E_STAT} <b>SYSTEM STATISTICS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{E_USER} <b>Active Users</b>: {stats['unique_users']}\n"
        f"{E_GLOBE} <b>Total Requests</b>: {stats['total']}\n"
        f"{E_SUCCESS} <b>Success</b>: {stats['success']}\n"
        f"{E_ERROR} <b>Failed</b>: {stats['fail']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{E_ANDROID} <b>Active Workers</b>: {NUM_WORKERS}\n"
        f"🔑 <b>Token Sets</b>: {len(TOKEN_SETS)}\n"
        f"⏳ <b>Queue Size</b>: {request_queue.qsize()}\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# --- Admin Commands ---
async def broadcast_worker(bot, users, text, chat_id, message_id):
    success = 0
    fail = 0
    total = len(users)
    
    for i, uid in enumerate(users):
        try:
            await bot.send_message(chat_id=uid, text=f"📢 <b>ADMIN NOTIFICATION</b>\n\n{text}", parse_mode=ParseMode.HTML)
            success += 1
        except Exception:
            fail += 1
            
        # Update progress every 5 users or at the end
        if (i + 1) % 5 == 0 or (i + 1) == total:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"{E_LOADING} <b>Broadcasting...</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"🔄 <b>Progress</b>: {i+1}/{total}\n"
                        f"{E_SUCCESS} <b>Success</b>: {success}\n"
                        f"{E_ERROR} <b>Failed</b>: {fail}"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        
        await asyncio.sleep(0.05) # Prevent flood limits

    # Final completion message
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"{E_SUCCESS} <b>Broadcast Complete!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👥 <b>Total</b>: {total}\n"
                f"{E_SUCCESS} <b>Success</b>: {success}\n"
                f"{E_ERROR} <b>Failed</b>: {fail}"
            ),
            parse_mode=ParseMode.HTML
        )
    except:
        pass

async def noti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = await db.get_lang(user_id) or DEFAULT_LANG
    
    if user_id != ADMIN_ID:
        return
        
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /noti {message}")
        return

    users = await db.get_all_users()
    if not users:
        await update.message.reply_text("No users found.")
        return

    status_msg = await update.message.reply_text(
        f"{E_LOADING} <b>Starting broadcast to {len(users)} users...</b>",
        parse_mode=ParseMode.HTML
    )
    
    asyncio.create_task(broadcast_worker(context.bot, users, msg, status_msg.chat_id, status_msg.message_id))

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = await db.get_lang(user_id) or DEFAULT_LANG
    
    if user_id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /rs {user_id}")
        return
        
    try:
        target_id = int(context.args[0])
        await db.reset_usage(target_id)
        await update.message.reply_text(T("admin_reset", lang).format(target_id))
    except ValueError:
        await update.message.reply_text("Invalid User ID")

async def set_donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    photo = None
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        photo = update.message.reply_to_message.photo[-1]
    elif update.message.photo:
        photo = update.message.photo[-1]
        
    if photo:
        file_id = photo.file_id
        await db.set_config("donate_photo", file_id)
        await update.message.reply_text(f"✅ Updated Donate Photo ID:\n<code>{file_id}</code>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Please reply to a photo with /setdonate to set it.")

async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    import app.config as config
    success, msg = config.load_dynamic_config()
    if success:
         await update.message.reply_text(f"✅ Reload Success: {msg}")
    else:
         await update.message.reply_text(f"❌ Reload Failed: {msg}")

async def addvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /addvip {user_id} {days}")
        return
    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        await db.set_vip(target_id, days)
        await update.message.reply_text(f"✅ User <code>{target_id}</code> has been granted VIP for {days} days.", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text("Invalid inputs. User ID and Days must be integers.")
        
async def rmvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /rmvip {user_id}")
        return
    try:
        target_id = int(context.args[0])
        await db.set_vip(target_id, 0) # 0 days essentially un-VIPs them
        await update.message.reply_text(f"✅ User <code>{target_id}</code> VIP status removed.", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text("Invalid User ID.")

async def show_language_select(update: Update):
    keyboard = [
        [InlineKeyboardButton("Tiếng Việt 🇻🇳", callback_data="setlang_VI")],
        [InlineKeyboardButton("English 🇺🇸", callback_data="setlang_EN")]
    ]
    text = T("lang_select", "EN")
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only process if it's a reply to the bot's prompt (ForceReply)
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user.is_bot:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()
    lang = await db.get_lang(user_id) or DEFAULT_LANG

    if "locket.cam/" in text:
        username = text.split("locket.cam/")[-1].split("?")[0]
    elif len(text) < 50 and " " not in text:
        username = text
    else:
        username = text

    msg = await update.message.reply_text(T("resolving", lang), parse_mode=ParseMode.HTML)
    
    uid = await locket.resolve_uid(username)
    if not uid:
        await msg.edit_text(T("not_found", lang), parse_mode=ParseMode.HTML)
        return
        
    # Admin bypass limit check
    if user_id != ADMIN_ID and not await db.check_can_request(user_id):
        await msg.edit_text(T("limit_reached", lang), parse_mode=ParseMode.HTML)
        return
        
    await msg.edit_text(T("checking_status", lang), parse_mode=ParseMode.HTML)
    status = await locket.check_status(uid)
    
    status_text = T("free_status", lang)
    if status and status.get("active"):
        status_text = T("gold_active", lang).format(status['expires'])
    
    safe_username = username[:30]
    keyboard = [[InlineKeyboardButton(T("btn_upgrade", lang), callback_data=f"upg|{uid}|{safe_username}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await msg.edit_text(
        f"{T('user_info_title', lang)}\n"
        f"{E_ID}: <code>{uid}</code>\n"
        f"{E_TAG}: <code>{username}</code>\n"
        f"{E_STAT} <b>Status</b>: {status_text}\n\n"
        f"👇",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    lang = await db.get_lang(user_id) or DEFAULT_LANG

    if data.startswith("setlang_"):
        new_lang = data.split("_")[1]
        await db.set_lang(user_id, new_lang)
        lang = new_lang
        await query.answer(f"Language: {new_lang}")
        await query.message.edit_text(
            T("menu_msg", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu_keyboard(lang)
        )
        return

    if data == "menu_lang":
        await show_language_select(update)
        return
        
    if data == "menu_help":
        help_text = T("help_msg", lang)
        if user_id == ADMIN_ID:
            help_text += (
                f"\n\n<b>👑 Admin Control:</b>\n"
                f"/noti [msg] - Broadcast message\n"
                f"/rs [id] - Reset usage limit\n"
                f"/setdonate - Set success photo\n"
                f"/stats - View detailed statistics"
            )
            
        await query.edit_message_text(
            help_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]])
        )
        return

    if data == "menu_back":
        await query.message.edit_text(
            T("menu_msg", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu_keyboard(lang)
        )
        return

    if data == "menu_input":
        try:
            await query.answer()
        except:
            pass
        await query.message.reply_text(
            T("prompt_input", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True, input_field_placeholder="Username...")
        )
        return

    if data.startswith("upg|"):
        parts = data.split("|")
        uid = parts[1]
        username = parts[2] if len(parts) > 2 else uid
        
        is_vip = await db.check_is_vip(user_id)
        if not is_vip and user_id != ADMIN_ID and not await db.check_can_request(user_id):
            try:
                await query.answer(T("limit_reached", lang), show_alert=True)
            except:
                pass
            return
            
        try:
            await query.answer("🚀 Queue...")
        except:
            pass
        
        item = {
            'user_id': user_id,
            'uid': uid,
            'username': username,
            'chat_id': query.message.chat_id,
            'message_id': query.message.message_id,
            'lang': lang,
            'is_vip': is_vip
        }
        
        priority = 0 if is_vip or user_id == ADMIN_ID else 1
        
        async with queue_lock:
            # Insert visually based on priority
            if priority == 0:
                # Find last VIP in pending_items to insert after
                last_vip_idx = -1
                for i, p_item in enumerate(pending_items):
                    if p_item.get('is_vip', False):
                        last_vip_idx = i
                pending_items.insert(last_vip_idx + 1, item)
                position = last_vip_idx + 2
            else:
                pending_items.append(item)
                position = len(pending_items)
            ahead = position - 1
        
        vip_tag = " [👑 VIP]" if is_vip else ""
        await query.edit_message_text(
            T("queued", lang).format(username, position, ahead) + vip_tag,
            parse_mode=ParseMode.HTML
        )
        
        await request_queue.put((priority, item))
        return

async def queue_worker(app, worker_id):
    while True:
        try:
            # Refresh config checks
            if len(config.TOKEN_SETS) == 0:
                await asyncio.sleep(5)
                continue
                
            token_idx = (worker_id - 1) % len(config.TOKEN_SETS)
            token_config = config.TOKEN_SETS[token_idx]
            token_name = token_config.get("name", f"Token-{token_idx+1}")
            
            # Unpack PriorityQueue tuple
            priority, item = await request_queue.get()
            
            user_id = item['user_id']
            uid = item['uid']
            username = item['username']
            chat_id = item['chat_id']
            message_id = item['message_id']
            lang = item['lang']
            is_vip = item.get('is_vip', False)
            
            async with queue_lock:
                if item in pending_items:
                    pending_items.remove(item)
                await update_pending_positions(app) # Enabled queue updates
            
            print(f"{Clr.BLUE}[Worker #{worker_id}][{token_name}] Processing:{Clr.ENDC} UID={uid} | UserID={user_id}")
            
            async def edit(text):
                try:
                    await app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    if "Message is not modified" in str(e):
                        pass
                    elif "Message to edit not found" in str(e):
                        pass
                    else:
                        logger.error(f"Edit msg error: {e}")

            # Double check limit before processing (unless admin)
            if user_id != ADMIN_ID and not await db.check_can_request(user_id):
                await edit(T("limit_reached", lang))
                request_queue.task_done()
                continue
            
            logs = [f"[Worker #{worker_id}] Processing Request..."]
            loop = asyncio.get_running_loop()
            
            def safe_log_callback(msg):
                clean_msg = msg.replace(Clr.BLUE, "").replace(Clr.GREEN, "").replace(Clr.WARNING, "").replace(Clr.FAIL, "").replace(Clr.ENDC, "").replace(Clr.BOLD, "")
                logs.append(clean_msg)
                asyncio.run_coroutine_threadsafe(update_log_ui(), loop)

            async def update_log_ui():
                display_logs = "\n".join(logs[-10:])
                text = (
                    f"{E_LOADING} <b>⚡ SYSTEM EXPLOIT RUNNING...</b>\n"
                    f"<pre>{display_logs}</pre>"
                )
                try:
                    await app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                except:
                    pass

            await update_log_ui()
            
            # Use dynamic token config
            success, msg_result = await locket.inject_gold(uid, token_config, safe_log_callback)
            
            # Log request to DB
            await db.log_request(user_id, uid, "SUCCESS" if success else "FAIL")
            
            if success:
                if user_id != ADMIN_ID and not is_vip:
                    await db.increment_usage(user_id)
                    
                pid, link = await nextdns.create_profile(config.NEXTDNS_KEY, safe_log_callback)
                
                dns_text = ""
                if link:
                   dns_text = T('dns_msg', lang).format(link, pid)
                else:
                   dns_text = f"{E_ERROR} NextDNS Error: Check API Key"
                
                final_msg = (
                    f"{T('success_title', lang)}\n\n"
                    f"{E_TAG}: <code>{username}</code>\n"
                    f"{E_ID}: <code>{uid}</code>\n"
                    f"{E_CALENDAR} <b>Plan</b>: Gold (30d)\n"
                    f"{dns_text}"
                )
                
                await asyncio.sleep(2.0)
                
                # Delete progress message and send photo with caption
                try:
                    await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except:
                    pass
                
                try:
                    current_photo = await db.get_config("donate_photo", DONATE_PHOTO)
                    await app.bot.send_photo(
                        chat_id=chat_id,
                        photo=current_photo,
                        caption=final_msg,
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Send photo error: {e}")
                    # Fallback to text if photo fails
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=final_msg,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )

                # Wait 45s for THIS token/worker
                await asyncio.sleep(45)
            else:
                final_msg = f"{T('fail_title', lang)}\nInfo:\n<code>{msg_result}</code>"
                await edit(final_msg)
                
            request_queue.task_done()
            
        except Exception as e:
            logger.error(f"Worker #{worker_id} Exception: {e}")
            request_queue.task_done()

def get_main_menu_keyboard(lang):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T("btn_input", lang), callback_data="menu_input")],
        [InlineKeyboardButton(T("btn_lang", lang), callback_data="menu_lang"),
         InlineKeyboardButton(T("btn_help", lang), callback_data="menu_help")]
    ])

async def token_health_check(app):
    while True:
        try:
            logger.info("[HealthCheck] Running scheduled token validation...")
            valid_tokens = []
            removed_count = 0
            
            for token_config in config.TOKEN_SETS:
                # Basic validation ping using locket services or a mock check if needed.
                # Assuming locket services expose an async validation endpoint or
                # using a lightweight endpoint
                url = "https://api.locketcamera.com/v1/users/me"
                headers = {
                    "Authorization": f"Bearer {token_config.get('fetch_token')}",
                    "User-Agent": "Locket/1.0.0"
                }
                
                try:
                     import aiohttp
                     async with aiohttp.ClientSession() as session:
                          async with session.get(url, headers=headers) as resp:
                               if resp.status in [200, 403, 404]: # Assuming 401 is unauthorized
                                    valid_tokens.append(token_config)
                               else:
                                    logger.warning(f"[HealthCheck] Token {token_config.get('name')} failed with status {resp.status}. Discarding.")
                                    removed_count += 1
                except Exception as e:
                     logger.warning(f"[HealthCheck] Error checking token {token_config.get('name')}: {e}")
                     # Be safe, keep it if network error, only drop on explicit 401/auth fail.
                     valid_tokens.append(token_config)
            
            if removed_count > 0:
                logger.warning(f"[HealthCheck] Removed {removed_count} dead tokens. {len(valid_tokens)} remaining.")
                config.TOKEN_SETS = valid_tokens
                # Optionally update num workers if needed.
                
            await asyncio.sleep(3600) # Check every hour
        except asyncio.CancelledError:
             break
        except Exception as e:
            logger.error(f"[HealthCheck] Critical error in health checker: {e}")
            await asyncio.sleep(300)

def run_bot():
    logging.basicConfig(
        format='%(message)s',
        level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("telegram").setLevel(logging.ERROR)
    logging.getLogger("aiohttp").setLevel(logging.ERROR)

    app = ApplicationBuilder().token(config.BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setlang", setlang_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("noti", noti_command))
    app.add_handler(CommandHandler("rs", reset_command))
    app.add_handler(CommandHandler("setdonate", set_donate_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("reload", reload_command))
    app.add_handler(CommandHandler("addvip", addvip_command))
    app.add_handler(CommandHandler("rmvip", rmvip_command))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    async def post_init(application):
        await db.init_db()
        # Dynamically create workers based on config
        for i in range(1, config.NUM_WORKERS + 1):
            asyncio.create_task(queue_worker(application, i))
            
        # Start health check loop
        asyncio.create_task(token_health_check(application))

    app.post_init = post_init
    print(f"Bot is running... ({config.NUM_WORKERS} workers)")
    app.run_polling()
