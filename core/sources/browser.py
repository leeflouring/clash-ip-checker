import ipaddress
import re

from .base import BaseSource


class BrowserSource(BaseSource):
    def __init__(self, headless=True):
        self.playwright = None
        self.browser = None

    @property
    def name(self):
        return "browser"

    async def start(self):
        if self.browser:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise RuntimeError(
                "Browser mode requires Playwright and its Chromium browser"
            ) from error
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )

    async def stop(self):
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    async def check(self, proxy_url, timeout=None):
        await self.start()
        context = await self.browser.new_context(
            proxy={"server": proxy_url} if proxy_url else None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            ),
        )

        async def block_heavy_assets(route):
            if route.request.resource_type in {"image", "media", "font"}:
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", block_heavy_assets)
        page = await context.new_page()
        result = {
            "pure_emoji": "❓",
            "ip_attr": "未知",
            "ip_src": "未知",
            "pure_score": "❓",
            "bot_score": "N/A",
            "ip": "❓",
            "source": "browser",
            "error": None,
        }
        try:
            await page.goto(
                "https://ippure.com/",
                wait_until="domcontentloaded",
                timeout=(timeout or 30) * 1000,
            )
            text = await page.inner_text("body")

            score = re.search(r"IPPure系数.*?(\d+(?:\.\d+)?%)", text, re.S)
            if score:
                result["pure_score"] = score.group(1)
                result["pure_emoji"] = self.get_emoji(score.group(1))
            bot = re.search(r"bot\s*(\d+(?:\.\d+)?%)", text, re.I)
            if bot:
                result["bot_score"] = bot.group(1)
            attribute = re.search(r"IP属性\s*\n?\s*([^\n]+)", text)
            if attribute:
                result["ip_attr"] = re.sub(
                    r"IP$",
                    "",
                    attribute.group(1).strip(),
                )
            source = re.search(r"IP来源\s*\n?\s*([^\n]+)", text)
            if source:
                result["ip_src"] = re.sub(
                    r"IP$",
                    "",
                    source.group(1).strip(),
                )
            address = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
            if address:
                result["ip"] = address.group(0)
            try:
                address = ipaddress.ip_address(result["ip"])
                risk = float(result["pure_score"].removesuffix("%"))
                if (
                    not address.is_global
                    or not 0 <= risk <= 100
                    or result["ip_attr"] == "未知"
                    or result["ip_src"] == "未知"
                ):
                    raise ValueError
            except (TypeError, ValueError):
                result["error"] = (
                    "Browser response missing complete public IP risk data"
                )
            result["full_string"] = (
                f"【{result['pure_emoji']} "
                f"{result['ip_attr']}|{result['ip_src']}】"
            )
        except Exception as error:
            result["error"] = str(error)
        finally:
            await page.close()
            await context.close()
        return result
