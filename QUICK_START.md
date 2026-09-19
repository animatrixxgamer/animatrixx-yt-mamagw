# 🚀 Quick Start Guide

## What You Need (Get These First!)

### 1. Telegram Bot Token (FREE)
```
1. Open Telegram
2. Search: @BotFather
3. Send: /newbot
4. Follow instructions
5. Copy your token (like: 1234567890:ABCdefGHI...)
```

### 2. Your Telegram User ID (FREE)
```
1. Open Telegram
2. Search: @userinfobot
3. Send any message
4. Copy your numeric ID (like: 123456789)
```

### 3. YouTube API (FREE - 10,000 units/day)
```
1. Go to: console.cloud.google.com
2. Create project
3. APIs & Services → Library → Enable "YouTube Data API v3"
4. APIs & Services → Credentials → Create OAuth Client ID
5. Type: Web application
6. Add redirect: http://localhost:8000/oauth/youtube/callback
7. Copy Client ID and Client Secret
```

---

## 🎨 Button Colors

The bot has **colored inline buttons** (Bot API 9.4+):
- 🔵 **Blue** = Primary actions (Edit, View, Select)
- 🟢 **Green** = Positive actions (Start, Confirm, Approve)
- 🔴 **Red** = Negative actions (Stop, Delete, Cancel)

---

## 📦 Where To Host

### Option 1: Here (Easiest)
Just set environment variables in your dashboard!

### Option 2: Railway.app (FREE)
```
1. Go to railway.app
2. Connect GitHub
3. Add PostgreSQL
4. Set env vars
5. Deploy!
```

### Option 3: Render.com (FREE)
```
1. Go to render.com
2. Background Worker
3. Build: pip install -r requirements.txt
4. Start: python -m src.main
```

### Option 4: Your Own VPS
```bash
git clone your-repo
cd your-repo
docker-compose up -d
```

---

## ⚡ Features Included

### YouTube Features
- ✅ Connect multiple YouTube channels
- ✅ Upload videos with metadata
- ✅ Set title, description, tags, hashtags
- ✅ Privacy settings (public/private/unlisted)
- ✅ Schedule uploads
- ✅ Thumbnail upload
- ✅ Playlist assignment
- ✅ Quota monitoring

### Security Features
- ✅ Encrypted token storage
- ✅ Role-based access (Owner/Admin/User)
- ✅ Rate limiting
- ✅ Auto-ban for abuse
- ✅ AI-powered code scanning
- ✅ Audit logging

### Epic Features
- ✅ Colored buttons (Blue/Green/Red)
- ✅ GitHub backup system
- ✅ Auto-install dependencies
- ✅ Live logs viewer
- ✅ Broadcast messaging
- ✅ Subscription system
- ✅ Welcome messages

---

## 🔧 Setup Steps

### Step 1: Clone & Configure
```bash
git clone <your-repo>
cd youtube-telegram-bot
cp .env.example .env
```

### Step 2: Edit .env File
```env
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_AUTHORIZED_USER_IDS=your_user_id
YOUTUBE_CLIENT_ID=your_client_id
YOUTUBE_CLIENT_SECRET=your_client_secret
TOKEN_ENCRYPTION_KEY=any_random_string_here
```

### Step 3: Install & Run
```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot
python -m src.main
```

### Step 4: Test It!
1. Open Telegram
2. Find your bot
3. Send: `/start`
4. Click "Connect YouTube"
5. Authorize with Google
6. Send a video
7. Configure metadata
8. Upload!

---

## ❓ Common Questions

**Q: Button colors not showing?**
A: Update your Telegram app to latest version

**Q: YouTube upload fails?**
A: Check API is enabled in Google Cloud Console

**Q: Quota exceeded?**
A: Wait 24h (resets at midnight Pacific) or request increase

**Q: Bot doesn't respond?**
A: Check environment variables are set correctly

---

## 🆘 Need Help?

1. Check the error message
2. Review REQUIREMENTS_GUIDE.md
3. Check YouTube API status
4. Open GitHub issue

---

**Ready? Start by getting your Bot Token from @BotFather!** 🎉
