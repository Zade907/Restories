# 🎬 Reddit Shorts Factory

An **end-to-end automated pipeline** that turns viral Reddit posts into YouTube Shorts with AI-generated narration, subtitles, and gameplay footage — fully hands-free.

```
Reddit Post → AI Script → TTS Narration → Subtitles → Video Composition → YouTube Upload
```

---

## ✨ Features

| Feature          | Tool                                   |
| ---------------- | -------------------------------------- |
| Reddit scraping  | RSS feeds + public JSON endpoints      |
| Hook generation  | Gemini / OpenRouter / Ollama           |
| Script rewriting | Gemini / OpenRouter / Ollama           |
| Text-to-speech   | Edge-TTS (Microsoft Neural) / Coqui    |
| Subtitles        | Script-derived timing + FFmpeg ASS/SRT |
| Background music | FFmpeg mix from `assets/music/`        |
| Video editing    | FFmpeg + MoviePy                       |
| Auto-upload      | YouTube Data API v3                    |
| Dashboard        | Streamlit                              |
| Scheduling       | APScheduler                            |
| Database         | SQLite                                 |

---

## 🚀 Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/yourname/reddit-shorts-factory
cd reddit-shorts-factory
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Install FFmpeg

**Ubuntu/Debian:**

```bash
sudo apt update && sudo apt install ffmpeg -y
```

**macOS:**

```bash
brew install ffmpeg
```

**Windows:**
Download from https://ffmpeg.org/download.html and add to PATH.

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your API keys:

```env
GEMINI_API_KEY=xxxxx        # Free at makersuite.google.com
OPENROUTER_API_KEY=xxxxx    # Free tier at openrouter.ai
```

Reddit authentication is no longer required for the MVP. The app now uses public RSS feeds and `.json` endpoints.

### 4. Add Gameplay Footage

Drop MP4 files into `assets/gameplay/`:

- Minecraft parkour
- Subway Surfers
- GTA driving
- Any gameplay in landscape orientation (auto-cropped to 9:16)

### 5. Run!

```bash
# Test scraper
python main.py scrape

# Generate videos (dry run, no upload)
python main.py run --max 3

# Generate + upload to YouTube
python main.py run --max 3 --upload

# Launch dashboard
python main.py dashboard
```

---

## 🔑 API Setup

### Reddit Access

The MVP uses public Reddit endpoints only:

- RSS feeds for discovery, such as `https://www.reddit.com/r/AITAH/.rss`
- Public `.json` endpoints for post metadata and content, such as `https://www.reddit.com/r/AITAH/top.json?t=day`

No Reddit app approval or OAuth credentials are required.

### Gemini API (Free Tier)

1. Go to https://makersuite.google.com/app/apikey
2. Create an API key
3. Free tier: 60 requests/minute, 1500/day

### OpenRouter (Fallback)

1. Go to https://openrouter.ai/keys
2. Create an account and generate a key
3. Free models available (Mistral 7B, etc.)

### Ollama (Local Fallback)

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull mistral

# Start server (runs automatically)
ollama serve
```

### YouTube API

1. Go to https://console.cloud.google.com/
2. Create a project → Enable **YouTube Data API v3**
3. Create **OAuth 2.0 credentials** (Desktop App)
4. Download `client_secrets.json` → place in `config/`
5. First run will open browser for OAuth login

---

## 📁 Project Structure

```
reddit-shorts-factory/
├── main.py                  # CLI entry point
├── pipeline.py              # Main orchestrator
├── requirements.txt
├── .env.example
│
├── config/
│   ├── config.json          # All settings
│   └── utils.py             # Logging, retry, config loading
│
├── scraper/
│   └── scraper.py           # Public Reddit scraping + scoring

├── services/
│   └── reddit_fetcher.py    # RSS / JSON fetcher with caching + throttling
│
├── summarizer/
│   └── summarizer.py        # LLM script generation + metadata
│
├── tts/
│   └── tts_engine.py        # Edge-TTS + Coqui TTS
│
├── subtitles/
│   └── subtitle_gen.py      # Script-timed SRT/ASS generation
│
├── video_editor/
│   └── editor.py            # FFmpeg video composition
│
├── uploader/
│   └── youtube_uploader.py  # YouTube OAuth + upload
│
├── scheduler/
│   └── scheduler.py         # APScheduler automation
│
├── dashboard/
│   └── app.py               # Streamlit dashboard
│
├── database/
│   └── db.py                # SQLite tracking layer
│
└── assets/
    └── gameplay/            # ← Drop your MP4 clips here
```

---

## ⚙️ Configuration

Edit `config/config.json` to customize:

```json
{
  "reddit": {
    "subreddits": ["AITAH", "confession", "tifu"],
    "min_upvotes": 1000,
    "min_comments": 50,
    "min_char_limit": 300,
    "max_char_limit": 3000,
    "post_limit": 25,
    "time_filter": "day"
  },
  "llm": {
    "provider_order": ["gemini", "openrouter", "ollama"]
  },
  "tts": {
    "voice": "en-US-AriaNeural",
    "rate": "+10%"
  },
  "reddit_fetcher": {
    "request_delay_seconds": 1.25,
    "cache_seconds": 3600,
    "max_retries": 4,
    "backoff_factor": 1.8,
    "json_limit": 25
  },
  "scheduler": {
    "enabled": true,
    "interval_hours": 6,
    "max_videos_per_run": 3
  }
}
```

---

## 🕐 Automation

### Linux/macOS (Cron)

```bash
# Run every 6 hours
crontab -e
0 */6 * * * cd /path/to/reddit-shorts-factory && /path/to/venv/bin/python main.py run --max 3 --upload >> logs/cron.log 2>&1
```

### Windows (Task Scheduler)

```
Action: python C:\path\to\reddit-shorts-factory\main.py run --max 3 --upload
Trigger: Daily, repeat every 6 hours
```

### APScheduler (Built-in)

```bash
python main.py schedule
```

---

## 🎛️ Dashboard

```bash
python main.py dashboard
# Opens at http://localhost:8501
```

Features:

- **Run Pipeline** with review mode and parallel workers
- **Review Queue** to edit title, description, and hook before upload
- **Preview Videos** directly in browser
- **Manual YouTube Upload** per video
- **Analytics** — runs, success rates, hook usage, and stage timings
- **Searchable History** for upload/review records

---

## 🔧 Troubleshooting

### "No posts scraped"

- Check that the subreddit exists and the public RSS/JSON endpoints are reachable
- Try increasing `min_upvotes` to lower threshold
- Check `time_filter` — use `"week"` for more results

### "All LLM providers failed"

- Verify `GEMINI_API_KEY` is set correctly
- Try Ollama: `ollama pull mistral && ollama serve`
- Check OpenRouter API key and free credits

### "FFmpeg not found"

- Ensure FFmpeg is installed and in your system PATH
- Test: `ffmpeg -version`

### "No gameplay clips found"

- Drop `.mp4` files into `assets/gameplay/`
- A black placeholder will be used if folder is empty

### TTS is slow

- Edge-TTS requires internet connection
- For offline: install Coqui TTS: `pip install TTS`

---

## 🗺️ Roadmap

- [ ] TikTok auto-upload
- [ ] AI-generated thumbnails (DALL-E / Stable Diffusion)
- [ ] Multiple YouTube channels
- [ ] Analytics dashboard with YouTube API stats
- [ ] Voice style per subreddit niche
- [ ] Batch processing queue
- [ ] Webhook notifications (Discord/Slack)

---

## 📄 License

MIT License — free to use and modify.

---

## ⚠️ Legal Notes

- Reddit content is rewritten by AI — never copied verbatim
- Ensure your use of Reddit data complies with Reddit's API Terms
- Gameplay footage should be royalty-free or owned by you
- YouTube upload complies with YouTube's Terms of Service
