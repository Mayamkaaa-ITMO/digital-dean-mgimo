"""Verify the chosen AI endpoint with one short JSON request."""
from dean.ai import CHAT_MODEL, RouterAI
from dean.domain import DomainError


def main():
    engine=RouterAI.from_env()
    if not engine.enabled:
        raise SystemExit("Задай ключ нужного провайдера.")
    try:
        reply=engine.complete([{"role":"user","content":'Ответь одним JSON. Верни {"result": 7}.'}],80,model=CHAT_MODEL)
    except DomainError as error:
        raise SystemExit(f"{engine.provider_name}: HTTP {error.status}. {error.message}") from None
    if reply.get("result")!=7:
        raise SystemExit(f"{engine.provider_name}: получен JSON неожиданного формата.")
    print(f"{engine.provider_name}: модель вернула проверочный JSON.")


if __name__=="__main__":
    main()
