import asyncio
import ipaddress
import math
import os
import re

import yaml

from .clash_api import ClashController
from .config import config


class CheckerService:
    def __init__(self, api_url=None, api_secret=""):
        self.clash = ClashController(api_url or config.api_url, api_secret)
        self.skip_keywords = config.skip_keywords

    async def _check_ip_fast(self, proxy_url, options=None):
        from .sources.ipquery import IPQuerySource
        from .sources.ippure import IPPureSource
        from .sources.ping0 import Ping0Source

        options = options or {}
        sources = {
            "ippure": IPPureSource(),
            "ping0": Ping0Source(),
            "ipquery": IPQuerySource(),
        }
        primary = options.get("source", config.source)
        names = [primary] if primary in {"ping0", "ippure"} else ["ping0"]
        if options.get("fallback", config.fallback):
            names.extend(name for name in ("ping0", "ippure") if name not in names)
            names.append("ipquery")

        last_error = None
        for name in names:
            try:
                result = await sources[name].check(
                    proxy_url,
                    timeout=options.get("request_timeout", config.request_timeout),
                )
                if not result:
                    last_error = "Empty response"
                    continue
                if result.get("error"):
                    last_error = result["error"]
                    continue
                result.setdefault("source", name)
                if not self._is_complete_result(result):
                    last_error = f"Incomplete response from {name}"
                    continue
                return result
            except Exception as error:
                last_error = str(error)

        return {
            "pure_emoji": "⚫",
            "ip_attr": "未知",
            "ip_src": "未知",
            "pure_score": "?",
            "ip": "?",
            "source": "unknown",
            "error": f"All sources failed. Last: {last_error}",
        }

    @staticmethod
    def _is_complete_result(result):
        if not isinstance(result, dict) or result.get("error"):
            return False
        try:
            address = ipaddress.ip_address(result.get("ip"))
            score = float(str(result.get("pure_score")).removesuffix("%"))
        except (TypeError, ValueError):
            return False
        unknown = {None, "", "?", "❓", "未知", "—"}
        return (
            address.is_global
            and math.isfinite(score)
            and 0 <= score <= 100
            and result.get("ip_attr") not in unknown
            and (
                result.get("source") == "ipquery"
                or result.get("ip_src") not in unknown
            )
        )

    @staticmethod
    def _strip_old_tag(name):
        return re.sub(r"\s*【[^】]*】", "", name).strip()

    def _format_name(self, old_name, result):
        base_name = self._strip_old_tag(old_name)
        if result.get("error"):
            return f"{base_name} 【❌ 失败】"
        if result.get("full_string"):
            return f"{base_name} {result['full_string']}"
        info = f"{result.get('ip_attr', '未知')}|{result.get('ip_src', '未知')}"
        return f"{base_name} 【{result.get('pure_emoji', '❓')} {info}】"

    @staticmethod
    def _public_error(value, limit=240):
        if not value:
            return ""
        text = " ".join(str(value).split())
        text = re.sub(r"https?://\S+", "[redacted URL]", text, flags=re.IGNORECASE)
        text = re.sub(
            r"\b(authorization)(\s*[:=]\s*)"
            r"(?:(?:bearer|basic)\s+)?[^\s,;]+",
            r"\1\2[redacted]",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b(authorization|api[-_ ]?key|token|secret|password)"
            r"(\s*[:=]\s*)[^\s,;]+",
            r"\1\2[redacted]",
            text,
            flags=re.IGNORECASE,
        )
        return text if len(text) <= limit else f"{text[:limit - 1]}…"

    @staticmethod
    def _public_result(
        node_id,
        original_name,
        name,
        status,
        result=None,
        primary_source=None,
    ):
        result = result or {}
        source = result.get("source", "")
        return {
            "id": node_id,
            "original_name": original_name,
            "name": name,
            "ip": result.get("ip", "—"),
            "risk": result.get("pure_score", "—"),
            "bot": result.get("bot_score", "N/A"),
            "shared": result.get("shared_users", "N/A"),
            "type": result.get("ip_attr", "—"),
            "native": result.get("ip_src", "—"),
            "source": source,
            "error": CheckerService._public_error(result.get("error")),
            "degraded": bool(
                source
                and primary_source
                and source != primary_source
                and source != "unknown"
            ),
            "status": status,
        }

    @staticmethod
    def atomic_save(data, file_path):
        temporary_path = f"{file_path}.tmp"
        try:
            with open(temporary_path, "w", encoding="utf-8") as output:
                yaml.safe_dump(
                    data,
                    output,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
            os.replace(temporary_path, file_path)
        except Exception:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
            raise

    async def async_atomic_save(self, data, file_path):
        await asyncio.to_thread(self.atomic_save, data, file_path)

    @staticmethod
    def _load_yaml(file_path):
        with open(file_path, "r", encoding="utf-8") as source:
            data = yaml.safe_load(source)
        proxies = data.get("proxies") if isinstance(data, dict) else None
        if not isinstance(proxies, list) or not proxies:
            raise ValueError("Invalid Clash YAML: proxies must be a non-empty list")
        if not all(
            isinstance(proxy, dict)
            and isinstance(proxy.get("name"), str)
            and proxy["name"].strip()
            for proxy in proxies
        ):
            raise ValueError("Invalid Clash YAML: every proxy needs a name")
        return data, proxies

    @staticmethod
    def _rename_in_groups(yaml_data, old_name, new_name):
        groups = yaml_data.get("proxy-groups", [])
        if not isinstance(groups, list):
            return
        for group in groups:
            if not isinstance(group, dict):
                continue
            proxies = group.get("proxies")
            if isinstance(proxies, list):
                group["proxies"] = [
                    new_name if proxy_name == old_name else proxy_name
                    for proxy_name in proxies
                ]

    async def run_check(
        self,
        file_path,
        progress_cb=None,
        options=None,
        stop_event=None,
        target_nodes=None,
    ):
        options = options or {}
        if not await self.clash.version():
            raise ConnectionError("Clash API is unreachable")

        if not await self.clash.load_config(os.path.abspath(file_path)):
            raise ValueError("Failed to load the configuration into Clash")
        await asyncio.sleep(1)
        await self.clash.update_ports(config.mixed_port)
        await self.clash.set_mode_global()

        port = await self.clash.get_mixed_port()
        proxy_url = f"http://127.0.0.1:{port}"
        if not await self.clash.get_proxies():
            raise ValueError("No proxies found in the configuration")

        yaml_data, yaml_proxies = self._load_yaml(file_path)
        total = len(target_nodes) if target_nodes else len(yaml_proxies)
        checked = 0
        primary_source = (
            "browser"
            if options.get("mode") == "browser"
            else options.get("source", config.source)
        )
        skip_keywords = (
            options["skip_keywords"]
            if "skip_keywords" in options
            else self.skip_keywords
        )

        if progress_cb:
            await progress_cb(0, total, "Starting...")

        browser_source = None
        if options.get("mode") == "browser":
            from .sources.browser import BrowserSource

            browser_source = BrowserSource(headless=options.get("headless", True))

        try:
            for position, proxy_config in enumerate(yaml_proxies):
                original_name = proxy_config["name"]
                if target_nodes and original_name not in target_nodes:
                    continue
                node_id = (
                    target_nodes[original_name]
                    if target_nodes
                    else position
                )
                if stop_event and stop_event.is_set():
                    if progress_cb:
                        await progress_cb(checked, total, "Cancelled by user")
                    break

                display_name = self._strip_old_tag(original_name)
                if any(keyword in original_name for keyword in skip_keywords):
                    checked += 1
                    result = self._public_result(
                        node_id,
                        original_name,
                        original_name,
                        "skipped",
                    )
                    if progress_cb:
                        await progress_cb(
                            checked,
                            total,
                            f"Skipped: {display_name}",
                            result,
                        )
                    continue

                if progress_cb:
                    await progress_cb(
                        checked,
                        total,
                        f"Checking: {display_name}",
                    )

                if not await self.clash.switch_proxy(original_name):
                    checked += 1
                    result = self._public_result(
                        node_id,
                        original_name,
                        original_name,
                        "failed",
                        {
                            "source": "mihomo",
                            "error": "Mihomo 无法切换到该节点",
                        },
                    )
                    if progress_cb:
                        await progress_cb(
                            checked,
                            total,
                            f"Could not switch to: {display_name}",
                            result,
                        )
                    continue

                await asyncio.sleep(0.5)
                if browser_source:
                    try:
                        check_result = await browser_source.check(
                            proxy_url,
                            timeout=options.get(
                                "request_timeout",
                                config.request_timeout,
                            ),
                        )
                    except Exception as error:
                        check_result = {
                            "source": "browser",
                            "error": str(error),
                        }
                    if (
                        check_result.get("error")
                        and options.get("fallback", config.fallback)
                    ):
                        check_result = await self._check_ip_fast(
                            proxy_url,
                            options,
                        )
                else:
                    check_result = await self._check_ip_fast(proxy_url, options)
                if not self._is_complete_result(check_result):
                    check_result["error"] = (
                        check_result.get("error")
                        or f"Incomplete response from "
                        f"{check_result.get('source', 'unknown')}"
                    )
                new_name = self._format_name(original_name, check_result)
                proxy_config["name"] = new_name
                self._rename_in_groups(yaml_data, original_name, new_name)
                checked += 1

                if checked % 5 == 0:
                    await self.async_atomic_save(yaml_data, file_path)

                status = "failed" if check_result.get("error") else "checked"
                result = self._public_result(
                    node_id,
                    original_name,
                    new_name,
                    status,
                    check_result,
                    primary_source,
                )
                if progress_cb:
                    await progress_cb(
                        checked,
                        total,
                        f"Result: {result['ip']} / {result['risk']}",
                        result,
                    )
        finally:
            if browser_source:
                await browser_source.stop()

        await self.async_atomic_save(yaml_data, file_path)
