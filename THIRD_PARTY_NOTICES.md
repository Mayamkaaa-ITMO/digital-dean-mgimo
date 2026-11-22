# Сторонние компоненты

| Компонент | Применение | Лицензия и источник |
| --- | --- | --- |
| Noto Sans Regular 2.015 | Встроенный TrueType-шрифт в демонстрационных PDF | SIL Open Font License 1.1. Copyright 2022 The Noto Project Authors. Полный текст в dean/assets/NotoSans-OFL.txt. [Исходный проект](https://github.com/notofonts/latin-greek-cyrillic) |
| Python | Стандартная библиотека WSGI, SQLite, scrypt и обработка дат | [PSF License](https://docs.python.org/3/license.html) |
| Gunicorn 27.0.1 | Сервер для последующего развёртывания | [MIT](https://github.com/benoitc/gunicorn/blob/master/LICENSE) |
| Caddy | Обратный прокси и HTTPS в конфигурации развёртывания | [Apache 2.0](https://github.com/caddyserver/caddy/blob/master/LICENSE) |

Noto Sans скопирован из установленного пакета шрифтов, его name-таблица содержит copyright и ссылку на SIL OFL. Файл включён без изменений. Генератор PDF для простых текстовых документов находится в dean/pdf.py и использует встроенный шрифт с Unicode mapping. Демонстрационные PDF не содержат официальных печатей, логотипов и подписей вуза.

## QR Code generator library

Файл `dean/vendor/qrcodegen.py` из выпуска 1.8.0 Project Nayuki. Лицензия MIT и исходный copyright сохранены в заголовке файла. Blob 5d84c39ef49cc92b60f029e53ca7d158b9f7da19, SHA-256 b089855caf16185c61421ea4927c1b213cf9468940d71fa8ab11ef83662dcc84. [Поставляемый исходник](https://github.com/nayuki/QR-Code-generator/blob/v1.8.0/python/qrcodegen.py)

## Nunito в календаре Raspay

Кириллица и латиница поставляются в WOFF2 из публичной версии Raspay по SIL Open Font License 1.1. Текст лицензии и copyright лежат в `dean/assets/Nunito-OFL.txt`. [Исходный проект Nunito](https://github.com/googlefonts/nunito) · [Raspay](https://raspay.up.railway.app/)

В Railway используются те же локальные файлы Nunito. Контурные знаки навигации сделаны на основе Lucide. Текст лицензии находится в `dean/assets/Lucide-LICENSE.txt`. [Lucide](https://lucide.dev/license)

Декоративное фото `dean/static/railway-campus.jpg` взято из макета проекта. Это сгенерированное изображение вымышленного кампуса без университетских эмблем.

Шрифт Manrope для интерфейса поставляется по SIL Open Font License 1.1. [Текст лицензии](contracts/Manrope-OFL.txt)

## Учебная речь

Короткий фрагмент инаугурационной речи Джона Кеннеди (1961) перекодирован в MP3 из тестовой записи jfk.wav проекта whisper.cpp. [Исходная запись](https://github.com/ggml-org/whisper.cpp/blob/master/samples/jfk.wav)

## Локальный счётчик людей

Для инференса используется отдельно установленная библиотека Ultralytics и официальная модель YOLO11n COCO. Установка и веса располагаются вне распространяемого архива. Контрольная фотография берётся из установленного пакета. [Ultralytics](https://github.com/ultralytics/ultralytics) · [модель](https://github.com/ultralytics/assets/releases/tag/v8.3.0)

Для приёмки использован фрагмент открытого демонстрационного ролика MMTracking с пешеходами. Исходный ролик не входит в пакет. [Исходная запись](https://github.com/open-mmlab/mmtracking/blob/master/demo/demo.mp4)

## Видеотека

*Intro to the Semantic Web*, Manu Sporny. Условия исходной копии — Creative Commons Attribution 3.0. Выполнены перекодирование в MP4, распознавание речи и перевод субтитров. Русская дорожка распространяется на условиях [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Конспект и задания составлены для этого приложения. [YouTube](https://www.youtube.com/watch?v=OGg8A2zfWKg) · [Файл и условия лицензии](https://commons.wikimedia.org/wiki/File:Intro_to_the_Semantic_Web.webm)
