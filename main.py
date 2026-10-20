"""FRIDAY - Android-first AI assistant (Kivy).

Streaming replies, markdown rendering, persistent history with search,
image understanding, voice input, spoken replies, optional web search.
Build with Buildozer; see README.md. Keep your API key private.
"""
import os
import threading
import time

import kivy
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput
from kivy.graphics import Color, RoundedRectangle
from kivy.utils import platform

import friday_core as core

Window.softinput_mode = "resize"  # keep the input box above the keyboard

THEMES = {
    "Obsidian": {"bg": (0.035, 0.035, 0.035, 1), "panel": (0.085, 0.085, 0.085, 1),
                 "input": (0.14, 0.14, 0.14, 1), "fg": (0.96, 0.96, 0.96, 1),
                 "muted": (0.62, 0.62, 0.62, 1), "accent": (1, 1, 1, 1), "code": "bdbdbd"},
    "Paper": {"bg": (0.94, 0.94, 0.94, 1), "panel": (1, 1, 1, 1),
              "input": (0.86, 0.86, 0.86, 1), "fg": (0.06, 0.06, 0.06, 1),
              "muted": (0.35, 0.35, 0.35, 1), "accent": (0.05, 0.05, 0.05, 1), "code": "444444"},
    "Graphite": {"bg": (0.12, 0.12, 0.12, 1), "panel": (0.18, 0.18, 0.18, 1),
                 "input": (0.24, 0.24, 0.24, 1), "fg": (0.96, 0.96, 0.96, 1),
                 "muted": (0.68, 0.68, 0.68, 1), "accent": (0.9, 0.9, 0.9, 1), "code": "d0d0d0"},
}
IMG_REQ, VOICE_REQ = 7192, 7193


def rounded(widget, color, radius=13):
    with widget.canvas.before:
        Color(*color)
        rect = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[dp(radius)] * 4)
    widget.bind(pos=lambda w, v: setattr(rect, "pos", v),
                size=lambda w, v: setattr(rect, "size", v))


class Bubble(BoxLayout):
    """One chat message. Height follows its content; text can stream in."""

    def __init__(self, app, who, text, image_path=None, actions=(), **kw):
        super().__init__(orientation="vertical", size_hint_y=None,
                         padding=dp(12), spacing=dp(6), **kw)
        self.app, self.who, self.raw = app, who, text
        c = app.colors
        self.bind(minimum_height=self.setter("height"))
        rounded(self, c["input"] if who == "YOU" else c["panel"])
        title = Label(text="[b]%s[/b]" % who, markup=True, size_hint_y=None, height=dp(18),
                      color=c["fg"] if who == "YOU" else c["muted"], font_size="11sp",
                      halign="left", valign="middle")
        title.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.body = Label(text="", markup=True, size_hint_y=None, color=c["fg"],
                          font_size="15sp", halign="left", valign="top", text_size=(None, None))
        self.body.bind(width=lambda w, s: setattr(w, "text_size", (max(1, s), None)),
                       texture_size=lambda w, s: setattr(w, "height", s[1] + dp(2)))
        self.add_widget(title)
        self.add_widget(self.body)
        if image_path and os.path.exists(image_path):
            img = KivyImage(source=image_path, size_hint_y=None, height=dp(180))
            if hasattr(img, "fit_mode"):
                img.fit_mode = "contain"
            else:  # older Kivy
                img.allow_stretch, img.keep_ratio = True, True
            self.add_widget(img)
        self.row = None
        self.buttons = {}
        if actions:
            self.row = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(6))
            self.row.add_widget(Label())  # spacer pushes buttons right
            for name, cb in actions:
                b = Button(text=name, size_hint_x=None, width=dp(86), font_size="12sp",
                           background_normal="", background_down="",
                           background_color=c["bg"], color=c["muted"])
                b.bind(on_release=lambda *_, cb=cb: cb(self))
                self.buttons[name] = b
                self.row.add_widget(b)
            self.add_widget(self.row)
        self.set_text(text)

    def set_text(self, raw):
        self.raw = raw
        self.body.text = core.md_to_markup(raw, self.app.mono_font, self.app.colors["code"]) \
            if self.who != "YOU" else core.escape_markup(raw)

    def drop_button(self, name):
        b = self.buttons.pop(name, None)
        if b is not None and self.row is not None:
            self.row.remove_widget(b)


class FridayApp(App):
    title = "FRIDAY"

    # ------------------------------------------------------------------ build
    def build(self):
        self.data_dir = self.user_data_dir
        os.makedirs(os.path.join(self.data_dir, "attachments"), exist_ok=True)
        self.settings = core.Settings(self.data_dir)
        self.store = core.Store(os.path.join(self.data_dir, "friday_history.sqlite3"))
        self.set_theme(self.settings.get("theme"))
        mono = os.path.join(kivy.kivy_data_dir, "fonts", "RobotoMono-Regular.ttf")
        self.mono_font = mono if os.path.exists(mono) else None
        self.active_chat = self.settings.get("active_chat")
        self.messages = []
        self.pending_image = None
        self.busy = False
        self.cancel = threading.Event()
        self.popup = None
        self.last_ai_bubble = None
        self._buf, self._last_flush, self._stream_bubble = "", 0.0, None
        self._tts, self._tts_ready, self._tts_listener = None, False, None
        self._android_hooked = False
        Window.bind(on_keyboard=self.on_keyboard)
        root = self.build_ui()
        Clock.schedule_once(lambda *_: self.load_chat(self.active_chat), 0)
        return root

    def set_theme(self, name):
        self.theme_name = name if name in THEMES else "Obsidian"
        self.colors = THEMES[self.theme_name]
        Window.clearcolor = self.colors["bg"]

    def btn(self, text, cb, width=None, accent=False, height=40):
        c = self.colors
        b = Button(text=text, size_hint_x=None if width else 1, width=width or 0,
                   size_hint_y=None, height=dp(height), font_size="13sp",
                   background_normal="", background_down="",
                   background_color=c["accent"] if accent else c["input"],
                   color=c["bg"] if accent else c["fg"])
        b.bind(on_release=lambda *_: cb())
        return b

    def text_input(self, **kw):
        c = self.colors
        return TextInput(background_normal="", background_active="",
                         background_color=c["input"], foreground_color=c["fg"],
                         cursor_color=c["fg"], hint_text_color=c["muted"],
                         selection_color=(*c["muted"][:3], 0.4), **kw)

    def label(self, text, **kw):
        kw.setdefault("color", self.colors["muted"])
        kw.setdefault("font_size", "12sp")
        kw.setdefault("halign", "left")
        kw.setdefault("valign", "middle")
        lab = Label(text=text, **kw)
        lab.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        return lab

    def build_ui(self):
        c = self.colors
        root = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        header = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(6))
        brand = BoxLayout(orientation="vertical")
        t = Label(text="[b]FRIDAY[/b]", markup=True, font_size="22sp", halign="left",
                  valign="middle", color=c["fg"])
        t.bind(size=lambda w, s: setattr(w, "text_size", s))
        s = Label(text="PERSONAL INTELLIGENCE", font_size="9sp", halign="left",
                  valign="middle", color=c["muted"])
        s.bind(size=lambda w, s_: setattr(w, "text_size", s_))
        brand.add_widget(t)
        brand.add_widget(s)
        header.add_widget(brand)
        header.add_widget(self.btn("New", self.new_chat, dp(56)))
        header.add_widget(self.btn("History", self.show_history, dp(76)))
        header.add_widget(self.btn("Settings", self.show_settings, dp(84)))
        root.add_widget(header)

        self.chat_scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        self.chat_stack = GridLayout(cols=1, spacing=dp(9), size_hint_y=None,
                                     padding=(0, dp(4), 0, dp(8)))
        self.chat_stack.bind(minimum_height=self.chat_stack.setter("height"))
        self.chat_scroll.add_widget(self.chat_stack)
        root.add_widget(self.chat_scroll)

        self.attach_row = BoxLayout(size_hint_y=None, height=0, opacity=0, disabled=True,
                                    spacing=dp(8))
        self.attach_label = self.label("")
        self.attach_row.add_widget(self.attach_label)
        self.attach_row.add_widget(self.btn("Remove", self.clear_image, dp(80), height=32))
        root.add_widget(self.attach_row)

        self.prompt = self.text_input(hint_text="Message Friday...", multiline=True,
                                      size_hint_y=None, height=dp(52), font_size="16sp",
                                      padding=[dp(12), dp(12), dp(12), dp(12)])
        self.prompt.bind(minimum_height=lambda w, h: setattr(
            w, "height", max(dp(52), min(dp(150), h))))
        root.add_widget(self.prompt)

        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(7))
        row.add_widget(self.btn("+ Image", self.pick_image, dp(90), height=42))
        row.add_widget(self.btn("Voice", self.voice_input, dp(80), height=42))
        self.send_btn = self.btn("Send", self.on_send_tap, accent=True, height=42)
        row.add_widget(self.send_btn)
        root.add_widget(row)

        self.status = self.label("Ready", size_hint_y=None, height=dp(20), font_size="11sp")
        root.add_widget(self.status)
        return root

    def reload_ui(self):
        if self.root is not None:
            Window.remove_widget(self.root)
        self.root = self.build_ui()
        Window.add_widget(self.root)
        self.last_ai_bubble = None
        self.load_chat(self.active_chat)

    # ----------------------------------------------------------------- helpers
    def say(self, text):
        self.status.text = text

    def open_popup(self, title, content, size=(0.94, 0.85)):
        c = self.colors
        self.close_popup()
        self.popup = Popup(title=title, content=content, size_hint=size, background="",
                           background_color=c["panel"], title_color=c["fg"],
                           separator_color=c["muted"])
        self.popup.bind(on_dismiss=lambda *_: setattr(self, "popup", None))
        self.popup.open()

    def close_popup(self):
        if self.popup is not None:
            p, self.popup = self.popup, None
            p.dismiss()

    def on_keyboard(self, window, key, *args):
        if key != 27:  # Android back
            return False
        if self.popup is not None:
            self.close_popup()
            return True
        if platform == "android":
            try:
                from jnius import autoclass
                autoclass("org.kivy.android.PythonActivity").mActivity.moveTaskToBack(True)
                return True
            except Exception:
                pass
        return False

    def on_pause(self):
        return True  # keep running in the background

    def scroll_bottom(self, force=True):
        if force or self.chat_scroll.scroll_y < 0.06:
            Clock.schedule_once(lambda *_: setattr(self.chat_scroll, "scroll_y", 0), 0.05)

    def add_bubble(self, who, text, image_path=None, actions=()):
        b = Bubble(self, who, text, image_path, actions)
        self.chat_stack.add_widget(b)
        self.scroll_bottom()
        return b

    def ai_actions(self, regen=True):
        acts = [("Copy", self.copy_bubble)]
        if regen:
            acts.append(("Regenerate", self.regenerate))
        return acts

    def copy_bubble(self, bubble):
        Clipboard.copy(bubble.raw)
        self.say("Copied to clipboard.")

    # ------------------------------------------------------------------- chats
    def new_chat(self):
        if self.busy:
            return self.say("Wait for the reply or tap Stop first.")
        self.active_chat = self.store.new_chat()
        self.messages = []
        self.clear_image()
        self.render_chat()
        self.settings.set(active_chat=self.active_chat)

    def load_chat(self, chat_id):
        if not chat_id or self.store.chat_title(chat_id) is None:
            return self.new_chat()
        self.active_chat = chat_id
        self.messages = self.store.messages(chat_id)
        self.render_chat()
        self.settings.set(active_chat=chat_id)

    def render_chat(self):
        self.chat_stack.clear_widgets()
        self.last_ai_bubble = None
        if not self.messages:
            self.add_bubble("FRIDAY", "Hi, I'm Friday. Ask me anything, dictate with Voice, "
                                      "or attach an image for me to look at.")
            return
        last_ai = max((i for i, m in enumerate(self.messages) if m["role"] == "assistant"),
                      default=-1)
        for i, m in enumerate(self.messages):
            if m["role"] == "user":
                self.add_bubble("YOU", m["content"], m["image_path"])
            else:
                b = self.add_bubble("FRIDAY", m["content"], actions=self.ai_actions(i == last_ai))
                if i == last_ai:
                    self.last_ai_bubble = b
        self.scroll_bottom()

    # -------------------------------------------------------------- sending
    def on_send_tap(self):
        if self.busy:
            self.cancel.set()
            self.say("Stopping...")
        else:
            self.send_message()

    def send_message(self):
        text = self.prompt.text.strip()
        image = self.pending_image
        if not text and not image:
            return
        if not self.settings.get("api_key"):
            self.say("Add your OpenAI API key in Settings to enable replies.")
            return self.show_settings()
        self.stop_speaking()
        content = text or "What is in this image?"
        self.prompt.text = ""
        self.clear_image()
        mid = self.store.add_message(self.active_chat, "user", content, image)
        self.messages.append({"id": mid, "role": "user", "content": content, "image_path": image})
        if self.store.chat_title(self.active_chat) == "New chat":
            self.store.set_title(self.active_chat, core.make_title(text))
        if self.messages and len(self.messages) == 1:
            self.chat_stack.clear_widgets()  # drop the welcome bubble
        self.add_bubble("YOU", content, image)
        self.start_reply()

    def start_reply(self):
        if self.last_ai_bubble is not None:
            self.last_ai_bubble.drop_button("Regenerate")
        self.busy = True
        self.cancel.clear()
        self.send_btn.text = "Stop"
        self.say("Friday is thinking...")
        self._buf = ""
        self._stream_bubble = self.add_bubble("FRIDAY", "...")
        threading.Thread(target=self.worker, daemon=True).start()

    def worker(self):
        error, answer = None, ""
        try:
            items = core.build_input(self.messages)
            answer = core.stream_response(
                self.settings.get("api_key"), self.settings.get("model") or core.DEFAULT_MODEL,
                core.build_instructions(self.settings.get("custom_instructions")), items,
                self.on_delta, self.cancel, bool(self.settings.get("web_search")))
        except core.ApiError as e:
            error = str(e)
        except Exception as e:  # never let a thread die silently
            error = "Unexpected error: %s" % e
        Clock.schedule_once(lambda *_: self.finish_reply(answer, error))

    def on_delta(self, delta):
        self._buf += delta
        now = time.time()
        if now - self._last_flush > 0.08:
            self._last_flush = now
            Clock.schedule_once(self.flush_stream)

    def flush_stream(self, *_):
        if self._stream_bubble is not None and self._buf:
            self._stream_bubble.set_text(self._buf)
            self.scroll_bottom(force=False)

    def finish_reply(self, answer, error):
        bubble, self._stream_bubble = self._stream_bubble, None
        self.busy = False
        self.send_btn.text = "Send"
        answer = answer or self._buf.strip()
        if answer:
            mid = self.store.add_message(self.active_chat, "assistant", answer)
            self.messages.append({"id": mid, "role": "assistant", "content": answer,
                                  "image_path": None})
            bubble.set_text(answer)
            if self.cancel.is_set():
                self.say("Stopped.")
            else:
                self.say("Ready")
            self.last_ai_bubble = bubble
            for name, cb in self.ai_actions():
                self._attach_action(bubble, name, cb)
            if error is None and not self.cancel.is_set() and self.settings.get("speak_replies"):
                self.speak(answer)
        if error and not answer:
            self.chat_stack.remove_widget(bubble)
            self.add_bubble("FRIDAY", error, actions=[("Retry", self.retry)])
            self.say("Error - see message above.")
        elif error:
            self.say("Reply cut short: " + error.splitlines()[0])
        elif not answer:
            bubble.set_text("Stopped." if self.cancel.is_set() else "(No reply)")
            if self.cancel.is_set():
                self.say("Stopped.")
        self.scroll_bottom(force=False)

    def _attach_action(self, bubble, name, cb):
        """Add an action button to a bubble that was created without a button row."""
        if bubble.row is None:
            bubble.row = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(6))
            bubble.row.add_widget(Label())
            bubble.add_widget(bubble.row)
        c = self.colors
        b = Button(text=name, size_hint_x=None, width=dp(86), font_size="12sp",
                   background_normal="", background_down="",
                   background_color=c["bg"], color=c["muted"])
        b.bind(on_release=lambda *_: cb(bubble))
        bubble.buttons[name] = b
        bubble.row.add_widget(b)

    def regenerate(self, bubble=None):
        if self.busy or not self.messages or self.messages[-1]["role"] != "assistant":
            return
        last = self.messages.pop()
        self.store.delete_message(last["id"])
        if self.last_ai_bubble is not None:
            self.chat_stack.remove_widget(self.last_ai_bubble)
            self.last_ai_bubble = None
        self.start_reply()

    def retry(self, bubble=None):
        if self.busy or not self.messages or self.messages[-1]["role"] != "user":
            return
        if bubble is not None:
            self.chat_stack.remove_widget(bubble)
        self.start_reply()

    # --------------------------------------------------------------- images
    def pick_image(self):
        if platform == "android":
            return self.android_pick_image()
        chooser = FileChooserListView(filters=["*.png", "*.jpg", "*.jpeg", "*.webp"])
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        box.add_widget(chooser)
        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        row.add_widget(self.btn("Cancel", self.close_popup))

        def use():
            if chooser.selection:
                self.set_image(self.prepare_desktop_image(chooser.selection[0]))
                self.close_popup()
        row.add_widget(self.btn("Use image", use, accent=True))
        box.add_widget(row)
        self.open_popup("Choose image", box)

    def new_attachment_path(self):
        return os.path.join(self.data_dir, "attachments", "img_%d.jpg" % int(time.time() * 1000))

    def prepare_desktop_image(self, src):
        dst = self.new_attachment_path()
        try:
            from PIL import Image, ImageOps
            im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
            im.thumbnail((2048, 2048))
            im.save(dst, "JPEG", quality=85)
        except Exception:
            import shutil
            shutil.copyfile(src, dst)
        return dst

    def set_image(self, path):
        self.pending_image = path
        self.attach_label.text = "Image attached"
        self.attach_row.height, self.attach_row.opacity, self.attach_row.disabled = dp(34), 1, False
        self.say("Image ready. Add a question or tap Send.")

    def clear_image(self):
        self.pending_image = None
        self.attach_row.height, self.attach_row.opacity, self.attach_row.disabled = 0, 0, True

    # ------------------------------------------------------------- Android
    def hook_android(self):
        if self._android_hooked:
            return
        from android import activity
        activity.bind(on_activity_result=self.on_activity_result)
        self._android_hooked = True

    def on_activity_result(self, request_code, result_code, intent):
        if request_code == IMG_REQ:
            threading.Thread(target=self.read_android_image,
                             args=(result_code, intent), daemon=True).start()
        elif request_code == VOICE_REQ:
            self.read_android_voice(result_code, intent)

    def android_pick_image(self):
        try:
            from jnius import autoclass
            self.hook_android()
            Intent = autoclass("android.content.Intent")
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            intent = Intent(Intent.ACTION_GET_CONTENT)
            intent.setType("image/*")
            intent.addCategory(Intent.CATEGORY_OPENABLE)
            act.startActivityForResult(Intent.createChooser(intent, "Select image for Friday"),
                                       IMG_REQ)
        except Exception as e:
            self.say("Image picker unavailable: %s" % e)

    def read_android_image(self, result_code, intent):
        """Decode, rotate, downscale to <=2048px and save as JPEG (saves data and cost)."""
        if result_code != -1 or intent is None or intent.getData() is None:
            return
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            resolver, uri = act.getContentResolver(), intent.getData()
            BF = autoclass("android.graphics.BitmapFactory")
            Opts = autoclass("android.graphics.BitmapFactory$Options")
            Bitmap = autoclass("android.graphics.Bitmap")

            opts = Opts()
            opts.inJustDecodeBounds = True
            s = resolver.openInputStream(uri)
            BF.decodeStream(s, None, opts)
            s.close()
            sample = 1
            while max(opts.outWidth, opts.outHeight) // sample > 2048:
                sample *= 2
            opts = Opts()
            opts.inSampleSize = sample
            s = resolver.openInputStream(uri)
            bmp = BF.decodeStream(s, None, opts)
            s.close()
            if bmp is None:
                raise RuntimeError("unsupported image")
            try:  # honour camera orientation
                Exif = autoclass("android.media.ExifInterface")
                s = resolver.openInputStream(uri)
                orient = Exif(s).getAttributeInt("Orientation", 1)
                s.close()
                deg = {6: 90, 3: 180, 8: 270}.get(orient, 0)
                if deg:
                    Matrix = autoclass("android.graphics.Matrix")
                    m = Matrix()
                    m.postRotate(float(deg))
                    bmp = Bitmap.createBitmap(bmp, 0, 0, bmp.getWidth(), bmp.getHeight(), m, True)
            except Exception:
                pass
            out = autoclass("java.io.ByteArrayOutputStream")()
            bmp.compress(Bitmap.CompressFormat.JPEG, 85, out)
            arr = out.toByteArray()
            raw = bytes(arr) if isinstance(arr, (bytes, bytearray)) else \
                bytes(b & 0xFF for b in arr)
            path = self.new_attachment_path()
            with open(path, "wb") as f:
                f.write(raw)
            Clock.schedule_once(lambda *_: self.set_image(path))
        except Exception as e:
            Clock.schedule_once(lambda *_: self.say("Could not load image: %s" % e))

    def voice_input(self):
        if platform != "android":
            return self.say("Voice input works on the Android build.")
        try:
            from jnius import autoclass
            self.hook_android()
            Intent = autoclass("android.content.Intent")
            RI = autoclass("android.speech.RecognizerIntent")
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            intent = Intent(RI.ACTION_RECOGNIZE_SPEECH)
            intent.putExtra(RI.EXTRA_LANGUAGE_MODEL, RI.LANGUAGE_MODEL_FREE_FORM)
            intent.putExtra(RI.EXTRA_PROMPT, "Speak to Friday")
            lang = (self.settings.get("voice_lang") or "").strip()
            if lang:
                intent.putExtra(RI.EXTRA_LANGUAGE, lang)
            act.startActivityForResult(intent, VOICE_REQ)
            self.say("Listening...")
        except Exception as e:
            self.say("Voice input unavailable: %s" % e)

    def read_android_voice(self, result_code, intent):
        if result_code != -1 or intent is None:
            return Clock.schedule_once(lambda *_: self.say("Ready"))
        try:
            from jnius import autoclass
            RI = autoclass("android.speech.RecognizerIntent")
            res = intent.getStringArrayListExtra(RI.EXTRA_RESULTS)
            if res is not None and res.size() > 0:
                phrase = str(res.get(0))
                Clock.schedule_once(lambda *_: self.put_voice(phrase))
        except Exception as e:
            Clock.schedule_once(lambda *_: self.say("Voice error: %s" % e))

    def put_voice(self, phrase):
        self.prompt.text = (self.prompt.text + " " + phrase).strip()
        self.prompt.focus = True
        self.say("Voice captured. Edit if needed, then tap Send.")

    # ------------------------------------------------------------------ TTS
    def init_tts(self):
        if platform != "android" or self._tts is not None:
            return
        from jnius import autoclass, PythonJavaClass, java_method
        app = self

        class InitListener(PythonJavaClass):
            __javainterfaces__ = ["android/speech/tts/TextToSpeech$OnInitListener"]
            __javacontext__ = "app"

            @java_method("(I)V")
            def onInit(self, status):
                if status == 0:
                    try:
                        Locale = autoclass("java.util.Locale")
                        app._tts.setLanguage(Locale.getDefault())
                    except Exception:
                        pass
                    app._tts_ready = True

        TTS = autoclass("android.speech.tts.TextToSpeech")
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        self._tts_listener = InitListener()  # keep a reference or it gets garbage-collected
        self._tts = TTS(act, self._tts_listener)

    def speak(self, text):
        if platform != "android":
            return
        try:
            self.init_tts()
            if not self._tts_ready:
                return  # engine still starting; next reply will be spoken
            plain = core.strip_markdown(text)[:3500]
            self._tts.speak(plain, 0, None, "friday_reply")
        except Exception:
            pass

    def stop_speaking(self):
        try:
            if self._tts is not None:
                self._tts.stop()
        except Exception:
            pass

    def on_stop(self):
        self.stop_speaking()
        try:
            if self._tts is not None:
                self._tts.shutdown()
        except Exception:
            pass

    # ------------------------------------------------------------- settings
    def show_settings(self):
        if self.busy:
            return self.say("Wait for the reply or tap Stop first.")
        c, st = self.colors, self.settings
        form = GridLayout(cols=1, spacing=dp(8), size_hint_y=None, padding=(0, 0, dp(4), 0))
        form.bind(minimum_height=form.setter("height"))

        def add(widget, h=None):
            if h:
                widget.size_hint_y, widget.height = None, dp(h)
            form.add_widget(widget)
            return widget

        add(self.label("OpenAI API key (stored only on this device)"), 22)
        key_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        key = self.text_input(text=st.get("api_key"), password=True, multiline=False,
                              font_size="14sp")
        show = self.btn("Show", lambda: None, dp(64), height=46)

        def toggle():
            key.password = not key.password
            show.text = "Show" if key.password else "Hide"
        show.bind(on_release=lambda *_: toggle())
        key_row.add_widget(key)
        key_row.add_widget(show)
        add(key_row)

        add(self.label("Model"), 22)
        model = add(self.text_input(text=st.get("model"), multiline=False), 44)
        preset = Spinner(text="Quick pick", values=core.MODEL_PRESETS, background_normal="",
                         background_color=c["input"], color=c["fg"])
        preset.bind(text=lambda w, v: setattr(model, "text", v) if v != "Quick pick" else None)
        add(preset, 40)

        add(self.label("Theme"), 22)
        theme = Spinner(text=self.theme_name, values=list(THEMES), background_normal="",
                        background_color=c["input"], color=c["fg"])
        add(theme, 40)

        sw_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        sw_row.add_widget(self.label("Speak replies aloud", color=c["fg"], font_size="14sp"))
        speak = Switch(active=bool(st.get("speak_replies")), size_hint_x=None, width=dp(80))
        sw_row.add_widget(speak)
        add(sw_row)
        sw2 = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        sw2.add_widget(self.label("Web search (needs a supporting model)", color=c["fg"],
                                  font_size="14sp"))
        web = Switch(active=bool(st.get("web_search")), size_hint_x=None, width=dp(80))
        sw2.add_widget(web)
        add(sw2)

        add(self.label("Voice language (e.g. en-US, hi-IN; blank = device default)"), 22)
        lang = add(self.text_input(text=st.get("voice_lang"), multiline=False), 42)
        add(self.label("Custom instructions (tone, name, preferences)"), 22)
        instr = add(self.text_input(text=st.get("custom_instructions"), multiline=True,
                                    font_size="14sp"), 100)

        scroll = ScrollView(do_scroll_x=False)
        scroll.add_widget(form)
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        box.add_widget(scroll)
        actions = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        actions.add_widget(self.btn("Cancel", self.close_popup))

        def save():
            old_theme = self.theme_name
            st.set(api_key=key.text.strip(), model=model.text.strip() or core.DEFAULT_MODEL,
                   theme=theme.text, speak_replies=speak.active, web_search=web.active,
                   voice_lang=lang.text.strip(), custom_instructions=instr.text.strip())
            self.close_popup()
            if theme.text != old_theme:
                self.set_theme(theme.text)
                self.reload_ui()
            self.say("Settings saved.")
        actions.add_widget(self.btn("Save", save, accent=True))
        box.add_widget(actions)
        self.open_popup("Settings", box, (0.95, 0.9))

    # -------------------------------------------------------------- history
    def show_history(self):
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        search = self.text_input(hint_text="Search chats...", multiline=False,
                                 size_hint_y=None, height=dp(42))
        box.add_widget(search)
        listing = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        listing.bind(minimum_height=listing.setter("height"))
        scroll = ScrollView(do_scroll_x=False)
        scroll.add_widget(listing)
        box.add_widget(scroll)

        def refresh(*_):
            listing.clear_widgets()
            for cid, title, updated in self.store.list_chats(search.text.strip()):
                row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
                row.add_widget(self.btn("%s  -  %s" % (title or "Chat", (updated or "")[:16]),
                                        lambda cid=cid: self.open_chat(cid), height=44))
                d = self.btn("Delete", lambda: None, dp(70), height=44)

                def ask(cid=cid, d=d):
                    if d.text == "Delete":
                        d.text = "Sure?"
                        Clock.schedule_once(lambda *_: setattr(d, "text", "Delete"), 3)
                    else:
                        self.delete_chat(cid)
                        refresh()
                d.bind(on_release=lambda *_, ask=ask: ask())
                row.add_widget(d)
                listing.add_widget(row)
            if not listing.children:
                listing.add_widget(self.label("No chats found.", size_hint_y=None, height=dp(30)))
        search.bind(text=refresh)
        refresh()

        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        row.add_widget(self.btn("New chat", lambda: (self.close_popup(), self.new_chat()),
                                accent=True))
        row.add_widget(self.btn("Copy this chat", self.copy_chat))
        row.add_widget(self.btn("Close", self.close_popup))
        box.add_widget(row)
        self.open_popup("Chat history", box, (0.95, 0.88))

    def open_chat(self, cid):
        if self.busy:
            return self.say("Wait for the reply or tap Stop first.")
        self.close_popup()
        self.load_chat(cid)

    def delete_chat(self, cid):
        if self.busy and cid == self.active_chat:
            return
        self.store.delete_chat(cid)
        if cid == self.active_chat:
            self.active_chat = None
            self.new_chat()

    def copy_chat(self):
        Clipboard.copy(core.chat_as_text(self.messages))
        self.say("Chat copied to clipboard.")


if __name__ == "__main__":
    FridayApp().run()
