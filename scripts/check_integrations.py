"""Report enabled integrations and exact redirect URLs without exposing secrets."""
from os import getenv

from dean.access_config import AccessConfig, LABELS, SOCIAL
from dean.ai import RouterAI
from dean.oauth import callback


def main():
    settings = AccessConfig.from_env()
    origin = getenv("DEAN_PUBLIC_ORIGIN", "").rstrip("/")
    for provider in SOCIAL:
        state = "настроен" if settings.enabled(provider, origin) else "ожидает настройки"
        print(f"{LABELS[provider]} · {state}")
        if origin:
            print(callback(origin, provider))
    model = RouterAI.from_env()
    state = "настроен" if model.provider_name == "DeepSeek" and model.enabled else "ожидает настройки"
    print(f"DeepSeek · {state}")


if __name__ == "__main__":
    main()
