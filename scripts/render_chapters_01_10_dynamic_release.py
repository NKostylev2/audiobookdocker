import os
import sys
import time
import subprocess
import soundfile as sf

BASE_DIR = r"E:\AI-Dubbing"
FFMPEG_EXE = os.path.join(BASE_DIR, "vendor", "ffmpeg", "bin", "ffmpeg.exe")
OUTPUT_DIR = os.path.join(BASE_DIR, "output", "wide_awake")

CHAPTERS = [
    (1, "Chapter_01_Рыжие Сдобные Зомби.wav", r"M:\Downloads\Глава 1_ Рыжие сдобные зомби.png"),
    (2, "Chapter_02_Чертовы Ньютоны.wav", r"M:\Downloads\Глава 2_ Малиновые печенья Ньютона.png"),
    (3, "Chapter_03_Сливочное Рандеву На Двоих.wav", r"M:\Downloads\Глава 3_ Сливочное рандеву на двоих.png"),
    (4, "Chapter_04_Панацея Из Арахисового масла.wav", r"M:\Downloads\Глава 4_ Панацея из арахисового масла.png"),
    (5, "Chapter_05_Маниакальная Мята.wav", r"M:\Downloads\Маниакальная мята_ шоколадное печенье.png"),
    (6, "Chapter_06_Карамельный Комфорт.wav", r"M:\Downloads\Карамельный комфорт_ уютная выпечка.png"),
    (7, "Chapter_07_Горько-Сладкие Ириски.wav", r"M:\Downloads\Горько-сладкие ириски при свечах.png"),
    (8, "Chapter_08_Сумерки с Тройной Карамелью.wav", r"M:\Downloads\Сумерки с тройной карамелью.png"),
    (9, "Chapter_09_ШОКоладная Крошка.wav", r"M:\Downloads\Шоколадная крошка_ глава 9.png"),
    (10, "Chapter_10_Гневные Ореховые Помадки.wav", r"M:\Downloads\Глава 10_ Ореховые шоколадные помадки.png"),
]

COMBINED_WAV = os.path.join(OUTPUT_DIR, "Wide_Awake_Chapters_01_10.wav")
OUTPUT_MP4 = os.path.join(OUTPUT_DIR, "Wide_Awake_Chapters_01_10.mp4")


def main():
    print("=" * 75)
    print("RENDERING FULL RELEASE: CHAPTERS 01-10 (DYNAMIC COVER TRANSITIONS)")
    print("=" * 75)

    if not os.path.exists(COMBINED_WAV):
        print(f"[Error] Missing audio file: {COMBINED_WAV}")
        sys.exit(1)

    info_comb = sf.info(COMBINED_WAV)
    total_audio_dur = info_comb.duration

    chapter_durs = []
    print("\nVerifying chapters and covers:")
    for num, wav_name, img_path in CHAPTERS:
        wav_path = os.path.join(OUTPUT_DIR, wav_name)
        if not os.path.exists(wav_path):
            print(f"[Error] Missing chapter audio: {wav_path}")
            sys.exit(1)
        if not os.path.exists(img_path):
            print(f"[Error] Missing chapter image: {img_path}")
            sys.exit(1)
        dur = sf.info(wav_path).duration
        chapter_durs.append(dur)
        print(f"  [OK] Ch {num:02d}: {dur/60:.2f}m | {os.path.basename(img_path)}")

    # Calculate segment durations for the 10 images:
    # Ch 1: d1 + 1.5s
    # Ch 2..9: d_i + 3.0s (1.5s before audio + d_i + 1.5s after audio)
    # Ch 10: 1.5s + d10
    segment_durs = []
    for i, dur in enumerate(chapter_durs):
        if i == 0:
            seg = dur + 1.5
        elif i == len(chapter_durs) - 1:
            seg = dur + 1.5
        else:
            seg = dur + 3.0
        segment_durs.append(seg)

    sum_seg_dur = sum(segment_durs)
    print(f"\nSum of segment durations: {sum_seg_dur:.2f}s ({sum_seg_dur/60:.2f} min)")
    print(f"Total audio duration:     {total_audio_dur:.2f}s ({total_audio_dur/60:.2f} min)")

    # Print exact visual switch timestamps
    curr_t = 0.0
    print("\nImage switch schedule:")
    for i, (num, _, img_path) in enumerate(CHAPTERS):
        hrs = int(curr_t // 3600)
        mins = int((curr_t % 3600) // 60)
        secs = int(curr_t % 60)
        print(f"  Ch {num:02d} Image switches at {hrs:02d}:{mins:02d}:{secs:02d} (duration: {segment_durs[i]:.2f}s) -> {os.path.basename(img_path)}")
        curr_t += segment_durs[i]

    # Build FFmpeg command
    cmd = [FFMPEG_EXE, "-y"]

    # Inputs 0..9: images
    for _, _, img_path in CHAPTERS:
        cmd.extend(["-i", img_path])

    # Input 10: combined wav
    cmd.extend(["-i", COMBINED_WAV])

    # Filter complex
    filter_parts = []
    concat_inputs = ""
    for i in range(10):
        filter_parts.append(
            f"[{i}:v]loop=loop=-1:size=1:start=0,scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p,fps=2,trim=duration={segment_durs[i]:.3f}[v{i}]"
        )
        concat_inputs += f"[v{i}]"

    filter_str = ";".join(filter_parts) + f";{concat_inputs}concat=n=10:v=1:a=0[v]"

    cmd.extend([
        "-filter_complex", filter_str,
        "-map", "[v]",
        "-map", "10:a",
        "-c:v", "h264_nvenc",
        "-b:v", "1500k",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        OUTPUT_MP4
    ])

    print("\nStarting FFmpeg render with NVENC...")
    t0 = time.time()
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    dur = time.time() - t0

    if res.returncode == 0 and os.path.exists(OUTPUT_MP4) and os.path.getsize(OUTPUT_MP4) > 100000000:
        size_mb = os.path.getsize(OUTPUT_MP4) / (1024 * 1024)
        print(f"\n-> SUCCESS in {dur:.1f}s ({dur/60:.2f} min)! Output file size: {size_mb:.2f} MB")
    else:
        print(f"\n-> FAILED! Returncode: {res.returncode}")
        print(res.stderr.decode("utf-8", errors="ignore")[-1000:])
        sys.exit(1)


if __name__ == "__main__":
    main()
