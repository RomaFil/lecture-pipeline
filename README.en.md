# lecture-pipeline

> English summary. The full documentation of this repository is in Ukrainian: [README.md](README.md).

Record a lecture in OBS and do nothing else: the video is transcribed on a private VPS with faster-whisper, a local LLM names the subject and lesson type, the recording goes to YouTube as unlisted into the right playlist, and the raw transcript comes back to the notes vault. No paid APIs — classification runs on Ollama on the same CPU-only server.

Engineering points:
- sync over VPN with SHA-256 verification before deleting the local copy;
- transcription in a separate process, because 6 GB RAM cannot hold whisper large-v3 int8 and a 7B model at once — load, use, release;
- lesson type (lecture / practice / lab): without the enum, practices silently count as lectures;
- filenames carry the recording time, otherwise two classes of one subject on one day overwrite each other;
- a watchdog that complains in Telegram, including "today was a class day but fewer recordings arrived than expected";
- a 45-case test harness for the alerting itself: an alert nobody has seen fire is a hope.

Stack: faster-whisper, Ollama, ffmpeg, Python, YouTube Data API, PowerShell, bash, cron.
