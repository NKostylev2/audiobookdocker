Minimal stand-ins for torch / torchaudio / whisper / cosyvoice. They let tests/test_generator_exit_codes.py
run scripts/generate_chapter_vid_voice.py end-to-end (as a subprocess) without a GPU, models or audio.
They implement only what the exercised code paths touch and must never be used outside the tests.
