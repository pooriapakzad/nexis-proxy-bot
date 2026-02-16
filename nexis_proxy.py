"""
╔══════════════════════════════════════════════════════╗
║           NEXIS PROXY BOT — v2.0                     ║
║   جمع‌آوری، تست و ارسال پروکسی از کانال‌های تلگرام   ║
╚══════════════════════════════════════════════════════╝

نصب:
    pip install telethon python-telegram-bot httpx

راه‌اندازی:
    1. تنظیمات پایین رو پر کن
    2. python nexis_proxy_bot.py
"""

import asyncio
import re
import time
import logging
import httpx
from datetime import datetime
from typing import Optional
from telethon import TelegramClient
from telegram import Bot
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

# ─────────────────────────────────────────────
# ⚙️  تنظیمات — اینجا رو پر کن
# ─────────────────────────────────────────────

BOT_TOKEN        = "8335962573:AAHVk5Hvq6vGNCOlsmgi3P0raE5RJPsr_XQ"       # از @BotFather
OUTPUT_CHANNEL   = "@proxyney10"        # کانال خروجی
API_ID           = 22633821               # از my.telegram.org
API_HASH         = "6bf4c85c437caebda13cb3f8bcba65d1"        # از my.telegram.org
PHONE            = "+989029083185"        # شماره تلگرامت

SOURCE_CHANNELS = [
    "@proxy_mtn",
    "@ProxyForTelegram",
    "@proxies_MTProto",
    "@socks5_proxy",
    "@http_proxies_free",
    "@Myporoxy"
    # هر کانالی که میخوای اضافه کن...
]

COLLECT_INTERVAL    = 120   # هر چند ثانیه (120 = 2 دقیقه)
TEST_TIMEOUT        = 6     # تایم‌اوت تست پروکسی
MAX_PER_POST        = 5    # حداکثر پروکسی در هر پیام
MESSAGES_TO_SCAN    = 20    # چند پیام آخر هر کانال اسکن بشه


# ─────────────────────────────────────────────
# 🎨  قالب پیام — هر طور خواستی عوض کن
# ─────────────────────────────────────────────

def build_message(proxies: list, ptype: str, batch: int, total: int) -> str:
    now = datetime.now().strftime("%H:%M  |  %Y/%m/%d")
    emoji = {"SOCKS5": "🟣", "HTTP": "🔵", "HTTPS": "🔵", "MTPROTO": "✈️"}.get(ptype, "⚪")

    lines = [
        f"{emoji}  *پروکسی {ptype} — تست شده و فعال*",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 {now}   |   دسته {batch}/{total}",
        "",
    ]

    for i, p in enumerate(proxies, 1):
        lines.append(f"*{i}.* `{p['address']}`")
        if p.get("latency"):
            lines.append(f"    ⚡ `{p['latency']} ms`")
        if ptype == "MTPROTO" and p.get("link"):
            lines.append(f"    🔗 [اتصال مستقیم]({p['link']})")
        lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"✅  *{len(proxies)} پروکسی فعال*",
        "📢  @NexisProxy  |  🤖 Nexis Bot",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 🔍  استخراج پروکسی از متن
# ─────────────────────────────────────────────

class ProxyExtractor:

    RE_MTPROTO = re.compile(
        r'https?://t\.me/proxy\?server=([\w.\-]+)&port=(\d+)&secret=([a-fA-F0-9]+)',
        re.I
    )
    RE_IP_PORT = re.compile(
        r'(?:socks5?://|http://|https://)?(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})'
        r'(?::([^\s:@]+):([^\s:@]+))?',
        re.I
    )
    HTTP_PORTS = {80, 8080, 3128, 8888, 8118, 8081, 8000, 1080, 3129, 8085}

    @classmethod
    def extract(cls, text: str) -> list:
        found, seen = [], set()

        for m in cls.RE_MTPROTO.finditer(text):
            key = f"{m.group(1)}:{m.group(2)}"
            if key not in seen:
                seen.add(key)
                found.append({
                    "type": "MTPROTO",
                    "host": m.group(1),
                    "port": int(m.group(2)),
                    "secret": m.group(3),
                    "address": key,
                    "link": m.group(0),
                })

        for m in cls.RE_IP_PORT.finditer(text):
            host, port_str = m.group(1), m.group(2)
            port = int(port_str)
            key = f"{host}:{port_str}"
            if key in seen or not cls._valid_ip(host):
                continue
            if "t.me" in text[max(0, m.start()-15):m.start()]:
                continue
            seen.add(key)
            prefix = text[max(0, m.start()-10):m.start()].lower()
            ptype = "SOCKS5" if ("socks" in prefix or port == 1080) else \
                    "HTTP"   if (port in cls.HTTP_PORTS or "http" in prefix) else \
                    "SOCKS5"
            entry = {"type": ptype, "host": host, "port": port, "address": key}
            if m.group(3): entry["username"] = m.group(3)
            if m.group(4): entry["password"] = m.group(4)
            found.append(entry)

        return found

    @staticmethod
    def _valid_ip(ip: str) -> bool:
        try:
            parts = ip.split(".")
            return len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
        except:
            return False


# ─────────────────────────────────────────────
# 🧪  تست پروکسی
# ─────────────────────────────────────────────

class ProxyTester:

    @staticmethod
    async def _tcp(host: str, port: int, timeout: int) -> Optional[int]:
        try:
            start = time.time()
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except:
                pass
            return int((time.time() - start) * 1000)
        except:
            return None

    @staticmethod
    async def _socks5(host: str, port: int, timeout: int) -> Optional[int]:
        try:
            start = time.time()
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.write(b'\x05\x01\x00')
            await writer.drain()
            data = await asyncio.wait_for(reader.read(2), timeout=3)
            writer.close()
            if data and len(data) >= 2 and data[0] == 0x05:
                return int((time.time() - start) * 1000)
        except:
            pass
        return None

    @staticmethod
    async def _http(host: str, port: int, username=None, password=None, timeout=TEST_TIMEOUT) -> Optional[int]:
        try:
            proxy_url = f"http://{username}:{password}@{host}:{port}" if username else f"http://{host}:{port}"
            start = time.time()
            async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as c:
                r = await c.get("http://httpbin.org/ip")
                if r.status_code == 200:
                    return int((time.time() - start) * 1000)
        except:
            pass
        return None

    @classmethod
    async def test(cls, proxy: dict) -> Optional[dict]:
        ptype = proxy["type"]
        host, port = proxy["host"], proxy["port"]

        if ptype == "SOCKS5":
            lat = await cls._socks5(host, port, TEST_TIMEOUT)
        elif ptype in ("HTTP", "HTTPS"):
            lat = await cls._http(host, port, proxy.get("username"), proxy.get("password"))
        else:
            lat = await cls._tcp(host, port, TEST_TIMEOUT)

        return {**proxy, "latency": lat} if lat is not None else None

    @classmethod
    async def test_batch(cls, proxies: list) -> list:
        results = await asyncio.gather(*[cls.test(p) for p in proxies], return_exceptions=True)
        working = [r for r in results if isinstance(r, dict)]
        working.sort(key=lambda x: x.get("latency", 9999))
        return working


# ─────────────────────────────────────────────
# 🤖  ربات اصلی
# ─────────────────────────────────────────────

class NexisProxyBot:

    def __init__(self):
        self.bot    = Bot(token=BOT_TOKEN, request=HTTPXRequest())
        self.client = TelegramClient("nexis_session", API_ID, API_HASH)
        self.seen   = set()
        self.stats  = {"collected": 0, "working": 0, "sent": 0}
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%H:%M:%S"
        )
        self.log = logging.getLogger("NexisBot")

    async def _scrape(self, channel: str) -> list:
        proxies = []
        try:
            entity = await self.client.get_entity(channel)
            async for msg in self.client.iter_messages(entity, limit=MESSAGES_TO_SCAN):
                if msg.text:
                    proxies.extend(ProxyExtractor.extract(msg.text))
        except Exception as e:
            self.log.warning(f"⚠️  {channel}: {e}")
        return proxies

    async def collect(self) -> list:
        self.log.info(f"🔍  اسکن {len(SOURCE_CHANNELS)} کانال...")
        results = await asyncio.gather(
            *[self._scrape(ch) for ch in SOURCE_CHANNELS],
            return_exceptions=True
        )
        new = []
        for r in results:
            if not isinstance(r, list):
                continue
            for p in r:
                if p["address"] not in self.seen:
                    self.seen.add(p["address"])
                    new.append(p)
        self.stats["collected"] += len(new)
        self.log.info(f"📦  {len(new)} پروکسی جدید")
        return new

    async def send(self, working: list):
        if not working:
            return
        grouped = {}
        for p in working:
            grouped.setdefault(p["type"], []).append(p)

        total = sum(-(-len(v) // MAX_PER_POST) for v in grouped.values())
        n = 0
        for ptype, proxies in grouped.items():
            for i in range(0, len(proxies), MAX_PER_POST):
                n += 1
                chunk = proxies[i:i+MAX_PER_POST]
                msg = build_message(chunk, ptype, n, total)
                try:
                    await self.bot.send_message(
                        chat_id=OUTPUT_CHANNEL,
                        text=msg,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True,
                    )
                    self.stats["sent"] += len(chunk)
                    self.log.info(f"✅  {len(chunk)} پروکسی {ptype} ارسال شد")
                except Exception as e:
                    self.log.error(f"❌  {e}")
                await asyncio.sleep(2)

    async def cycle(self):
        self.log.info("=" * 45)
        t0 = time.time()
        raw = await self.collect()
        if not raw:
            self.log.info("💤  پروکسی جدیدی نبود")
            return
        self.log.info(f"🧪  تست {len(raw)} پروکسی...")
        working = await ProxyTester.test_batch(raw)
        self.stats["working"] += len(working)
        self.log.info(f"✅  {len(working)}/{len(raw)} فعال")
        await self.send(working)
        self.log.info(
            f"⏱  {round(time.time()-t0,1)}s | "
            f"جمع={self.stats['collected']} فعال={self.stats['working']} ارسال={self.stats['sent']}"
        )

    async def run(self):
        self.log.info("🤖  Nexis Proxy Bot v2.0 شروع شد")
        await self.client.start(phone=PHONE)
        self.log.info(f"✅  اتصال برقرار | خروجی: {OUTPUT_CHANNEL}")

        try:
            await self.bot.send_message(
                chat_id=OUTPUT_CHANNEL,
                text=(
                    "🤖 *Nexis Proxy Bot* شروع به کار کرد!\n\n"
                    f"📡 منابع: `{len(SOURCE_CHANNELS)}` کانال\n"
                    f"⏰ بازه: هر `{COLLECT_INTERVAL // 60}` دقیقه\n"
                    "🧪 فقط پروکسی‌های فعال ارسال می‌شن\n\n"
                    "📢 @NexisProxy"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            self.log.warning(f"پیام خوش‌آمد: {e}")

        while True:
            try:
                await self.cycle()
            except Exception as e:
                self.log.error(f"❌  {e}")
            self.log.info(f"💤  صبر {COLLECT_INTERVAL}s...")
            await asyncio.sleep(COLLECT_INTERVAL)


if __name__ == "__main__":
    asyncio.run(NexisProxyBot().run())