# 🚀 Deploy on Oracle Cloud Always Free Tier

Oracle Cloud gives you an **Always Free** ARM VM with **4 CPUs and 24GB RAM** —
more power than any other free hosting. No trial, no credit card, free forever.

---

## Step 1: Create an Oracle Cloud Account

1. Go to [cloud.oracle.com/free](https://cloud.oracle.com/free)
2. Sign up with your email
3. **No credit card required** for the Always Free tier
4. Verify your email and complete registration

> ⚠️ Oracle requires phone verification. Use a real number.

## Step 2: Create a Compute Instance

1. After logging in, go to **Menu → Compute → Instances**
2. Click **Create Instance**
3. Configure:
   - **Name:** `youtube-bot`
   - **Image:** Select **Ubuntu 22.04 or 24.04** (aarch64/ARM)
   - **Shape:** Select **VM.Standard.A1.Flex** (Always Free eligible)
     - **OCPU:** 4 (max free)
     - **RAM:** 24 GB (max free)
   - **VNIC:** Accept defaults
4. **SSH Keys:**
   - If you have an SSH key, paste your **public key**
   - If not, generate one first (see below)

### Generate SSH Keys (if needed)

```bash
# On your PC:
ssh-keygen -t ed25519 -C "oracle-bot" -f ~/oracle-bot-key
# This creates two files:
#   ~/oracle-bot-key      (private — keep safe)
#   ~/oracle-bot-key.pub  (public — paste into Oracle)
```

5. Click **Create** and wait 2-3 minutes for the instance to start

## Step 3: Connect to Your VM

```bash
ssh -i ~/oracle-bot-key ubuntu@<YOUR_PUBLIC_IP>
```

Find your public IP in the Oracle Cloud console under your instance details.

> On Windows, use **PuTTY** or **Windows Terminal** with the OpenSSH client.

## Step 4: Set Up the Server

Run these commands on the VM:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y python3 python3-pip python3-venv ffmpeg git

# Create a working directory
mkdir -p ~/youtube-bot && cd ~/youtube-bot

# Create a Python virtual environment
python3 -m venv venv
source venv/bin/activate
```

## Step 5: Upload Your Bot Files

### Option A: Upload the zip from your PC

```bash
# From your PC (not the VM):
scp -i ~/oracle-bot-key /home/animatrixx-gamer/freebuff-desktop.zip \
    ubuntu@<YOUR_PUBLIC_IP>:~/youtube-bot/
```

Then on the VM:
```bash
cd ~/youtube-bot
sudo apt install -y unzip
unzip freebuff-desktop.zip
```

### Option B: Clone from Git (if your repo is on GitHub)

```bash
cd ~/youtube-bot
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git .
```

### Option C: Copy-paste bot.py manually

If you only have `bot.py`, you can create it directly:
```bash
nano ~/youtube-bot/bot.py
# Paste your bot.py content, then Ctrl+X → Y → Enter
```

## Step 6: Install Dependencies

```bash
cd ~/youtube-bot
source venv/bin/activate
pip install -r requirements.txt
```

## Step 7: Configure Environment

```bash
# Create the .env file
nano .env
```

Paste your configuration:
```env
BOT_TOKEN=your_bot_token_here
OWNER_ID=your_telegram_user_id
YOUTUBE_CLIENT_ID=your_client_id
YOUTUBE_CLIENT_SECRET=your_client_secret
YOUTUBE_REDIRECT_URI=http://localhost:8080/oauth/youtube/callback
PORT=8080
STORAGE_DIR=/home/ubuntu/youtube-bot/storage
```

Save with `Ctrl+X → Y → Enter`.

## Step 8: Test It

```bash
cd ~/youtube-bot
source venv/bin/activate
python bot.py
```

You should see the startup banner. Open Telegram and test your bot!
Press `Ctrl+C` to stop.

## Step 9: Run It 24/7 with systemd

Create a service so the bot runs automatically and restarts on crash:

```bash
sudo tee /etc/systemd/system/youtube-bot.service > /dev/null << 'EOF'
[Unit]
Description=YouTube Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/youtube-bot
ExecStart=/home/ubuntu/youtube-bot/venv/bin/python bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

# Security
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/ubuntu/youtube-bot/storage

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable youtube-bot
sudo systemctl start youtube-bot
```

## Step 10: Manage the Service

```bash
# Check status
sudo systemctl status youtube-bot

# View logs
sudo journalctl -u youtube-bot -f

# Restart
sudo systemctl restart youtube-bot

# Stop
sudo systemctl stop youtube-bot
```

## Step 11: Open the Port (if needed)

Oracle Cloud has a default security list. If you need the OAuth callback to work:

1. Go to **Oracle Cloud Console → Networking → Virtual Cloud Networks**
2. Click your VCN → **Security Lists**
3. Click **Default Security List → Add Ingress Rules**
4. Add:
   - **Source CIDR:** `0.0.0.0/0`
   - **Destination Port:** `8080`
   - **Protocol:** TCP
5. Click **Add Ingress Rules**

---

## Oracle Cloud Free Tier Includes

| Resource | Free Amount |
|----------|------------|
| ARM VMs | 4 OCPUs, 24GB RAM (1 instance) |
| Storage | 200GB boot volume |
| Networking | 10TB outbound/month |
| Public IP | 1 always free |

This is **way more** than enough for the bot. The bot uses ~100MB RAM.

---

## Updating the Bot

```bash
# SSH into the VM
ssh -i ~/oracle-bot-key ubuntu@<YOUR_PUBLIC_IP>

# Upload new bot.py (from your PC)
scp -i ~/oracle-bot-key bot.py ubuntu@<YOUR_PUBLIC_IP>:~/youtube-bot/bot.py

# Restart the service
sudo systemctl restart youtube-bot
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Can't SSH in | Check security list rules and make sure instance is running |
| Bot crashes on start | `sudo journalctl -u youtube-bot -n 50` to see errors |
| OAuth callback fails | Open port 8080 in security list, use public IP in redirect URI |
| "No module found" errors | Make sure you're using the venv: `source venv/bin/activate` |
| Database lost | Check that `STORAGE_DIR` points to the right place |
| Bot stops after a while | Check `systemctl status` — it should auto-restart |
