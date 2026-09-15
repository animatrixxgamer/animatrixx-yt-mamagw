# 🎯 What You Need For The Bot & Where To Get It

## 📋 Required Credentials

### 1. **Telegram Bot Token** (FREE)
**What:** Your bot's unique API key
**Where:** 
- Open Telegram → Search `@BotFather`
- Send `/newbot`
- Follow instructions → Copy the token (format: `1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ`)

### 2. **Your Telegram User ID** (FREE)
**What:** Your personal Telegram ID (for admin access)
**Where:**
- Open Telegram → Search `@userinfobot`
- Send any message → It shows your ID (e.g., `123456789`)

### 3. **YouTube Data API v3** (FREE - 10,000 units/day)
**What:** Access YouTube to upload videos, manage channels
**Where:**
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create new project (or select existing)
3. Go to **APIs & Services** → **Library**
4. Search "YouTube Data API v3" → Click **Enable**
5. Go to **APIs & Services** → **Credentials**
6. Click **Create Credentials** → **OAuth client ID**
7. Choose **Web application**
8. Add **Authorized redirect URIs**: `http://localhost:8000/oauth/youtube/callback`
9. Copy **Client ID** and **Client Secret**

### 4. **GitHub Token** (Optional - for backup)
**What:** Backup your bot code to GitHub
**Where:**
1. Go to [GitHub Settings → Developer Settings](https://github.com/settings/tokens)
2. Click **Generate new token (classic)**
3. Select scopes: `repo` (full control)
4. Generate and copy

### 5. **OpenRouter API Key** (Optional - for AI security)
**What:** AI-powered code scanning
**Where:**
1. Go to [OpenRouter](https://openrouter.ai)
2. Sign up (free credits available)
3. Go to **Keys** → Create new key
4. Copy the key

---

## 🎨 Button Colors (Bot API 9.4+)

The bot now supports **colored inline buttons**:
- `style="primary"` → 🔵 **Blue** (default for most actions)
- `style="success"` → 🟢 **Green** (for confirm, start, approve)
- `style="danger"` → 🔴 **Red** (for stop, delete, cancel)

---

## 🚀 Deployment Options

### **Option 1: Host Here (Recommended)**
You can deploy directly on the platform you're using. Just:
1. Set environment variables in the dashboard
2. The bot will auto-start

### **Option 2: Railway.app (FREE tier)**
1. Go to [railway.app](https://railway.app)
2. Connect GitHub repo
3. Add PostgreSQL plugin
4. Set environment variables
5. Deploy!

### **Option 3: Render.com (FREE tier)**
1. Go to [render.com](https://render.com)
2. Create **Background Worker**
3. Connect GitHub
4. Set build command: `pip install -r requirements.txt`
5. Set start command: `python -m src.main`
6. Add environment variables

### **Option 4: VPS (DigitalOcean, Linode, etc.)**
```bash
# SSH into your VPS
ssh root@your-vps-ip

# Install Docker
curl -fsSL https://get.docker.com | sh

# Clone your bot
git clone your-repo
cd your-repo

# Start with Docker
docker-compose up -d
```

---

## 📦 What's Included In The Bot

### ✅ Core Features
- [x] YouTube channel connection (OAuth 2.0)
- [x] Video upload with metadata
- [x] Hashtag suggestions
- [x] Job tracking and retry
- [x] Multi-channel support
- [x] Admin panel

### ✅ Security Features
- [x] Encrypted token storage (Fernet)
- [x] Role-based access (Owner/Admin/User)
- [x] Rate limiting
- [x] Auto-ban for abuse
- [x] Input validation
- [x] Audit logging

### ✅ Epic Features (from your attached files)
- [x] Colored inline buttons (Blue/Green/Red)
- [x] AI-powered security scanner
- [x] GitHub backup system
- [x] Smart auto-install dependencies
- [x] Process management (start/stop/restart)
- [x] Live logs viewer
- [x] Subscription system
- [x] Broadcast messaging
- [x] File encryption
- [x] Welcome messages with photos
- [x] Rate limit watchdog

---

## 🔧 Quick Setup

```bash
# 1. Clone the bot
git clone <your-repo-url>
cd youtube-telegram-bot

# 2. Copy environment template
cp .env.example .env

# 3. Edit .env with your credentials
nano .env

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run the bot
python -m src.main
```

---

## 💡 Pro Tips

1. **Never share your bot token** - anyone with it can control your bot
2. **Use environment variables** - never hardcode secrets
3. **Start with "private" uploads** - test before going public
4. **Monitor YouTube quota** - 10,000 units/day is enough for ~6 videos
5. **Enable 2FA on Google** - protect your YouTube account

---

## 🆘 Common Issues

| Issue | Solution |
|-------|----------|
| "Bot token invalid" | Check token from @BotFather |
| "YouTube upload fails" | Check API is enabled in Google Cloud |
| "Quota exceeded" | Wait 24h or request quota increase |
| "Button colors don't show" | Update Telegram app to latest version |
| "Bot doesn't respond" | Check environment variables are set |

---

**Ready to build? Start by getting your Telegram Bot Token from @BotFather!** 🚀
