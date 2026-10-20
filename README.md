# FRIDAY for Android (v2)

Monochrome Kivy AI assistant using the OpenAI Responses API.

## What's new in v2
- **Live streaming replies** with a Stop button, auto-retry on 429/5xx, friendly error messages
- **Fixed multi-turn bug**: assistant history is now sent in the format the API accepts
- Markdown rendering (headings, bold, lists, inline/code blocks), Copy and Regenerate buttons, Retry on errors
- History **search**, delete (tap twice), and "Copy this chat"
- Images are auto-rotated, downscaled to 2048px and stored uniquely per message (v1 overwrote one file, breaking old chats); only the latest 2 images are re-sent to save data
- Working **text-to-speech** (Settings -> Speak replies), voice-language setting, custom instructions, optional web search
- Auto-growing input box, keyboard-safe layout, Android back-button handling, theme-correct popups
- Core logic in `friday_core.py` with tests: `python3 tests/test_core.py`

## Build (Linux / Colab)
```bash
sudo apt update && sudo apt install -y git zip unzip openjdk-17-jdk python3-pip \
  autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev \
  libtinfo6 cmake libffi-dev libssl-dev
python3 -m pip install --user --upgrade buildozer cython
cd FRIDAY_Android
buildozer android debug        # APK appears in bin/
```
First build downloads the SDK/NDK and takes a while. Install the APK on your phone, open
**Settings**, paste your OpenAI API key, Save.

## Notes
- The key is stored in the app's private storage (not encrypted). Never publish a build containing a key.
- API usage may incur charges. Web search needs a model/account that supports the tool.
- Upgrading from v1: your chat history and settings carry over.
- Troubleshooting: `401` bad key, `404` wrong model, `429` quota/billing. If Buildozer fails, use a clean Ubuntu environment.
