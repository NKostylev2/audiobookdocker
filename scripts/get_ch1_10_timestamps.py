import os
import soundfile as sf

output_dir = r"E:\AI-Dubbing\output\wide_awake"
chapters = [
    "Chapter_01_Рыжие Сдобные Зомби.wav",
    "Chapter_02_Чертовы Ньютоны.wav",
    "Chapter_03_Сливочное Рандеву На Двоих.wav",
    "Chapter_04_Панацея Из Арахисового масла.wav",
    "Chapter_05_Маниакальная Мята.wav",
    "Chapter_06_Карамельный Комфорт.wav",
    "Chapter_07_Горько-Сладкие Ириски.wav",
    "Chapter_08_Сумерки с Тройной Карамелью.wav",
    "Chapter_09_ШОКоладная Крошка.wav",
    "Chapter_10_Гневные Ореховые Помадки.wav",
]

current_t = 0.0
pause = 3.0
print("TIMESTAMPS (NEW AUDIO 29.10):")
for i, name in enumerate(chapters, 1):
    path = os.path.join(output_dir, name)
    info = sf.info(path)
    hrs = int(current_t // 3600)
    mins = int((current_t % 3600) // 60)
    secs = int(current_t % 60)
    title = name.replace(".wav", "").split("_", 2)[2]
    print(f"Глава {i:02d}: {hrs:02d}:{mins:02d}:{secs:02d} — {title} ({info.duration/60:.2f} мин)")
    current_t += info.duration + pause
current_t -= pause
hrs = int(current_t // 3600)
mins = int((current_t % 3600) // 60)
secs = int(current_t % 60)
print(f"Итоговая длительность: {hrs:02d}:{mins:02d}:{secs:02d} ({current_t:.2f} сек)")
