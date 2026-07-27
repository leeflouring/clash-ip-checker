import ipaddress
import math

from curl_cffi.requests import AsyncSession

from ..config import config
from .base import BaseSource


class IPQuerySource(BaseSource):
    @property
    def name(self) -> str:
        return "ipquery"

    async def check(self, proxy_url: str, timeout: int = None) -> dict:
        result = {
            "pure_emoji": "❓",
            "ip_attr": "未知",
            "ip_src": "未知",
            "pure_score": "?",
            "ip": "?",
            "source": "ipquery",
            "error": None,
        }
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

        try:
            async with AsyncSession(
                proxies=proxies,
                impersonate="chrome124",
                timeout=timeout or config.request_timeout,
            ) as session:
                response = await session.get("https://api.ipquery.io/?format=json")
            if response.status_code != 200:
                result["error"] = f"HTTP {response.status_code}"
                return result

            data = response.json()
            risk = data.get("risk") if isinstance(data, dict) else None
            address = data.get("ip") if isinstance(data, dict) else None
            score = risk.get("risk_score") if isinstance(risk, dict) else None
            flags = (
                "is_mobile",
                "is_vpn",
                "is_tor",
                "is_proxy",
                "is_datacenter",
            )
            try:
                parsed_address = (
                    ipaddress.ip_address(address)
                    if isinstance(address, str)
                    else None
                )
            except (TypeError, ValueError):
                parsed_address = None
            valid_score = (
                isinstance(score, (int, float))
                and not isinstance(score, bool)
                and math.isfinite(score)
                and 0 <= score <= 100
            )
            valid_flags = isinstance(risk, dict) and all(
                isinstance(risk.get(flag), bool) for flag in flags
            )
            if not (
                parsed_address
                and parsed_address.is_global
                and valid_score
                and valid_flags
            ):
                result["error"] = "Invalid IPQuery response"
                return result

            result.update(
                {
                    "ip": str(parsed_address),
                    "pure_score": f"{score}%",
                    "pure_emoji": self.get_emoji(f"{score}%"),
                    "ip_attr": (
                        "机房"
                        if risk["is_datacenter"]
                        else "移动" if risk["is_mobile"] else "非机房"
                    ),
                }
            )
        except Exception as error:
            result["error"] = str(error)

        return result
