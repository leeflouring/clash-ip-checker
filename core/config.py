import os

import yaml


DEFAULT_CONFIG = {
    "api_url": "http://127.0.0.1:9090",
    "check_url": "https://my.123169.xyz/v1/info",
    "request_timeout": 15,
    "mixed_port": 7890,
    "user_agent": "ClashVerge/2.4.3 Mihomo/1.19.17",
    "skip_keywords": [
        "剩余",
        "到期",
        "有效期",
        "重置",
        "官网",
        "网址",
        "更新",
        "公告",
        "建议",
    ],
    "max_age": 360,
    "max_queue_size": 10,
    "max_subscription_bytes": 5 * 1024 * 1024,
    "max_redirects": 3,
    "job_ttl": 3600,
    "max_jobs": 100,
    "source": "ping0",
    "fallback": True,
    "show_advanced_settings": False,
    "allow_private_subscriptions": False,
}


def parse_bool(value):
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("expected true or false")


ENV_OVERRIDES = {
    "MAX_QUEUE_SIZE": ("max_queue_size", int),
    "MAX_AGE": ("max_age", int),
    "REQUEST_TIMEOUT": ("request_timeout", int),
    "SOURCE": ("source", str),
    "FALLBACK": ("fallback", parse_bool),
    "MAX_SUBSCRIPTION_BYTES": ("max_subscription_bytes", int),
    "MAX_REDIRECTS": ("max_redirects", int),
    "JOB_TTL": ("job_ttl", int),
    "MAX_JOBS": ("max_jobs", int),
    "SHOW_ADVANCED_SETTINGS": ("show_advanced_settings", parse_bool),
    "ALLOW_PRIVATE_SUBSCRIPTIONS": ("allow_private_subscriptions", parse_bool),
}


class Config:
    def __init__(self):
        self._config = DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        config_path = os.getenv("CONFIG_PATH", "config.yaml")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as config_file:
                    user_config = yaml.safe_load(config_file) or {}
                if not isinstance(user_config, dict):
                    raise ValueError("configuration root must be a mapping")
                self._config.update(user_config)
                print(f"LOG: Loaded config from {config_path}", flush=True)
            except (OSError, ValueError, yaml.YAMLError) as error:
                print(
                    f"LOG: Failed to load config: {error}. Using defaults.",
                    flush=True,
                )
        else:
            print(
                f"LOG: Config file {config_path} not found. Using defaults.",
                flush=True,
            )

        for env_name, (config_name, converter) in ENV_OVERRIDES.items():
            value = os.getenv(env_name)
            if value is None:
                continue
            try:
                self._config[config_name] = converter(value)
            except ValueError as error:
                print(
                    f"LOG: Ignoring invalid {env_name}: {error}.",
                    flush=True,
                )

    def get(self, key, default=None):
        return self._config.get(key, default)

    @property
    def api_url(self):
        return self._config["api_url"]

    @property
    def check_url(self):
        return self._config["check_url"]

    @property
    def request_timeout(self):
        return int(self._config["request_timeout"])

    @property
    def user_agent(self):
        return self._config["user_agent"]

    @property
    def max_queue_size(self):
        return int(self._config["max_queue_size"])

    @property
    def skip_keywords(self):
        return list(self._config["skip_keywords"])

    @property
    def max_age(self):
        return int(self._config["max_age"])

    @property
    def mixed_port(self):
        return int(self._config["mixed_port"])

    @property
    def max_subscription_bytes(self):
        return int(self._config["max_subscription_bytes"])

    @property
    def max_redirects(self):
        return int(self._config["max_redirects"])

    @property
    def job_ttl(self):
        return int(self._config["job_ttl"])

    @property
    def max_jobs(self):
        return int(self._config["max_jobs"])

    @property
    def allow_private_subscriptions(self):
        return parse_bool(self._config["allow_private_subscriptions"])

    @property
    def api_token(self):
        return os.getenv("API_TOKEN", "")

    @property
    def source(self):
        return self._config["source"]

    @property
    def fallback(self):
        return parse_bool(self._config["fallback"])

    @property
    def show_advanced_settings(self):
        return parse_bool(self._config["show_advanced_settings"])


config = Config()
