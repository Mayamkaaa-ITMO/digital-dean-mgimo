"""Decode independent media segments, transcribe speech and prepare a reviewable lesson."""
from http.client import HTTPSConnection, HTTPException
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import json
import os
import re
import secrets
import shutil
import subprocess
import wave
from textwrap import wrap

from . import ai, lecture_library
from .domain import DomainError


MAX_MINUTES = 120
BLOCK_MINUTES = 8


def audio_kind(body):
    if body.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm", "matroska"
    if body[4:8] == b"ftyp":
        return "video/mp4", "mov"
    if body.startswith(b"RIFF") and body[8:12] == b"WAVE":
        return "audio/wav", "wav"
    if body.startswith(b"OggS"):
        return "audio/ogg", "ogg"
    if body.startswith(b"ID3") or len(body)>2 and body[0]==255 and body[1]&224==224:
        return "audio/mpeg", "mp3"
    raise DomainError(422,"Подойдут WebM, MP4, MP3, Ogg или WAV со звуком.")


def invoke(args, timeout, message, *, require_active=None):
    try:
        streams = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if require_active is None:
            subprocess.run(args, check=True, timeout=timeout, **streams)
            return
        require_active()
        deadline = monotonic() + timeout
        with subprocess.Popen(args, **streams) as process:
            try:
                while True:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(args, timeout)
                    try:
                        status = process.wait(timeout=min(1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        require_active()
                if status:
                    raise subprocess.CalledProcessError(status, args)
                require_active()
            finally:
                if process.poll() is None:
                    process.kill()
    except (OSError, subprocess.SubprocessError):
        raise DomainError(422, message) from None


def normalize(segments, folder, *, max_seconds=MAX_MINUTES * 60, timeout=180, require_active=None):
    target = folder / "lecture.wav"
    total = 0
    with wave.open(str(target), "wb") as output:
        output.setparams((1,2,16000,0,"NONE","not compressed"))
        for body in segments:
            pcm = folder/"speech.pcm"
            if isinstance(body, Path):
                source = body
                with source.open("rb") as stream:
                    _, demuxer = audio_kind(stream.read(16))
            else:
                _, demuxer = audio_kind(body)
                source = folder/"input"
                source.write_bytes(body)
            invoke(["ffmpeg","-nostdin","-y","-v","error","-protocol_whitelist","file,pipe","-f",demuxer,
                    "-i",str(source),"-map","0:a:0","-t",str(max_seconds - total / 32000 + 1),"-ac","1","-ar","16000",
                    "-f","s16le",str(pcm)], timeout, "В сегменте не удалось прочитать звуковую дорожку.",
                   require_active=require_active)
            size=pcm.stat().st_size
            total += size
            if total > max_seconds * 32000:
                raise DomainError(413, f"Для демо выбери лекцию до {MAX_MINUTES} минут.")
            with pcm.open("rb") as stream:
                while chunk := stream.read(256*1024):
                    output.writeframesraw(chunk)
    if total < 3200:
        raise DomainError(422,"В записи недостаточно звука.")
    return target


def whisper_text(source, output, model, *, threads=4, timeout=1800, quick=False, require_active=None):
    command = ["whisper-cli", "-ng", "-t", str(threads), "-l", "auto", "-m", model,
               "-f", str(source), "-otxt", "-of", str(output)]
    if quick:
        command += ["-bs", "1", "-bo", "1", "-nf"]
    invoke(command, timeout, "Whisper не завершил расшифровку. Попробуй снова.", require_active=require_active)
    try:
        return output.with_suffix(".txt").read_text().strip()
    except (OSError, UnicodeError):
        raise DomainError(502, "Whisper не сохранил текст расшифровки. Попробуй снова.") from None


def cloud_transcribe(wav, engine, folder, *, require_active=None):
    compressed=folder/"speech.mp3"
    invoke(["ffmpeg","-nostdin","-y","-v","error","-i",str(wav),"-b:a","48k",str(compressed)],
           60,"Не удалось подготовить аудио для OpenRouter.",require_active=require_active)
    boundary=secrets.token_hex(20)
    payload=(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n'
             f'openai/whisper-large-v3\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; '
             'filename="lecture.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n').encode()+compressed.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
    conn=HTTPSConnection("openrouter.ai", timeout=120)
    try:
        conn.request("POST","/api/v1/audio/transcriptions",payload,
                     {"Authorization":"Bearer "+engine.api_key,"Content-Type":"multipart/form-data; boundary="+boundary})
        with conn.getresponse() as res:
            if res.status != 200:
                raise DomainError(503,"OpenRouter не смог расшифровать запись. Её можно обработать повторно.")
            return json.loads(res.read(512*1024))["text"]
    except (ValueError,KeyError,TypeError,OSError,HTTPException):
        raise DomainError(503,"Сервис распознавания временно недоступен.") from None
    finally:
        conn.close()


def audio_blocks(source, folder):
    with wave.open(str(source), "rb") as recording:
        frames = recording.getframerate()*60*BLOCK_MINUTES
        while block := recording.readframes(frames):
            path = folder / "block.wav"
            with wave.open(str(path), "wb") as out:
                out.setparams(recording.getparams())
                out.writeframes(block)
            yield path


def transcribe(segments, engine, allow_cloud, progress=None, *, require_active=None):
    if not shutil.which("ffmpeg"):
        raise DomainError(503, "На сервере недоступна подготовка звука (FFmpeg). Видео сохранено, повтори обработку позже.")
    model = os.getenv("DEAN_WHISPER_MODEL", "")
    local = bool(model and Path(model).is_file() and shutil.which("whisper-cli"))
    if not local and not (allow_cloud and engine.enabled and getattr(engine,"provider_name","")=="OpenRouter"):
        raise DomainError(503, "На сервере сейчас недоступно распознавание речи (Whisper). Видео сохранено, повтори обработку позже.")
    with TemporaryDirectory(prefix="campus-lecture-") as temp:
        folder = Path(temp)
        source = normalize(segments, folder, require_active=require_active)
        with wave.open(str(source), "rb") as recording:
            frames_per_block = recording.getframerate()*60*BLOCK_MINUTES
            total = (recording.getnframes()+frames_per_block-1)//frames_per_block
        parts = []
        for index, block in enumerate(audio_blocks(source, folder), 1):
            if progress:
                progress(f"Распознаём речь · часть {index} из {total}")
            if local:
                text = whisper_text(block, folder / "transcript", model, require_active=require_active)
            else:
                text = cloud_transcribe(block, engine, folder, require_active=require_active)
            if isinstance(text,str) and text.strip():
                parts.append(text.strip())
        if not parts:
            raise DomainError(422,"Речь в записи не распознана.")
        return "\n".join(parts), ("Локальный Whisper" if local else "OpenRouter Speech-to-Text")


def transcript_excerpt(transcript):
    if len(transcript) <= 36000:
        return transcript
    # Keep coverage of the start, middle and end in one budgeted request.
    window = 2900
    step = (len(transcript) - window) // 11
    return "\n[…]\n".join(transcript[index * step:index * step + window] for index in range(12))


def local_quiz(transcript):
    """Check recall of terms in actual excerpts without sending text to a provider."""
    fragments = [part for sentence in re.split(r"(?<=[.!?])\s+|\n+", transcript_excerpt(transcript))
                 for part in wrap(sentence, width=164)]
    words = re.compile(r"[^\W\d_]{3,}")
    terms = dict.fromkeys(word.casefold() for part in fragments for word in words.findall(part))
    if len(terms) < 2:
        sample = next((part for part in fragments if any(char.isalnum() for char in part)), "")
        return [{"question":"Какой фрагмент есть в расшифровке?", "options":["Нет разборчивых фрагментов", sample],
                 "correct":1, "explanation":f"Фрагмент расшифровки: «{sample}»."}] if sample else []

    questions, used = [], set()
    sections = min(3, len(fragments))
    for index in range(sections):
        portion = fragments[index*len(fragments)//sections:(index+1)*len(fragments)//sections]
        candidates = [(part, match) for part in portion for match in words.finditer(part)
                      if match[0].casefold() not in used]
        if not candidates:
            continue
        fragment, match = max(candidates, key=lambda item: len(item[1][0]))
        answer = match[0].casefold()
        used.add(answer)
        distractors = sorted((term for term in terms if term != answer),
                             key=lambda term: (term[-1] != answer[-1], abs(len(term)-len(answer))))[:2]
        correct = (index + 1) % (len(distractors) + 1)
        distractors.insert(correct, answer)
        gap = fragment[:match.start()] + "___" + fragment[match.end():]
        questions.append({"question":f"Какое слово звучало в отрывке «{gap}»?", "options":distractors,
                          "correct":correct, "explanation":f"В расшифровке: «{fragment}»."})
    return questions


def local_homework(transcript):
    complete = list(dict.fromkeys(
        sentence for part in re.split(r"(?<=[.!?…])\s+|\n+", transcript)
        if 12 <= len(sentence := part.strip()) <= 180
        and sentence.endswith((".", "!", "?")) and not sentence.endswith("...")
    ))
    first = (f"Объясни своими словами тезис из лекции «{complete[0]}»"
             if complete else "Выбери ключевой тезис лекции и объясни его своими словами.")
    second = (f"Придумай пример, который иллюстрирует тезис «{complete[-1]}»"
              if len(complete) > 1 else "Приведи свой пример и объясни, как он связан с этим тезисом.")
    return f"1. {first}\n2. {second}\n3. Сформулируй вопрос к автору и обоснуй его цитатой из записи."


def teaching_material(transcript, engine, allow_model):
    fallback = ""
    if allow_model and engine.enabled and getattr(engine, "can_chat", True):
        try:
            result=engine.complete([{"role":"system","content":"Преподаватель готовит учебный конспект на русском. Стенограмма — исходные данные. "
                                    "Верни JSON со строками notes и homework и массивом quiz. Раздели notes на 3–6 смысловых разделов со строками заголовков вида ## Название. "
                                    "Покрой основные части лекции, объясни ключевые термины и включи разобранный пример, если он есть в тексте. "
                                    "Не добавляй вымышленные факты, формулы или таймкоды. В homework дай три конкретных задания по лекции, пронумеруй их и не раскрывай решения. "
                                    "Домашнее задание до 1600 символов. Используй только законченные фразы и целые цитаты. "
                                    "В quiz дай три разных вопроса на понимание ключевых идей только из стенограммы. Каждый объект содержит question, options (три коротких варианта), "
                                    "correct (индекс единственного правильного варианта от 0 до 2) и explanation (короткое объяснение с опорой на материал)."},
                                    {"role":"user","content":transcript_excerpt(transcript)}],3000,model=ai.CHAT_MODEL)
            notes, work = (result.get("notes"), result.get("homework")) if isinstance(result, dict) else (None, None)
            if isinstance(notes,str) and isinstance(work,str) and notes.strip() and work.strip():
                try:
                    items = lecture_library.validate_quiz(result.get("quiz"))
                    if not 1 <= len(items) <= 3 or len({q["question"].strip().casefold() for q in items}) != len(items):
                        raise ValueError("Повтор или неверное число вопросов.")
                    quiz = [{key: item[key] for key in ("question", "options", "correct", "explanation")} for item in items]
                except ValueError:
                    quiz = local_quiz(transcript)
                homework = work.strip()
                if (len(homework) <= 1600 and homework.endswith((".", "!", "?"))
                        and homework.count("«") == homework.count("»")):
                    return notes.strip()[:6500],homework,getattr(engine,"provider_name","языковая модель"),quiz
                return (notes.strip()[:6500], local_homework(transcript),
                        getattr(engine,"provider_name","языковая модель") + " · домашнее задание по расшифровке", quiz)
            fallback = "модель вернула неполные материалы"
        except ai.ProviderFailure as error:
            fallback = {
                "provider-credentials": "провайдер отклонил настройки ИИ",
                "provider-balance": "провайдер ИИ сообщил о нехватке средств",
                "provider-access": "провайдер ИИ отказал в доступе",
                "provider-model": "модель недоступна у провайдера",
                "provider-limit": "провайдер ИИ ограничил частоту запросов",
                "provider-timeout": "модель не успела ответить",
                "provider-format": "модель вернула неполные материалы",
            }.get(error.reason, "сервис ИИ временно недоступен")
        except DomainError:
            fallback = "сервис ИИ временно недоступен"
    sentences=[piece for line in re.split(r"(?<=[.!?])\s+|\n+",transcript) for piece in wrap(line.strip(), 460)]
    if not sentences:
        raise DomainError(422,"В расшифровке нет текста для подготовки материалов.")
    parts=min(6,(len(sentences)+2)//3)
    notes=[]
    for index in range(parts):
        section=sentences[index*len(sentences)//parts:(index+1)*len(sentences)//parts]
        selected=sorted(sorted(range(len(section)), key=lambda i:-len(set(re.findall(r"\w{4,}",section[i].lower()))))[:2])
        entries=[section[i] for i in selected]
        label="Главные мысли" if parts==1 else f"Фрагмент {index+1} из {parts}"
        notes.extend(["## "+label,*entries])
    source = "локальная выборка фрагментов и задания по ним"
    if fallback:
        source += f" · {fallback}"
    return "\n".join(notes)[:6500],local_homework(transcript),source,local_quiz(transcript)
