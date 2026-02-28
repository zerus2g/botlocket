import aiosqlite
import time
from datetime import datetime
import asyncio

DB_NAME = "bot_data.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as conn:
        await conn.execute('''CREATE TABLE IF NOT EXISTS usage_logs (
                        user_id INTEGER,
                        date TEXT,
                        count INTEGER,
                        PRIMARY KEY (user_id, date)
                    )''')
        await conn.execute('''CREATE TABLE IF NOT EXISTS user_settings (
                        user_id INTEGER PRIMARY KEY,
                        language TEXT,
                        is_vip INTEGER DEFAULT 0,
                        vip_expiry DATETIME
                    )''')
        await conn.execute('''CREATE TABLE IF NOT EXISTS bot_config (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )''')
        await conn.execute('''CREATE TABLE IF NOT EXISTS request_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        uid TEXT,
                        status TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )''')
        # Table to store hot-reload tokens
        await conn.execute('''CREATE TABLE IF NOT EXISTS tokens (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT,
                        fetch_token TEXT,
                        app_transaction TEXT,
                        hash_params TEXT,
                        hash_headers TEXT,
                        is_sandbox INTEGER DEFAULT 0,
                        is_active INTEGER DEFAULT 1,
                        fail_count INTEGER DEFAULT 0
                    )''')
        await conn.commit()

async def get_user_usage(user_id):
    async with aiosqlite.connect(DB_NAME) as conn:
        today = datetime.now().strftime("%Y-%m-%d")
        async with conn.execute("SELECT count FROM usage_logs WHERE user_id = ? AND date = ?", (user_id, today)) as cursor:
            result = await cursor.fetchone()
            return result[0] if result else 0

async def increment_usage(user_id):
    async with aiosqlite.connect(DB_NAME) as conn:
        today = datetime.now().strftime("%Y-%m-%d")
        async with conn.execute("SELECT count FROM usage_logs WHERE user_id = ? AND date = ?", (user_id, today)) as cursor:
            result = await cursor.fetchone()
        
        if result:
            new_count = result[0] + 1
            await conn.execute("UPDATE usage_logs SET count = ? WHERE user_id = ? AND date = ?", (new_count, user_id, today))
        else:
            await conn.execute("INSERT INTO usage_logs (user_id, date, count) VALUES (?, ?, ?)", (user_id, today, 1))
            
        await conn.commit()

async def check_can_request(user_id, max_limit=5):
    # Check VIP first
    is_vip = await check_is_vip(user_id)
    if is_vip:
        return True # VIPs have unlimited requests
        
    current = await get_user_usage(user_id)
    return current < max_limit

async def set_lang(user_id, lang):
    async with aiosqlite.connect(DB_NAME) as conn:
        # We need to preserve VIP status if it exists, so use an upsert or check first
        async with conn.execute("SELECT is_vip, vip_expiry FROM user_settings WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            
        if row:
            await conn.execute("UPDATE user_settings SET language = ? WHERE user_id = ?", (lang, user_id))
        else:
            await conn.execute("INSERT INTO user_settings (user_id, language) VALUES (?, ?)", (user_id, lang))
        await conn.commit()

async def get_lang(user_id):
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute("SELECT language FROM user_settings WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
            return result[0] if result else None

async def check_is_vip(user_id):
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute("SELECT is_vip FROM user_settings WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
            return bool(result[0]) if result else False

async def set_vip(user_id, is_vip: bool):
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute("SELECT language FROM user_settings WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            
        vip_val = 1 if is_vip else 0
        if row:
            await conn.execute("UPDATE user_settings SET is_vip = ? WHERE user_id = ?", (vip_val, user_id))
        else:
            await conn.execute("INSERT INTO user_settings (user_id, is_vip) VALUES (?, ?)", (user_id, vip_val))
        await conn.commit()

async def get_all_users():
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute("SELECT DISTINCT user_id FROM usage_logs UNION SELECT user_id FROM user_settings") as cursor:
            users = [row[0] async for row in cursor]
            return users

async def reset_usage(user_id):
    async with aiosqlite.connect(DB_NAME) as conn:
        today = datetime.now().strftime("%Y-%m-%d")
        await conn.execute("DELETE FROM usage_logs WHERE user_id = ? AND date = ?", (user_id, today))
        await conn.commit()

async def set_config(key, value):
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute("SELECT value FROM bot_config WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            
        if row:
            await conn.execute("UPDATE bot_config SET value = ? WHERE key = ?", (value, key))
        else:
            await conn.execute("INSERT INTO bot_config (key, value) VALUES (?, ?)", (key, value))
        await conn.commit()

async def get_config(key, default=None):
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute("SELECT value FROM bot_config WHERE key = ?", (key,)) as cursor:
            result = await cursor.fetchone()
            return result[0] if result else default

async def log_request(user_id, uid, status):
    async with aiosqlite.connect(DB_NAME) as conn:
        await conn.execute("INSERT INTO request_logs (user_id, uid, status) VALUES (?, ?, ?)", (user_id, uid, status))
        await conn.commit()

async def get_stats():
    async with aiosqlite.connect(DB_NAME) as conn:
        async with conn.execute("SELECT COUNT(*) FROM request_logs") as cursor:
            total = (await cursor.fetchone())[0]
        
        async with conn.execute("SELECT COUNT(*) FROM request_logs WHERE status = 'SUCCESS'") as cursor:
            success = (await cursor.fetchone())[0]
            
        async with conn.execute("SELECT COUNT(*) FROM request_logs WHERE status != 'SUCCESS'") as cursor:
            fail = (await cursor.fetchone())[0]
            
        async with conn.execute("SELECT COUNT(DISTINCT user_id) FROM request_logs") as cursor:
            unique_users = (await cursor.fetchone())[0]
            
        return {
            "total": total,
            "success": success,
            "fail": fail,
            "unique_users": unique_users
        }
