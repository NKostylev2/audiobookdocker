"""Exit-code contract between the supervisor (run_audiobook.py) and its worker scripts.

Why this exists: before this module, worker scripts finished with exit code 0 on several
failure paths (missing text, missing voice, nothing synthesized), so the supervisor could
not tell "done" from "silently failed". Every worker now exits with one of these codes.
"""

EXIT_OK = 0

# generate_chapter_vid_voice.py
EXIT_TEXT_MISSING = 10      # chapter text file does not exist
EXIT_VOICE_MISSING = 11     # reference voice file does not exist
EXIT_NO_AUDIO = 12          # nothing was synthesized at all
EXIT_SEGMENTS_FAILED = 13   # >=1 segment could not be synthesized; master NOT assembled (cache kept for resume)

# verify_chapter_endings_gpu.py (pre-existing contract, unchanged)
EXIT_VERIFY_CANNOT_RUN = 1  # e.g. segments.json missing
EXIT_VERIFY_ISSUES = 2      # verification ran and found defects

# auto_repair_chapter.py
EXIT_REPAIR_INCOMPLETE = 4  # at least one flagged segment could not be repaired

# run_audiobook.py (its own process exit status)
EXIT_BATCH_FAILED = 1       # at least one chapter failed at a pipeline stage
EXIT_BATCH_NEEDS_REVIEW = 3 # no stage failed, but some chapters still have unresolved defects
