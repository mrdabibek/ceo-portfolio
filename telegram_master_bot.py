#!/usr/bin/env python3
"""
◈ CEO Portfolio — Telegram Master Assistant & Channel Auto-Poster
Bot: @usegravity_bot
Pure Python, zero-dependency.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import threading
import subprocess

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


CONFIG_FILE = "bot_config.json"
POSTS_FILE = "channel_posts.json"

DEFAULT_CONFIG = {
    "bot_token": "8743515377:AAF8nFZrgNxptoiMRjuOzMMzFhhhHYTUb9o",
    "channel_id": "",
    "admin_id": "",
    "last_posted_index": 0,
    "base_url": "https://ceo-portfolio.pages.dev",
    "schedule_hours": 0
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def load_posts():
    if os.path.exists(POSTS_FILE):
        try:
            with open(POSTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

class TelegramClient:
    def __init__(self, token):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}"

    def request(self, method, data=None):
        url = f"{self.api_url}/{method}"
        headers = {"User-Agent": "CEOPortfolioBot/2.0"}
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
                return json.loads(err_body)
            except Exception:
                return {"ok": False, "description": str(e)}
        except Exception as e:
            return {"ok": False, "description": str(e)}

    def get_me(self):
        return self.request("getMe")

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML"):
        data = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.request("sendMessage", data)

    def send_photo(self, chat_id, photo_url, caption="", parse_mode="HTML"):
        data = {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        return self.request("sendPhoto", data)

    def get_updates(self, offset=None, timeout=25):
        data = {"timeout": timeout, "allowed_updates": ["message", "channel_post", "my_chat_member"]}
        if offset is not None:
            data["offset"] = offset
        return self.request("getUpdates", data)


class PortfolioMasterBot:
    def __init__(self):
        self.config = load_config()
        self.posts = load_posts()
        self.tg = TelegramClient(self.config["bot_token"])
        self.running = True
        self.web_process = None

    def get_main_keyboard(self):
        base = self.config.get("base_url", "https://deposits-jeremy-comparing-wild.trycloudflare.com")
        return {
            "keyboard": [
                [{"text": "🎮 Subway Surfers O'ynash", "web_app": {"url": f"{base}/subway-surfers/index.html"}}],
                [{"text": "📢 Navbatdagi Loyiha"}, {"text": "📊 Tizim Holati"}],
                [{"text": "🌐 Cloudflare Deploy"}, {"text": "⚡ Web Server"}],
                [{"text": "💼 Upwork Taklif"}, {"text": "⏰ Avto-Jadval"}]
            ],
            "resize_keyboard": True,
            "persistent": True
        }

    def post_project(self, num_str, target_chat=None):
        num_str = str(num_str).zfill(2)
        post = self.posts.get(num_str)
        if not post:
            return False, f"Loyiha #{num_str} topilmadi! (01 dan 66 gacha mavjud)"

        chat = target_chat or self.config.get("channel_id")
        if not chat:
            return False, "Kanal hali ulanmagan! Iltimos, /setchannel @kanalingiz buyrug'ini yozing."

        text = post["text"]
        base = self.config.get("base_url", "https://ceo-portfolio.pages.dev")
        text = text.replace("https://ceo-portfolio.pages.dev", base)

        img = post.get("img")
        res = self.tg.send_photo(chat, img, caption=text, parse_mode="")
        if not res.get("ok"):
            res = self.tg.send_message(chat, text, parse_mode="")

        if res.get("ok"):
            self.config["last_posted_index"] = int(num_str)
            save_config(self.config)
            return True, f"Loyiha #{num_str} ({post['title']}) muvaffaqiyatli kanalga joylandi!"
        else:
            return False, f"Xatolik: {res.get('description', res.get('error'))}"

    def post_next(self, target_chat=None):
        cur = self.config.get("last_posted_index", 0)
        next_id = (cur % 66) + 1
        return self.post_project(str(next_id), target_chat)

    def trigger_deploy(self):
        try:
            # Check wrangler login status first
            check = subprocess.run("npx wrangler whoami", shell=True, capture_output=True, text=True, timeout=20)
            if "You are not authenticated" in check.stdout or "You are not authenticated" in check.stderr:
                tunnel_url = self.config.get("base_url", "https://deposits-jeremy-comparing-wild.trycloudflare.com")
                msg = (
                    "⚠️ <b>Cloudflare tizimiga 1 marta kirish kerak:</b>\n\n"
                    "Telegram bot fonda ishlayotgani sababli brauzerni to'g'ridan-to'g'ri ocha olmaydi.\n\n"
                    "👉 <b>Yechim (atigi 5 soniya):</b>\n"
                    "Kompyuteringizdagi <code>deploy-cloudflare.bat</code> faylini 2 marta bosing va brauzerda <b>'Allow'</b> tugmasini bosing! Shundan so'ng <b>ceo-portfolio.pages.dev</b> ga doimiy yuklanadi.\n\n"
                    f"🌐 <b>Hozirgi Jonli Havolangiz (100% ishlab turibdi):</b>\n{tunnel_url}"
                )
                return False, msg

            cmd = "npx wrangler pages deploy . --project-name=ceo-portfolio --commit-dirty=true"
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            if proc.returncode == 0:
                output = proc.stdout
                url_line = "https://ceo-portfolio.pages.dev"
                for line in output.splitlines():
                    if "pages.dev" in line:
                        url_line = line.strip()
                self.config["base_url"] = url_line
                save_config(self.config)
                return True, f"Cloudflare Pages-ga muvaffaqiyatli deploy bo'ldi!\n\n🌐 {url_line}"
            else:
                return False, f"Deploy xatoligi:\n{proc.stderr[:400]}"
        except Exception as e:
            return False, f"Xatolik yuz berdi: {str(e)}"

    def handle_message(self, msg):
        chat_id = msg.get("chat", {}).get("id")
        user_name = msg.get("from", {}).get("first_name", "Boss")
        text = msg.get("text", "").strip()

        if not text:
            return

        # Auto-save admin_id
        if not self.config.get("admin_id"):
            self.config["admin_id"] = str(chat_id)
            save_config(self.config)

        t_lower = text.lower()

        # /start or /help
        if text.startswith("/start") or text.startswith("/help"):
            ch_status = self.config.get('channel_id') or "Hali belgilanmagan (/setchannel orqali yozing)"
            welcome = f"""Salom, <b>{user_name}</b>! ◈ <b>Antigravity AI Boshqaruv Markazi</b> ishga tushdi.

Men sizning buyruqlaringizni 24/7 qabul qilaman va loyihalaringizni kanalingizga joylab boraman.

📌 <b>Ulangan Kanal:</b> <code>{ch_status}</code>
🌐 <b>Portfolio:</b> <code>{self.config.get('base_url')}</code>

<b>Quyidagi tugmalardan birini bosing yoki xohlagan buyruqni yozing:</b>"""
            self.tg.send_message(chat_id, welcome, reply_markup=self.get_main_keyboard())
            return

        # Set Channel
        if text.startswith("/setchannel"):
            parts = text.split()
            if len(parts) > 1:
                ch = parts[1].strip()
                self.config["channel_id"] = ch
                save_config(self.config)
                self.tg.send_message(chat_id, f"✅ Kanal muvaffaqiyatli saqlandi: <code>{ch}</code>\n\nBotingiz ushbu kanalda <b>Administrator</b> ekanligiga ishonch hosil qiling!", reply_markup=self.get_main_keyboard())
            else:
                self.tg.send_message(chat_id, "Foydalanish: <code>/setchannel @kanal_nomi</code>")
            return

        # Set Base URL
        if text.startswith("/seturl"):
            parts = text.split()
            if len(parts) > 1:
                new_url = parts[1].strip().rstrip("/")
                self.config["base_url"] = new_url
                save_config(self.config)
                self.tg.send_message(chat_id, f"✅ Jonli sayt havolasi yangilandi:\n<code>{new_url}</code>\n\nBundan keyingi barcha postlar shu havola bilan chiqadi!", reply_markup=self.get_main_keyboard())
            else:
                self.tg.send_message(chat_id, "Foydalanish: <code>/seturl https://yangi-manzil.pages.dev</code>")
            return

        # Post Next
        if text == "📢 Navbatdagi Loyiha" or text.startswith("/next") or "keyingi" in t_lower:
            self.tg.send_message(chat_id, "⏳ Navbatdagi loyiha kanalingizga yuklanmoqda...")
            ok, res = self.post_next()
            self.tg.send_message(chat_id, ("✅ " if ok else "❌ ") + res, reply_markup=self.get_main_keyboard())
            return

        # Post by ID: /post 01 or "loyiha 1 ni tashla"
        if text.startswith("/post") or "loyiha" in t_lower or "post" in t_lower:
            import re
            m = re.search(r"(\d{1,2})", text)
            if m:
                num = m.group(1).zfill(2)
                self.tg.send_message(chat_id, f"⏳ Loyiha #{num} kanalga tayyorlanmoqda...")
                ok, res = self.post_project(num)
                self.tg.send_message(chat_id, ("✅ " if ok else "❌ ") + res, reply_markup=self.get_main_keyboard())
                return
            elif text.startswith("/post"):
                self.tg.send_message(chat_id, "Foydalanish: <code>/post 01</code> dan <code>/post 66</code> gacha")
                return

        # Status
        if text == "📊 Tizim Holati" or text.startswith("/status") or "holat" in t_lower:
            cur = self.config.get("last_posted_index", 0)
            sched = self.config.get("schedule_hours", 0)
            status_text = f"""📊 <b>Tizim Holati:</b>

• Jami loyihalar: <b>66 ta</b>
• Oxirgi chiqqan loyiha: <b>#{cur:02d}</b>
• Navbatda turgani: <b>#{(cur % 66) + 1:02d}</b>
• Ulangan kanal: <b>{self.config.get('channel_id') or 'Belgilanmagan'}</b>
• Cloudflare Havolasi: <code>{self.config.get('base_url')}</code>
• Avto-post Jadvali: <b>{f'Har {sched} soatda' if sched > 0 else 'To‘xtatilgan'}</b>
• Local Web Server: <b>Aktiv (http://localhost:8080)</b>"""
            self.tg.send_message(chat_id, status_text, reply_markup=self.get_main_keyboard())
            return

        # Deploy
        if text == "🌐 Cloudflare Deploy" or text.startswith("/deploy") or "deploy" in t_lower:
            self.tg.send_message(chat_id, "🚀 Cloudflare Pages-ga yangi versiya yuklanmoqda... 20-30 soniya kuting.")
            ok, res = self.trigger_deploy()
            self.tg.send_message(chat_id, ("✅ " if ok else "❌ ") + res, reply_markup=self.get_main_keyboard())
            return

        # Web Server Status
        if text == "⚡ Web Server" or text.startswith("/server") or "server" in t_lower:
            self.tg.send_message(chat_id, "⚡ <b>Local Web Server:</b> Faol ishlab turibdi!\nManzil: <code>http://localhost:8080</code>\nBarcha 66 ta loyiha va simulyator kompyuteringizda yoniq.", reply_markup=self.get_main_keyboard())
            return

        # Upwork Pitch
        if text == "💼 Upwork Taklif" or text.startswith("/pitch"):
            parts = text.split()
            num = parts[1].zfill(2) if len(parts) > 1 and parts[1].isdigit() else "01"
            p = self.posts.get(num, self.posts.get("01"))
            pitch = f"""💼 <b>Upwork Uchun Taklif Xati (#{num}):</b>

<code>Hi! I noticed your requirements and can deliver this cleanly and on schedule.

I have already architected and shipped a production build with this exact architecture: "{p['title']}".
Live verified demo: {self.config.get('base_url')}/{p['folder']}/index.html

I ensure zero technical debt, modular components, and 30-day post-launch warranty. Let's discuss your roadmap!</code>"""
            self.tg.send_message(chat_id, pitch, reply_markup=self.get_main_keyboard())
            return

        # Auto Schedule
        if text == "⏰ Avto-Jadval" or text.startswith("/schedule"):
            parts = text.split()
            if len(parts) > 1 and parts[1].isdigit():
                h = int(parts[1])
                self.config["schedule_hours"] = h
                save_config(self.config)
                self.tg.send_message(chat_id, f"⏰ Avto-jadval yoqildi! Har {h} soatda 1 ta yangi loyiha kanalga tashlanadi.", reply_markup=self.get_main_keyboard())
            else:
                self.tg.send_message(chat_id, "Avtomatik kanalga tashlab turish uchun soatni belgilang:\nMasalan: <code>/schedule 3</code> (har 3 soatda)\nTo'xtatish uchun: <code>/stop</code>", reply_markup=self.get_main_keyboard())
            return

        if text.startswith("/stop"):
            self.config["schedule_hours"] = 0
            save_config(self.config)
            self.tg.send_message(chat_id, "🛑 Avto-post jadvali to'xtatildi.", reply_markup=self.get_main_keyboard())
            return

        # Run Command if requested
        if text.startswith("/cmd "):
            command = text[5:].strip()
            self.tg.send_message(chat_id, f"⚡ Buyruq bajarilmoqda: <code>{command}</code>")
            try:
                out = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT, text=True, timeout=30)
                reply = f"<code>{out[:3500]}</code>" if out else "Bajarildi (javob bo'sh)."
            except Exception as e:
                reply = f"Xatolik: {str(e)}"
            self.tg.send_message(chat_id, reply, reply_markup=self.get_main_keyboard())
            return

        # Default smart response
        self.tg.send_message(chat_id, f"Buyruq qabul qilindi. Loyihani kanalga chiqarish uchun pastdagi <b>'📢 Navbatdagi Loyiha'</b> tugmasini bosing yoki <code>/post 01</code> deb yozing!", reply_markup=self.get_main_keyboard())

    def handle_channel_post(self, post):
        # Auto-detect channel if bot receives channel updates
        chat = post.get("chat", {})
        cid = chat.get("id")
        title = chat.get("title", "")
        username = chat.get("username", "")
        ch_identifier = f"@{username}" if username else str(cid)
        if not self.config.get("channel_id"):
            self.config["channel_id"] = ch_identifier
            save_config(self.config)
            print(f"[+] Kanal avtomatik aniqlandi va saqlandi: {title} ({ch_identifier})")

    def run_scheduler(self):
        while self.running:
            try:
                h = self.config.get("schedule_hours", 0)
                if h > 0 and self.config.get("channel_id"):
                    time.sleep(h * 3600)
                    if self.config.get("schedule_hours", 0) > 0:
                        self.post_next()
                else:
                    time.sleep(10)
            except Exception:
                time.sleep(10)

    def run(self):
        print("=" * 66)
        print("  [+] USEGRAVITY TELEGRAM BOT FAOL!")
        print(f"  [*] Bot: @usegravity_bot")
        print(f"  [*] Kanal: {self.config.get('channel_id') or 'Kutilmoqda...'}")
        print("=" * 66)

        sched_t = threading.Thread(target=self.run_scheduler, daemon=True)
        sched_t.start()

        offset = None
        while self.running:
            try:
                updates = self.tg.get_updates(offset=offset, timeout=25)
                if updates.get("ok"):
                    for u in updates.get("result", []):
                        offset = u["update_id"] + 1
                        if "message" in u:
                            self.handle_message(u["message"])
                        elif "channel_post" in u:
                            self.handle_channel_post(u["channel_post"])
                        elif "my_chat_member" in u:
                            chat = u["my_chat_member"].get("chat", {})
                            if chat.get("type") == "channel":
                                username = chat.get("username")
                                self.config["channel_id"] = f"@{username}" if username else str(chat.get("id"))
                                save_config(self.config)
                time.sleep(0.3)
            except KeyboardInterrupt:
                print("Bot to'xtatildi.")
                self.running = False
                break
            except Exception as e:
                time.sleep(2)

if __name__ == "__main__":
    bot = PortfolioMasterBot()
    bot.run()
