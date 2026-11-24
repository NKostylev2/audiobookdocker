# Развертывание AI-Dubbing в Docker на GPU-сервере

Данная инструкция позволяет в пару команд поднять весь пайплайн генерации аудиокниг (CosyVoice 3 + Whisper GPU) на арендованном сервере с видеокартой NVIDIA (Vast.ai, RunPod, Hetzner, Lambda Labs и др.).

Все зависимости, CUDA-драйверы, PyTorch и модели скачиваются **автоматически** при первом запуске.

---

## 1. Требования к арендованному серверу

1. **ОС:** Linux (Ubuntu 22.04 / 24.04 или Debian).
2. **GPU:** NVIDIA GPU с VRAM от 8 GB (RTX 3070, 3080, 3090, 4090, A4000, A5000, A100 и т.д.).
3. **Установленные NVIDIA Drivers и Docker:**
   Проверьте наличие GPU:
   ```bash
   nvidia-smi
   ```
4. **NVIDIA Container Toolkit:**
   Если вы арендовали обычный VPS (а не готовый шаблон PyTorch в RunPod/Vast.ai), убедитесь, что установлен NVIDIA Container Toolkit:
   ```bash
   # Проверка
   docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
   ```
   *(Если toolkit не установлен, поставьте его по инструкции: `sudo apt-get install -y nvidia-container-toolkit && sudo systemctl restart docker`)*.

---

## 2. Клонирование и первый запуск

Склонируйте проект на сервер:
```bash
git clone --recursive <URL_ВАШЕГО_РЕПОЗИТОРИЯ> AI-Dubbing
cd AI-Dubbing
```

### Запуск интерактивного меню (рекомендуется):
```bash
docker compose run --rm audiobook
```
> **Что произойдет при первом запуске:**
> 1. Соберется Docker-образ на базе официального `pytorch:2.3.1-cuda12.1` со всеми аудио-библиотеками (`ffmpeg`, `sox`, `libsndfile`, `transformers`, `onnxruntime-gpu` и др.).
> 2. Скрипт bootstrap автоматически скачает:
>    - Веса **CosyVoice 3** (`FunAudioLLM/Fun-CosyVoice3-0.5B-2512` ~1.8 ГБ) через ModelScope / Hugging Face.
>    - Модель **Whisper base** для GPU/CPU верификации окончания фраз.
> 3. Модели сохраняются в постоянные Docker-тома (`cosyvoice_models`, `whisper_cache`), поэтому при повторных запусках ничего заново не скачивается.
> 4. Откроется привычное консольное меню `run_audiobook.py`.

---

## 3. Команды для работы

### Проверить статус всех глав:
```bash
docker compose run --rm audiobook python3 run_audiobook.py --status
```

### Сгенерировать конкретную главу:
```bash
docker compose run --rm audiobook python3 run_audiobook.py output/Potter/chapters_ru/Книга3Пролог.txt
```

### Сгенерировать все ожидающие главы подряд:
```bash
docker compose run --rm audiobook python3 run_audiobook.py --all
```

### Только верификация окончаний на GPU:
```bash
docker compose run --rm audiobook python3 run_audiobook.py --verify-only output/Potter/chapters_ru/Книга3Глава01.txt
```

### Только самоисцеление (auto-repair):
```bash
docker compose run --rm audiobook python3 run_audiobook.py --repair-only output/Potter/chapters_ru/Книга3Глава01.txt
```

### Открыть Bash-терминал внутри контейнера:
```bash
docker compose run --rm audiobook bash
```

---

## 4. Настройка Telegram-уведомлений (опционально)

Создайте в папке проекта файл `.env`:
```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=373648438
# Опционально прокси (если сервер не имеет прямого доступа к API Telegram):
# TELEGRAM_PROXY=socks5://user:pass@host:port
```
Контейнер автоматически подхватит эти переменные и будет присылать готовые аудиофайлы и статусы в Telegram.

---

## 5. Где сохраняются результаты?

Папка `./output` смонтирована с хост-системы (`bind mount`). Все готовые аудиокниги (`.wav`, `.mp3`) и метаданные сохраняются напрямую в папку `output/` на вашем арендованном сервере.

Вы можете скачать их на домашний ПК через:
- **SCP / SFTP:** `scp -r user@server_ip:/path/to/AI-Dubbing/output/Книга_3_Узник_Азкабана/Глава_01 ./`
- **Rsync:** `rsync -avzP user@server_ip:/path/to/AI-Dubbing/output/ ./local_output/`
- Или напрямую из Telegram, если настроен бот.

---

## 6. Запуск без Docker Compose (чистый docker run)

Если на хосте нет `docker compose`, можно собрать и запустить напрямую через `docker`:

```bash
# 1. Запуск интерактивного режима с пробросом GPU и папки output
docker run --gpus all --ipc=host -it --rm \
  -v $(pwd)/output:/app/output \
  -v $(pwd)/work:/app/work \
  -v $(pwd)/voices:/app/voices \
  -v ai_dubbing_models:/app/CosyVoice/pretrained_models \
  -v ai_dubbing_whisper:/root/.cache/whisper \
  nkostylev/ai-dubbing:latest
```
