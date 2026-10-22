# tg-archive

This program saves Telegram groups into folders on your computer. Each group gets its own folder.

There are two jobs:

1. **New messages.** A bot sits in the group and saves messages as they are sent.
2. **Old messages.** Your own Telegram account reads the history from before the bot joined.

Text is always saved. Pictures and other files are optional.

Only archive groups you are allowed to keep a copy of. The folders contain other people's messages.

## Which steps do you need?

| You want | Follow |
| --- | --- |
| Messages from now on, including pictures and files | Steps 1 through 6, option A |
| Messages from now on, text only | Steps 1 through 6, option D |
| Old messages as well | Steps 1 through 7 |
| A download that failed | Step 8 |

You can stop after step 6. Step 7 is only for older history.

## 1. Install

You need Python 3.11 or newer, on macOS or Linux.

Open Terminal. Go to this folder. The example below uses the folder name from a typical download. Use your real path if it is different.

```bash
cd tg-reading-bots
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

`pip install -e .` creates the `tg-archive` command.

Every new terminal window needs this again before the command will be found:

```bash
cd tg-reading-bots
source .venv/bin/activate
```

## 2. Create the bot

The bot is a separate Telegram account that you add to groups. It saves new messages.

1. Open Telegram.
2. Search for `@BotFather` and open the official bot.
3. Send `/newbot`.
4. Send a display name, for example `Reading Archive`.
5. Send a username that ends in `bot`, for example `my_reading_archive_bot`. If Telegram says the name is taken, choose another.
6. BotFather replies with a token. It looks like `123456789:AA...`. Copy that whole token.

## 3. Save the token on your computer

1. Open the `.env` file in this folder.
2. Find the line `BOT_TOKEN=`.
3. Paste the token right after the `=`. No quotes and no spaces.

```bash
BOT_TOKEN=123456789:AA...
```

Leave this file on your computer. It is listed in `.gitignore`, so git will not upload it.

## 4. Let the bot read the whole group

A new bot only sees commands and messages that mention it. Turn that limit off before you add it to a group.

1. In the BotFather chat, send `/setprivacy`.
2. Tap your bot.
3. Tap **Disable**.
4. Send `/setjoingroups`.
5. Tap your bot.
6. Tap **Enable**. This is usually already on.

If the bot is already in a group, remove it and add it again. The privacy setting applies when the bot enters.

## 5. Add the bot to a group

Repeat this for every group you want to save.

1. Open the group.
2. On a phone, tap the group name at the top. On a computer, click the name at the top of the chat.
3. Choose **Add Members**.
4. Search for the bot username from step 2 and add it.
5. Leave it as a normal member. A group does not need the bot to be an admin.

For a **channel**, add the bot as an admin. A channel only sends its posts to bots that are admins.

Send one message in the group after the bot is added. Older messages in that group are step 7.

## 6. Save new messages

Start the listener:

### Option A. Text, images, and files

This is the normal choice. Images means photos, GIFs, and picture files such as PNG. Files means documents, video, audio, voice messages, and stickers.

```bash
tg-archive run
```

### Option B. Text and images, skip other files

```bash
tg-archive run --no-files
```

### Option C. Text and files, skip images

```bash
tg-archive run --no-images
```

### Option D. Text only

```bash
tg-archive run --no-images --no-files
```

The message is still saved. The transcript says `image not downloaded` or `file not downloaded`. You can fetch those attachments later with step 7 or step 8.

### Option E. Only some groups

By default, every group the bot is in gets a folder. To limit that, edit `.env`:

```bash
ALLOWED_CHAT_IDS=-1001111111111,-1002222222222
```

Use the numeric ids from `tg-archive chats` in step 7, or from the folder name that appears after the first saved message. Leave the line empty to save every group again.

### While it is running

1. Send a message in the group from your own account.
2. The terminal prints a folder under `data/chats/`.
3. Open `messages` inside that folder and read today's `.txt` file.
4. Press Ctrl+C to stop.
5. Run the same command again when you want to keep saving.

The bot sees messages from people. It does not see messages sent by other bots. If this computer is off for more than 24 hours, Telegram drops the unsaved live messages. Step 7 fills that gap.

Run this and step 7 at different times. If one is already running, the other stops and tells you to close the first one.

To make option B, C, or D the default, edit `.env`:

```bash
DOWNLOAD_IMAGES=false
DOWNLOAD_FILES=false
```

`true` and `false` are the only values. The history command in step 7 does not read these two lines. History asks you with flags every time.

## 7. Save older messages

Skip this whole step if you only want messages from the moment the bot joined.

A bot cannot read history. This step signs in as you, once, and then downloads the chats you choose.

### 7.1 Create an API application

1. On a computer, open [my.telegram.org](https://my.telegram.org).
2. Log in with your phone number and the code Telegram sends you.
3. Open **API development tools**.
4. If you already see `App api_id` and `App api_hash`, skip to 7.2.
5. If a form titled **About the app** appears, fill it in like this:
   - **App title:** `tg-archive`
   - **Short name:** `tgarchive`
   - **URL:** leave empty
   - **Platform:** click **Desktop**. The form often opens with Android selected. Desktop is the right one, because this program runs on your computer.
   - **Description:** `Save group messages into local folders`
6. Click **Create application**.

The page says to avoid the word "Telegram" in the title and short name. The names above follow that.

If the site shows an error, wait a minute and try another browser. A VPN is a common cause.

### 7.2 Save the API id and hash

On the API development tools page, copy the two values into `.env`:

```bash
API_ID=123456
API_HASH=paste_the_hash_here
```

`API_ID` is the number. `API_HASH` is the long code next to it. These are not the bot token from step 2. You need both the token and these two values if you use live saving and history.

### 7.3 Sign in once

```bash
tg-archive login
```

1. If it asks for a phone number, type it in international form, for example `+15551234567`. You can also put `TELEGRAM_PHONE=+15551234567` in `.env` so it does not ask.
2. Open Telegram. A chat named **Telegram** has a login code. Type that code into the terminal. It is often in the app, not in an SMS.
3. If you use a two-step password, type it when asked. The characters stay hidden. That is normal.

This writes `data/user.session`. That file is a full login to your account. It is gitignored. Do not send it to anyone.

### 7.4 See the chats you can download

```bash
tg-archive chats
```

You get a list like this:

```text
id                 type         name
-1001234567890     supergroup   Design Reviews  @designreviews
-4990000001        group        Family
```

Copy the id from the first column.

To include private chats in that list:

```bash
tg-archive chats --dms
```

### 7A. Text only

Nothing else is downloaded. This is the right first run on a large group.

```bash
tg-archive history --chat -1001234567890
```

Replace the id with yours.

### 7B. Text and images

Images means photos, GIFs, and picture files such as PNG or JPG.

```bash
tg-archive history --chat -1001234567890 --images
```

### 7C. Text and other files, without images

Files means PDFs and other documents, video, audio, voice messages, and stickers.

```bash
tg-archive history --chat -1001234567890 --files
```

### 7D. Text, images, and files

```bash
tg-archive history --chat -1001234567890 --images --files
```

History can download files larger than 20 MB. The live bot in step 6 cannot, unless you later set up a local Bot API server. Those oversized live files are still written into the transcript with the reason.

### 7E. Several chats in one run

Repeat `--chat`. The same image and file choice applies to every chat in that run.

```bash
tg-archive history --chat -1001111111111 --chat -1002222222222 --images
```

### 7F. Choose a chat by username, link, or title

```bash
tg-archive history --chat @designreviews --images
tg-archive history --chat https://t.me/designreviews --files
tg-archive history --chat "Design Reviews"
```

If two chats have the same title, the command prints both ids and stops. Run it again with the numeric id.

### 7G. Every group on your account

This reads every group and supergroup. It asks you to type `y` before it starts. Channels and private chats are not included. Pass those with `--chat` if you want them.

```bash
tg-archive history --all-groups
tg-archive history --all-groups --images --files
```

`--yes` skips the question. Use it only when you are sure:

```bash
tg-archive history --all-groups --yes
```

You can mix this with extra chats:

```bash
tg-archive history --all-groups --chat @somechannel --files
```

### 7H. Add pictures or files later

Run text first. Run again with the flags you skipped. Messages you already saved are not copied a second time. The missing attachments are downloaded.

```bash
tg-archive history --chat -1001234567890
tg-archive history --chat -1001234567890 --images
tg-archive history --chat -1001234567890 --files
```

### 7I. Stop and continue

Press Ctrl+C. Run the same command again. It continues from where it stopped.

A long history waits about a second between requests so Telegram does not block the account. Large files print a percent while they download.

## 8. Retry a download that failed

Use this when step 6 recorded a file but could not save it. A file over 20 MB needs step 7 instead. The transcript names the reason.

Try the failed bot downloads again, using the same image and file settings as `tg-archive run`:

```bash
tg-archive retry
```

Try files only, or images only:

```bash
tg-archive retry --no-images
tg-archive retry --no-files
```

If the message was seen by history mode and never by the bot, `retry` tells you to run step 7 with `--images` or `--files`. The bot and your account use different file ids, so history is what can fetch those.

## 9. Read what was saved

Each chat is one folder. The number at the start is the chat id. The words after it are the group title.

```text
data/chats/-1001234567890__Design-Reviews/
  chat.json
  messages/2026-10-22.txt
  messages/2026-10-22.jsonl
  media/2026/10/22/143012_42_photo.jpg
  media/2026/10/22/album_99/
```

Open the `.txt` file for a normal transcript:

```text
[2026-10-22 14:30:12] Ada (@ada) #42
hello

[2026-10-22 14:30:18] Ada (@ada) #43
image: media/2026/10/22/143018_43_photo.jpg
look at this

[2026-10-22 14:32:00] Ada (@ada) #50-#51
album: media/2026/10/22/album_99/
  50_photo.jpg
  51_photo.jpg
two photos
```

The `.jsonl` file is the raw log, one JSON object per line. Dates follow the clock of the computer that saved them.

`chat.json` stores the current title and the older titles. If the group is renamed, the folder is renamed and the id at the front stays the same.

`image not downloaded` or `file not downloaded` means the text was saved and the attachment was left for a later run.

## 10. Put this project on GitHub

Your downloaded chats and secrets stay on this computer. Git is set to ignore:

- `.env` (bot token, API id, API hash, phone number)
- `data/` (every saved chat, plus `data/user.session`)
- `*.session` anywhere in the project

Before you push, run:

```bash
git status
```

You should see your code and this README. You should not see `.env`, `data/`, or any `.session` file. If those names appear as files to be committed, stop. Do not push.

On GitHub, create a new empty repository. Do not add a README, license, or `.gitignore` there. This project already has them.

Then, in this folder:

```bash
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

Someone who clones it runs step 1 onward with their own bot and their own API id.

## If something goes wrong

**The bot is in the group and nothing is saved.** In BotFather, `/setprivacy` must be **Disable** for this bot. Remove the bot from the group and add it again. Then send a new message. Old messages do not arrive through `tg-archive run`.

**`tg-archive` is not found.** Run `source .venv/bin/activate` in this folder. If it is still missing, run `pip install -e .`.

**Telegram rejected BOT_TOKEN.** In `.env`, paste only the token from BotFather. No quotes, no spaces, and not the whole `https://api.telegram.org/...` URL.

**History says API_ID and API_HASH are empty.** Finish step 7.1 and 7.2. The bot token cannot download old messages.

**The login code was rejected.** Run `tg-archive login` again and use the newest code in the Telegram chat.

**my.telegram.org shows an error.** Wait, switch browsers, and turn a VPN off. On the app form, choose **Desktop**.

**A file is larger than 20 MB.** Run step 7D, or 7B / 7C, for that chat. The live bot's normal servers stop at 20 MB.

**Another tg-archive command is using this data folder.** Quit the other Terminal window where `run` or `history` is still going, then try again.

## What this program does not do

- The live bot starts at the moment it joins. Older messages need step 7.
- Telegram keeps unsaved live updates for 24 hours.
- A bot is not told when someone deletes a group message. Edits are saved.
- A bot does not see other bots. History does, because it uses your account.
- If a message's attachment is replaced by an edit, the first downloaded file is kept. The new text is still saved.

## Optional: remove the 20 MB live limit

You do not need this for history. History already downloads large files.

A [local Bot API server](https://github.com/tdlib/telegram-bot-api), started with `--local`, lets `tg-archive run` download large files too. After it is running, set this in `.env`:

```bash
BOT_API_BASE=http://127.0.0.1:8081
```

Follow that project's own install guide, including its `logOut` step, before you point the bot at it.

## Check that the program still works

```bash
pytest
```

Those tests use fake messages. They do not contact Telegram and they do not read your `.env`.
