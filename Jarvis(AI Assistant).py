from dotenv import load_dotenv
import os
import tkinter as tk
from google import genai
from gtts import gTTS
import pygame
from tkinter import ttk, messagebox, simpledialog
import speech_recognition as sr
import pyttsx3
import datetime
import threading
import requests
import webbrowser
import pywhatkit
import cv2
import time
import urllib.parse
import ctypes
import fitz  # PyMuPDF for PDF reading
from PIL import Image, ImageTk

# ============================================================
# CONFIG / GLOBAL STATE
# ============================================================

recognizer = sr.Recognizer()
engine = pyttsx3.init()

# Load environment variables from the .env file
load_dotenv()

# Retrieve the News API key dynamically
news_api = os.getenv("NEWS_API_KEY")

if not news_api:
    print("Warning: NEWS_API_KEY is not set in the .env file.")

voice_language = 'en'
voice_gender = 'male'
speech_volume = 1.0
reminders = []  # list of (time_str, message)

conversation_file = "conversation_history.txt"
conversation_history = []

listening_for_activation = False

# ============================================================
# COLOR PALETTE — modern dark UI
# ============================================================

BG_APP = "#0f1420"          # overall app background
BG_SIDEBAR = "#141a2b"      # left nav
BG_PANEL = "#1a2136"        # content panels / cards
BG_PANEL_ALT = "#202942"    # secondary cards
BG_INPUT = "#232c47"        # input fields

ACCENT = "#6c8cff"          # primary accent (indigo/blue)
ACCENT_HOVER = "#5676e6"
ACCENT_SOFT = "#2a3560"

USER_BUBBLE = "#3355d8"
JARVIS_BUBBLE = "#1f2c4d"
SYSTEM_TEXT = "#8b93ad"

TEXT_MAIN = "#f2f4fb"
TEXT_MUTED = "#8b93ad"
TEXT_FAINT = "#5b6482"

SUCCESS = "#4ade80"
WARN = "#fbbf24"
DANGER = "#f87171"

FONT_FAMILY = "Segoe UI"

# ============================================================
# HELPERS: rounded shapes on canvas
# ============================================================

def round_rect(canvas, x1, y1, x2, y2, radius=14, **kwargs):
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# ============================================================
# CORE / BACKEND LOGIC  (unchanged behavior from original app)
# ============================================================

def load_conversation_history():
    global conversation_history
    if os.path.exists(conversation_file):
        with open(conversation_file, "r", encoding="utf-8") as f:
            conversation_history = f.read().splitlines()


def save_conversation_history():
    with open(conversation_file, "w", encoding="utf-8") as f:
        for line in conversation_history:
            f.write(f"{line}\n")


def add_to_history(role, text):
    timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M]")
    conversation_history.append(f"{timestamp} {role}: {text}")
    save_conversation_history()
    add_chat_bubble(text, role)
    refresh_history_view()


def speak(text):
    global voice_language
    # Unique filename per call avoids clashing with a previous file that
    # Windows may still have a lingering handle on.
    filename = f"temp_{int(time.time() * 1000)}.mp3"
    used_gtts = False
    try:
        tts = gTTS(text=text, lang=voice_language)
        tts.save(filename)
        pygame.mixer.init()
        pygame.mixer.music.load(filename)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
        used_gtts = True
    except Exception as e:
        print(f"gTTS/Pygame error: {e}. Falling back to pyttsx3.")
        engine.say(text)
        engine.runAndWait()
    finally:
        if used_gtts and pygame.mixer.get_init():
            if hasattr(pygame.mixer.music, "unload"):
                pygame.mixer.music.unload()
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        if os.path.exists(filename):
            # Retry a few times in case the OS hasn't released the file handle yet.
            for attempt in range(5):
                try:
                    os.remove(filename)
                    break
                except PermissionError:
                    time.sleep(0.2)
                except Exception as e:
                    print(f"Could not delete {filename}: {e}")
                    break


def aiProcess(command):
    update_status("Jarvis is thinking...", busy=True)
    try:
        # Fetch key directly from environment variables (.env)
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "Gemini API key is missing. Please add GEMINI_API_KEY to your .env file."

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=command,
        )
        return response.text
    except Exception as e:
        print(f"AI processing error: {e}")
        return "Sorry, I couldn't process that right now."


def open_camera_and_capture():
    try:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            speak("Camera could not be opened.")
            update_status("Camera error.")
            return
        speak("Camera opened. Capturing photo in 3 seconds.")
        update_status("Camera active, capturing photo...", busy=True)
        time.sleep(3)
        ret, frame = cap.read()
        if ret:
            filename = f"photo_{int(time.time())}.jpg"
            cv2.imwrite(filename, frame)
            speak("Photo captured and saved.")
            update_status(f"Photo saved as {filename}")
        else:
            speak("Failed to capture image.")
            update_status("Failed to capture image.")
        cap.release()
        cv2.destroyAllWindows()
    except Exception as e:
        speak("Error using the camera.")
        update_status("Camera error.")
        print(f"Camera error: {e}")


def reminder_checker():
    while True:
        now = datetime.datetime.now().strftime("%H:%M")
        for r in reminders[:]:
            if r[0] == now:
                speak(f"Reminder: {r[1]}")
                add_chat_bubble(f"Reminder: {r[1]}", "Reminder")
                reminders.remove(r)
                root.after(0, refresh_reminders_view)
        time.sleep(30)


def set_alarm(time_str):
    def alarm_ring():
        speak("Alarm ringing now!")
        add_chat_bubble("ALARM: Ringing now!", "Alarm")

    try:
        alarm_time = datetime.datetime.strptime(time_str, "%H:%M")
        now = datetime.datetime.now()
        if alarm_time < now:
            alarm_time += datetime.timedelta(days=1)
        delay = (alarm_time - now).total_seconds()
        if delay > 0:
            threading.Timer(delay, alarm_ring).start()
            speak(f"Alarm set for {time_str}")
            add_chat_bubble(f"Alarm set for {time_str}", "System")
        else:
            speak("Invalid alarm time.")
    except ValueError:
        speak("Please provide time in HH:MM format.")


def change_voice(gender):
    global voice_gender
    voices = engine.getProperty('voices')
    if gender == "male":
        engine.setProperty('voice', voices[0].id)
        voice_gender = "male"
        speak("Voice changed to male.")
    elif gender == "female" and len(voices) > 1:
        engine.setProperty('voice', voices[1].id)
        voice_gender = "female"
        speak("Voice changed to female.")
    else:
        speak("Female voice not available.")
    add_chat_bubble(f"Voice changed to {gender}", "System")


def set_volume(level):
    global speech_volume
    speech_volume = max(0.0, min(1.0, level))
    engine.setProperty('volume', speech_volume)
    if 'volume_value_label' in globals():
        volume_value_label.config(text=f"{int(speech_volume * 100)}%")


def switch_language(lang):
    global voice_language
    if lang == "hindi":
        voice_language = 'hi'
        speak("भाषा हिंदी में बदल दी गई है।")
    elif lang == "english":
        voice_language = 'en'
        speak("Language switched to English.")
    add_chat_bubble(f"Language switched to {lang}", "System")


def control_system(command):
    if "shutdown" in command:
        speak("Shutting down your PC.")
        add_chat_bubble("Shutting down PC...", "System")
        os.system("shutdown /s /t 1")
    elif "restart" in command:
        speak("Restarting your PC.")
        add_chat_bubble("Restarting PC...", "System")
        os.system("shutdown /r /t 1")
    elif "lock screen" in command:
        speak("Locking your screen.")
        add_chat_bubble("Locking screen...", "System")
        ctypes.windll.user32.LockWorkStation()
    elif "mute" in command:
        import pyautogui
        pyautogui.press("volumemute")
        speak("System muted.")
        add_chat_bubble("System muted.", "System")
    elif "unmute" in command:
        import pyautogui
        pyautogui.press("volumemute")
        speak("System unmuted.")
        add_chat_bubble("System unmuted.", "System")
    elif "open camera" in command or "click photo" in command:
        open_camera_and_capture()
    elif "set wallpaper" in command:
        path = command.replace("set wallpaper", "").strip()
        try:
            ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 3)
            speak("Wallpaper changed.")
            add_chat_bubble("Wallpaper changed.", "System")
        except Exception as e:
            speak("Failed to set wallpaper.")
            add_chat_bubble(f"Failed to set wallpaper: {e}", "System")
    elif "open folder" in command or "open file" in command:
        path = command.replace("open folder", "").replace("open file", "").strip()
        try:
            os.startfile(path)
            speak("Opened successfully.")
            add_chat_bubble(f"Opened {path}", "System")
        except Exception as e:
            speak("Could not open the specified path.")
            add_chat_bubble(f"Could not open {path}: {e}", "System")


def get_news():
    try:
        url = f"https://newsdata.io/api/1/latest?apikey={news_api}&country=in&language=en"
        r = requests.get(url)
        data = r.json()
        articles = data.get("results", [])
        headlines = [article.get("title", "No Title") for article in articles]
        if not headlines:
            speak("Sorry, no news found.")
            add_chat_bubble("No news found.", "Jarvis")
            return
        speak("Here are the top news headlines:")
        for i, headline in enumerate(headlines[:5], 1):
            speak(f"{i}. {headline}")
            add_chat_bubble(f"{i}. {headline}", "Jarvis")
    except Exception as e:
        speak("There was an error getting the news.")
        add_chat_bubble(f"Error getting news: {e}", "Jarvis")


def read_pdf(path):
    try:
        doc = fitz.open(path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except Exception as e:
        return f"Error reading PDF: {e}"


def processCommand(c):
    c = c.lower().strip()
    output = "Command completed."

    if c in ["exit", "shutdown", "bye", "quit", "close"]:
        speak("Goodbye.")
        root.quit()
        return

    if c in ["stop", "leave it", "ignore", "nevermind", "cancel"]:
        speak("Okay.")
        output = "Command cancelled."
        add_to_history("You", c)
        add_to_history("Jarvis", output)
        update_status("Ready.")
        return

    if "increase volume" in c:
        set_volume(speech_volume + 0.1)
        speak(f"Volume set to {int(speech_volume * 100)} percent")
    elif "decrease volume" in c:
        set_volume(speech_volume - 0.1)
        speak(f"Volume set to {int(speech_volume * 100)} percent")
    elif "set volume to" in c:
        try:
            level = int(c.split("set volume to")[-1].replace("percent", "").strip()) / 100
            set_volume(level)
            speak(f"Volume set to {int(speech_volume * 100)} percent")
        except Exception:
            speak("Sorry, couldn't set volume.")
            output = "Failed to set volume."
    elif "change voice to male" in c:
        change_voice("male")
    elif "change voice to female" in c:
        change_voice("female")
    elif "speak in hindi" in c:
        switch_language("hindi")
    elif "speak in english" in c:
        switch_language("english")
    elif "set alarm for" in c:
        time_str = c.split("set alarm for")[-1].strip()
        set_alarm(time_str)
    elif "remind me at" in c:
        parts = c.split("remind me at")
        if len(parts) > 1 and " to " in parts[1]:
            time_part, message = parts[1].split(" to ")
            reminders.append((time_part.strip(), message.strip()))
            speak("Reminder saved")
            output = "Reminder saved."
            root.after(0, refresh_reminders_view)
        else:
            speak("Please specify time and message for the reminder.")
            output = "Reminder command incomplete."
    elif "open gui" in c:
        speak("I am already in GUI mode.")
        output = "Already in GUI mode."
    elif any(x in c for x in ["shutdown", "restart", "lock screen", "mute", "unmute",
                              "set wallpaper", "open file", "open folder",
                              "open camera", "click photo"]):
        control_system(c)
        output = "System control command executed."
    elif "pause video" in c:
        import pyautogui
        pyautogui.press("k")
        speak("Video paused.")
        output = "Video paused."
    elif "resume video" in c or "play video" in c:
        import pyautogui
        pyautogui.press("k")
        speak("Resuming video.")
        output = "Video resumed."
    elif "open google" in c:
        webbrowser.open("https://google.com")
        output = "Opened Google."
    elif "open facebook" in c:
        webbrowser.open("https://facebook.com")
        output = "Opened Facebook."
    elif "open linkedin" in c:
        webbrowser.open("https://linkedin.com")
        output = "Opened LinkedIn."
    elif "open youtube" in c:
        webbrowser.open("https://youtube.com")
        output = "Opened YouTube."
    elif "open instagram" in c:
        webbrowser.open("https://www.instagram.com/")
        output = "Opened Instagram."
    elif "open whatsapp" in c:
        webbrowser.open("https://web.whatsapp.com/")
        output = "Opened WhatsApp."
    elif "read my last conversation" in c:
        last_lines = conversation_history[-6:]
        if last_lines:
            for line in last_lines:
                speak(line)
                add_chat_bubble(line, "History")
            output = "Read last conversation."
        else:
            speak("No conversation history found.")
            output = "No history."
    elif "clear history" in c or "delete history" in c:
        clear_history()
        output = "History cleared."
    elif "open cricbuzz" in c:
        webbrowser.open("https://www.cricbuzz.com/")
        output = "Opened Cricbuzz."
    elif "ind vs england" in c:
        webbrowser.open("https://www.google.com/search?q=ind+vs+end+scorecard")
        output = "Opened India vs England scorecard."
    elif c.startswith("play"):
        song = c.replace("play", "").strip()
        if "on spotify" in c:
            song = song.replace("on spotify", "").strip()
            speak(f"Playing {song} on Spotify.")
            query = urllib.parse.quote(song)
            webbrowser.open(f"https://open.spotify.com/search/{query}")
            output = f"Playing {song} on Spotify."
        else:
            speak(f"Playing {song} on YouTube.")
            try:
                pywhatkit.playonyt(song)
                output = f"Playing {song} on YouTube."
            except Exception as e:
                speak("Sorry, I couldn't play the video.")
                output = f"Failed to play {song}: {e}"
    elif "news" in c or "headlines" in c:
        get_news()
        output = "Fetching news."
    elif c.startswith("summarize"):
        topic = c.replace("summarize", "").strip()
        output_ai = aiProcess(f"Summarize the following: {topic}")
        speak(output_ai)
        output = output_ai
    elif c.startswith("explain"):
        topic = c.replace("explain", "").strip()
        output_ai = aiProcess(f"Explain: {topic}")
        speak(output_ai)
        output = output_ai
    elif c.startswith("generate code"):
        topic = c.replace("generate code", "").strip()
        output_ai = aiProcess(f"Generate code to: {topic}")
        speak("Here is the code")
        output = output_ai
    elif c.startswith("read pdf"):
        path = c.replace("read pdf", "").strip()
        text = read_pdf(path)
        if text:
            speak(text[:500])
            output = "Reading PDF."
        else:
            speak("Could not read PDF or PDF is empty.")
            output = "Failed to read PDF."
    else:
        output_ai = aiProcess(c)
        speak(output_ai)
        output = output_ai

    add_to_history("You", c)
    add_to_history("Jarvis", output)
    update_status("Ready.")


def clear_history():
    conversation_history.clear()
    if os.path.exists(conversation_file):
        os.remove(conversation_file)
    speak("Conversation history has been cleared.")
    for widget in chat_scroll_frame.winfo_children():
        widget.destroy()
    refresh_history_view()


# ============================================================
# GUI — MAIN WINDOW
# ============================================================

root = tk.Tk()
root.title("Jarvis Assistant")
root.geometry("1000x680")
root.minsize(860, 560)
root.configure(bg=BG_APP)

style = ttk.Style()
style.theme_use('clam')
style.configure('.', background=BG_APP, foreground=TEXT_MAIN, font=(FONT_FAMILY, 10))
style.configure('TFrame', background=BG_APP)
style.configure('TLabel', background=BG_APP, foreground=TEXT_MAIN)
style.configure('TScrollbar', background=BG_PANEL, troughcolor=BG_APP, bordercolor=BG_APP,
                 arrowcolor=TEXT_MUTED)

# --------------------------------------------------------
# App shell: Sidebar (left) + Content (right)
# --------------------------------------------------------

app_shell = tk.Frame(root, bg=BG_APP)
app_shell.pack(fill=tk.BOTH, expand=True)

# ---------------- SIDEBAR ----------------
sidebar = tk.Frame(app_shell, bg=BG_SIDEBAR, width=210)
sidebar.pack(side=tk.LEFT, fill=tk.Y)
sidebar.pack_propagate(False)

brand_frame = tk.Frame(sidebar, bg=BG_SIDEBAR)
brand_frame.pack(fill=tk.X, pady=(24, 6), padx=20)

brand_dot = tk.Canvas(brand_frame, width=14, height=14, bg=BG_SIDEBAR, highlightthickness=0)
brand_dot.pack(side=tk.LEFT)
brand_dot.create_oval(1, 1, 13, 13, fill=ACCENT, outline="")

brand_label = tk.Label(brand_frame, text="  JARVIS", bg=BG_SIDEBAR, fg=TEXT_MAIN,
                        font=(FONT_FAMILY, 15, "bold"))
brand_label.pack(side=tk.LEFT)

subtitle = tk.Label(sidebar, text="AI Desktop Assistant", bg=BG_SIDEBAR, fg=TEXT_FAINT,
                     font=(FONT_FAMILY, 9))
subtitle.pack(anchor="w", padx=20, pady=(0, 24))

nav_buttons = {}
pages = {}


def make_nav_button(parent, key, label_text, icon_char):
    btn_frame = tk.Frame(parent, bg=BG_SIDEBAR, cursor="hand2")
    btn_frame.pack(fill=tk.X, padx=12, pady=3)

    indicator = tk.Frame(btn_frame, bg=BG_SIDEBAR, width=4)
    indicator.pack(side=tk.LEFT, fill=tk.Y)

    inner = tk.Frame(btn_frame, bg=BG_SIDEBAR)
    inner.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0), pady=9)

    icon_lbl = tk.Label(inner, text=icon_char, bg=BG_SIDEBAR, fg=TEXT_MUTED, font=(FONT_FAMILY, 12))
    icon_lbl.pack(side=tk.LEFT)
    text_lbl = tk.Label(inner, text="  " + label_text, bg=BG_SIDEBAR, fg=TEXT_MUTED,
                         font=(FONT_FAMILY, 10, "bold"))
    text_lbl.pack(side=tk.LEFT)

    widgets = [btn_frame, inner, icon_lbl, text_lbl]

    def on_click(event=None):
        show_page(key)

    def on_enter(event=None):
        if active_page.get() != key:
            for w in widgets:
                w.config(bg=BG_PANEL_ALT)
            indicator.config(bg=BG_PANEL_ALT)

    def on_leave(event=None):
        if active_page.get() != key:
            for w in widgets:
                w.config(bg=BG_SIDEBAR)
            indicator.config(bg=BG_SIDEBAR)

    for w in widgets + [btn_frame]:
        w.bind("<Button-1>", on_click)
        w.bind("<Enter>", on_enter)
        w.bind("<Leave>", on_leave)

    nav_buttons[key] = {"widgets": widgets, "indicator": indicator,
                         "icon": icon_lbl, "text": text_lbl}
    return btn_frame


active_page = tk.StringVar(value="chat")

make_nav_button(sidebar, "chat", "Chat", "\u25CF")
make_nav_button(sidebar, "reminders", "Reminders & Alarms", "\u23F0")
make_nav_button(sidebar, "settings", "Settings", "\u2699")
make_nav_button(sidebar, "history", "History", "\u2261")

sidebar_spacer = tk.Frame(sidebar, bg=BG_SIDEBAR)
sidebar_spacer.pack(fill=tk.BOTH, expand=True)

status_card = tk.Frame(sidebar, bg=BG_PANEL_ALT)
status_card.pack(fill=tk.X, padx=14, pady=16)

status_dot = tk.Canvas(status_card, width=10, height=10, bg=BG_PANEL_ALT, highlightthickness=0)
status_dot.grid(row=0, column=0, padx=(12, 6), pady=12)
status_dot_id = status_dot.create_oval(1, 1, 9, 9, fill=SUCCESS, outline="")

status_label = tk.Label(status_card, text="Initializing...", bg=BG_PANEL_ALT, fg=TEXT_MUTED,
                         font=(FONT_FAMILY, 8), wraplength=140, justify=tk.LEFT)
status_label.grid(row=0, column=1, sticky="w", pady=12, padx=(0, 10))
status_card.grid_columnconfigure(1, weight=1)

# ---------------- CONTENT AREA ----------------
content_area = tk.Frame(app_shell, bg=BG_APP)
content_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)


def show_page(key):
    active_page.set(key)
    for k, page in pages.items():
        page.pack_forget()
    pages[key].pack(fill=tk.BOTH, expand=True)
    for k, b in nav_buttons.items():
        color = ACCENT_SOFT if k == key else BG_SIDEBAR
        fg = TEXT_MAIN if k == key else TEXT_MUTED
        for w in b["widgets"]:
            w.config(bg=color)
        b["indicator"].config(bg=ACCENT if k == key else BG_SIDEBAR)
        b["icon"].config(fg=fg)
        b["text"].config(fg=fg)


def update_status(message, busy=False):
    status_label.config(text=message)
    status_dot.itemconfig(status_dot_id, fill=WARN if busy else SUCCESS)


# ============================================================
# PAGE 1 — CHAT
# ============================================================

chat_page = tk.Frame(content_area, bg=BG_APP)
pages["chat"] = chat_page

chat_header = tk.Frame(chat_page, bg=BG_APP)
chat_header.pack(fill=tk.X, padx=24, pady=(24, 10))
tk.Label(chat_header, text="Conversation", bg=BG_APP, fg=TEXT_MAIN,
          font=(FONT_FAMILY, 16, "bold")).pack(side=tk.LEFT)
tk.Label(chat_header, text="  Talk to Jarvis by typing or using voice", bg=BG_APP, fg=TEXT_FAINT,
          font=(FONT_FAMILY, 9)).pack(side=tk.LEFT, pady=(6, 0))

# Scrollable chat log
chat_container = tk.Frame(chat_page, bg=BG_PANEL)
chat_container.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 10))

chat_canvas = tk.Canvas(chat_container, bg=BG_PANEL, highlightthickness=0)
chat_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

chat_vsb = ttk.Scrollbar(chat_container, orient="vertical", command=chat_canvas.yview)
chat_vsb.pack(side=tk.RIGHT, fill=tk.Y)
chat_canvas.configure(yscrollcommand=chat_vsb.set)

chat_scroll_frame = tk.Frame(chat_canvas, bg=BG_PANEL)
chat_window = chat_canvas.create_window((0, 0), window=chat_scroll_frame, anchor="nw")


def on_chat_frame_configure(event):
    chat_canvas.configure(scrollregion=chat_canvas.bbox("all"))


def on_chat_canvas_resize(event):
    chat_canvas.itemconfig(chat_window, width=event.width)


chat_scroll_frame.bind("<Configure>", on_chat_frame_configure)
chat_canvas.bind("<Configure>", on_chat_canvas_resize)


def _on_mousewheel(event):
    chat_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


chat_canvas.bind_all("<MouseWheel>", _on_mousewheel)

BUBBLE_STYLES = {
    "You": {"align": "e", "bg": USER_BUBBLE, "fg": "#ffffff", "label": "You"},
    "Jarvis": {"align": "w", "bg": JARVIS_BUBBLE, "fg": TEXT_MAIN, "label": "Jarvis"},
    "System": {"align": "center", "bg": None, "fg": SYSTEM_TEXT, "label": "System"},
    "Reminder": {"align": "center", "bg": None, "fg": WARN, "label": "Reminder"},
    "Alarm": {"align": "center", "bg": None, "fg": DANGER, "label": "Alarm"},
    "History": {"align": "center", "bg": None, "fg": TEXT_MUTED, "label": "History"},
}


def add_chat_bubble(text, role):
    style_info = BUBBLE_STYLES.get(role, BUBBLE_STYLES["System"])

    row = tk.Frame(chat_scroll_frame, bg=BG_PANEL)
    row.pack(fill=tk.X, padx=16, pady=5)

    if style_info["bg"] is None:
        # centered system-style line
        lbl = tk.Label(row, text=text, bg=BG_PANEL, fg=style_info["fg"],
                        font=(FONT_FAMILY, 8, "italic"))
        lbl.pack(anchor="center")
    else:
        bubble_wrap = tk.Frame(row, bg=BG_PANEL)
        if style_info["align"] == "e":
            bubble_wrap.pack(anchor="e")
        else:
            bubble_wrap.pack(anchor="w")

        name_lbl = tk.Label(bubble_wrap, text=style_info["label"], bg=BG_PANEL,
                             fg=TEXT_FAINT, font=(FONT_FAMILY, 7, "bold"))
        name_lbl.pack(anchor=style_info["align"] if style_info["align"] != "center" else "w")

        bubble = tk.Label(bubble_wrap, text=text, bg=style_info["bg"], fg=style_info["fg"],
                           font=(FONT_FAMILY, 10), wraplength=520, justify=tk.LEFT,
                           padx=14, pady=9)
        bubble.pack()

    chat_canvas.update_idletasks()
    chat_canvas.configure(scrollregion=chat_canvas.bbox("all"))
    chat_canvas.yview_moveto(1.0)


# Input bar
input_bar = tk.Frame(chat_page, bg=BG_PANEL)
input_bar.pack(fill=tk.X, padx=24, pady=(0, 24))

command_input = tk.Entry(input_bar, bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
                          relief="flat", font=(FONT_FAMILY, 11))
command_input.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=10, padx=(12, 8), pady=10)

placeholder_text = "Type your command here..."
command_input.insert(0, placeholder_text)
command_input.config(fg=TEXT_FAINT)


def on_focus_in(event):
    if command_input.get() == placeholder_text:
        command_input.delete(0, tk.END)
        command_input.config(fg=TEXT_MAIN)


def on_focus_out(event):
    if not command_input.get():
        command_input.insert(0, placeholder_text)
        command_input.config(fg=TEXT_FAINT)


command_input.bind("<FocusIn>", on_focus_in)
command_input.bind("<FocusOut>", on_focus_out)
command_input.bind("<Return>", lambda event: send_command_from_input())


def styled_button(parent, text, bg, hover_bg, command, fg="#ffffff", width=None):
    btn = tk.Label(parent, text=text, bg=bg, fg=fg, font=(FONT_FAMILY, 10, "bold"),
                    padx=16, pady=10, cursor="hand2")
    if width:
        btn.config(width=width)
    btn.bind("<Button-1>", lambda e: command())
    btn.bind("<Enter>", lambda e: btn.config(bg=hover_bg))
    btn.bind("<Leave>", lambda e: btn.config(bg=bg))
    return btn


def send_command_from_input():
    command = command_input.get().strip()
    if command and command != placeholder_text:
        command_input.delete(0, tk.END)
        update_status("Processing command...", busy=True)
        threading.Thread(target=processCommand, args=(command,), daemon=True).start()
    else:
        update_status("Please enter a command.")


send_btn = styled_button(input_bar, "Send", ACCENT, ACCENT_HOVER, send_command_from_input)
send_btn.pack(side=tk.LEFT, padx=(0, 8), pady=10)


def start_listening_thread():
    update_status("Listening... say 'Jarvis'", busy=True)
    voice_btn.config(state="disabled")
    threading.Thread(target=listen_for_activation, daemon=True).start()


voice_btn = styled_button(input_bar, "\U0001F3A4 Voice", SUCCESS, "#22c55e", start_listening_thread,
                           fg="#0f1420")
voice_btn.pack(side=tk.LEFT, padx=(0, 12), pady=10)


def listen_for_activation():
    global listening_for_activation
    listening_for_activation = True
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=1)
            update_status("Listening for 'Jarvis'...", busy=True)
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=3)
            word = recognizer.recognize_google(audio).lower()
            if "jarvis" in word:
                update_status("Jarvis activated. Listening for command...", busy=True)
                speak("Yes, I am listening.")
                listen_for_command()
            else:
                update_status("Activation word not detected. Ready.")
    except sr.WaitTimeoutError:
        update_status("No speech detected. Ready.")
    except sr.UnknownValueError:
        update_status("Could not understand audio. Ready.")
    except Exception as e:
        update_status(f"Microphone error. Ready.")
        print(f"Microphone error: {e}")
    finally:
        listening_for_activation = False
        voice_btn.config(state="normal")


def listen_for_command():
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=1)
            update_status("Listening for your command...", busy=True)
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=5)
            command = recognizer.recognize_google(audio)
            update_status("Processing command...", busy=True)
            threading.Thread(target=processCommand, args=(command,), daemon=True).start()
    except sr.WaitTimeoutError:
        update_status("No command detected. Ready.")
    except sr.UnknownValueError:
        update_status("Could not understand command. Ready.")
    except Exception as e:
        update_status("Command input error. Ready.")
        print(f"Command input error: {e}")


# ============================================================
# PAGE 2 — REMINDERS & ALARMS
# ============================================================

reminders_page = tk.Frame(content_area, bg=BG_APP)
pages["reminders"] = reminders_page

tk.Label(reminders_page, text="Reminders & Alarms", bg=BG_APP, fg=TEXT_MAIN,
          font=(FONT_FAMILY, 16, "bold")).pack(anchor="w", padx=24, pady=(24, 4))
tk.Label(reminders_page, text="Set alarms or reminders — Jarvis will speak them at the right time.",
          bg=BG_APP, fg=TEXT_FAINT, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=24, pady=(0, 16))

rem_body = tk.Frame(reminders_page, bg=BG_APP)
rem_body.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 24))

# --- Add reminder card ---
add_card = tk.Frame(rem_body, bg=BG_PANEL)
add_card.pack(fill=tk.X, pady=(0, 16))
add_card_inner = tk.Frame(add_card, bg=BG_PANEL)
add_card_inner.pack(fill=tk.X, padx=18, pady=16)

tk.Label(add_card_inner, text="New Reminder", bg=BG_PANEL, fg=TEXT_MAIN,
          font=(FONT_FAMILY, 11, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

tk.Label(add_card_inner, text="Time (HH:MM)", bg=BG_PANEL, fg=TEXT_MUTED,
          font=(FONT_FAMILY, 8)).grid(row=1, column=0, sticky="w")
rem_time_entry = tk.Entry(add_card_inner, bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
                            relief="flat", width=10, font=(FONT_FAMILY, 10))
rem_time_entry.grid(row=2, column=0, sticky="w", ipady=6, padx=(0, 12))

tk.Label(add_card_inner, text="Message", bg=BG_PANEL, fg=TEXT_MUTED,
          font=(FONT_FAMILY, 8)).grid(row=1, column=1, sticky="w")
rem_msg_entry = tk.Entry(add_card_inner, bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
                           relief="flat", font=(FONT_FAMILY, 10))
rem_msg_entry.grid(row=2, column=1, sticky="ew", ipady=6, padx=(0, 12))
add_card_inner.grid_columnconfigure(1, weight=1)


def add_reminder_clicked():
    t = rem_time_entry.get().strip()
    m = rem_msg_entry.get().strip()
    if not t or not m:
        messagebox.showwarning("Missing info", "Please provide both a time (HH:MM) and a message.")
        return
    try:
        datetime.datetime.strptime(t, "%H:%M")
    except ValueError:
        messagebox.showwarning("Invalid time", "Please use HH:MM 24-hour format, e.g. 14:30")
        return
    reminders.append((t, m))
    rem_time_entry.delete(0, tk.END)
    rem_msg_entry.delete(0, tk.END)
    refresh_reminders_view()
    add_chat_bubble(f"Reminder set for {t}: {m}", "System")


tk.Label(add_card_inner, text=" ", bg=BG_PANEL).grid(row=1, column=2)
add_rem_btn = styled_button(add_card_inner, "+ Add Reminder", ACCENT, ACCENT_HOVER, add_reminder_clicked)
add_rem_btn.grid(row=2, column=2, sticky="w")

# --- Alarm card ---
alarm_card = tk.Frame(rem_body, bg=BG_PANEL)
alarm_card.pack(fill=tk.X, pady=(0, 16))
alarm_inner = tk.Frame(alarm_card, bg=BG_PANEL)
alarm_inner.pack(fill=tk.X, padx=18, pady=16)

tk.Label(alarm_inner, text="Set an Alarm", bg=BG_PANEL, fg=TEXT_MAIN,
          font=(FONT_FAMILY, 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
tk.Label(alarm_inner, text="Time (HH:MM)", bg=BG_PANEL, fg=TEXT_MUTED,
          font=(FONT_FAMILY, 8)).grid(row=1, column=0, sticky="w")
alarm_time_entry = tk.Entry(alarm_inner, bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
                              relief="flat", width=10, font=(FONT_FAMILY, 10))
alarm_time_entry.grid(row=2, column=0, sticky="w", ipady=6, padx=(0, 12))


def set_alarm_clicked():
    t = alarm_time_entry.get().strip()
    if not t:
        messagebox.showwarning("Missing info", "Please provide a time (HH:MM).")
        return
    threading.Thread(target=set_alarm, args=(t,), daemon=True).start()
    alarm_time_entry.delete(0, tk.END)


set_alarm_btn = styled_button(alarm_inner, "Set Alarm", WARN, "#f59e0b", set_alarm_clicked, fg="#0f1420")
set_alarm_btn.grid(row=2, column=1, sticky="w")

# --- Active reminders list ---
tk.Label(rem_body, text="Active Reminders", bg=BG_APP, fg=TEXT_MAIN,
          font=(FONT_FAMILY, 11, "bold")).pack(anchor="w", pady=(4, 8))

reminders_list_frame = tk.Frame(rem_body, bg=BG_APP)
reminders_list_frame.pack(fill=tk.BOTH, expand=True)


def refresh_reminders_view():
    for w in reminders_list_frame.winfo_children():
        w.destroy()
    if not reminders:
        tk.Label(reminders_list_frame, text="No active reminders.", bg=BG_APP, fg=TEXT_FAINT,
                  font=(FONT_FAMILY, 9, "italic")).pack(anchor="w", pady=6)
        return
    for idx, (t, m) in enumerate(reminders):
        row = tk.Frame(reminders_list_frame, bg=BG_PANEL_ALT)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text=t, bg=BG_PANEL_ALT, fg=ACCENT, font=(FONT_FAMILY, 10, "bold"),
                  width=8).pack(side=tk.LEFT, padx=(12, 6), pady=10)
        tk.Label(row, text=m, bg=BG_PANEL_ALT, fg=TEXT_MAIN, font=(FONT_FAMILY, 10)
                  ).pack(side=tk.LEFT, fill=tk.X, expand=True, pady=10)

        def make_remove(i=idx):
            def _remove():
                if i < len(reminders):
                    reminders.pop(i)
                    refresh_reminders_view()
            return _remove

        remove_lbl = tk.Label(row, text="Remove", bg=BG_PANEL_ALT, fg=DANGER,
                                font=(FONT_FAMILY, 8, "bold"), cursor="hand2")
        remove_lbl.pack(side=tk.RIGHT, padx=12)
        remove_lbl.bind("<Button-1>", lambda e, f=make_remove(): f())


# ============================================================
# PAGE 3 — SETTINGS
# ============================================================

settings_page = tk.Frame(content_area, bg=BG_APP)
pages["settings"] = settings_page

tk.Label(settings_page, text="Settings", bg=BG_APP, fg=TEXT_MAIN,
          font=(FONT_FAMILY, 16, "bold")).pack(anchor="w", padx=24, pady=(24, 4))
tk.Label(settings_page, text="Personalize how Jarvis sounds and responds.",
          bg=BG_APP, fg=TEXT_FAINT, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=24, pady=(0, 16))

settings_body = tk.Frame(settings_page, bg=BG_APP)
settings_body.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 24))


def settings_card(parent, title):
    card = tk.Frame(parent, bg=BG_PANEL)
    card.pack(fill=tk.X, pady=(0, 16))
    inner = tk.Frame(card, bg=BG_PANEL)
    inner.pack(fill=tk.X, padx=18, pady=16)
    tk.Label(inner, text=title, bg=BG_PANEL, fg=TEXT_MAIN,
              font=(FONT_FAMILY, 11, "bold")).pack(anchor="w", pady=(0, 12))
    return inner


# Voice gender
voice_card = settings_card(settings_body, "Voice Gender")
gender_var = tk.StringVar(value=voice_gender)


def on_gender_change():
    change_voice(gender_var.get())


gender_row = tk.Frame(voice_card, bg=BG_PANEL)
gender_row.pack(anchor="w")
tk.Radiobutton(gender_row, text="Male", variable=gender_var, value="male",
               command=on_gender_change, bg=BG_PANEL, fg=TEXT_MAIN, selectcolor=BG_INPUT,
               activebackground=BG_PANEL, activeforeground=TEXT_MAIN,
               font=(FONT_FAMILY, 10)).pack(side=tk.LEFT, padx=(0, 20))
tk.Radiobutton(gender_row, text="Female", variable=gender_var, value="female",
               command=on_gender_change, bg=BG_PANEL, fg=TEXT_MAIN, selectcolor=BG_INPUT,
               activebackground=BG_PANEL, activeforeground=TEXT_MAIN,
               font=(FONT_FAMILY, 10)).pack(side=tk.LEFT)

# Language
lang_card = settings_card(settings_body, "Language")
lang_var = tk.StringVar(value="english")


def on_lang_change():
    switch_language(lang_var.get())


lang_row = tk.Frame(lang_card, bg=BG_PANEL)
lang_row.pack(anchor="w")
tk.Radiobutton(lang_row, text="English", variable=lang_var, value="english",
               command=on_lang_change, bg=BG_PANEL, fg=TEXT_MAIN, selectcolor=BG_INPUT,
               activebackground=BG_PANEL, activeforeground=TEXT_MAIN,
               font=(FONT_FAMILY, 10)).pack(side=tk.LEFT, padx=(0, 20))
tk.Radiobutton(lang_row, text="Hindi", variable=lang_var, value="hindi",
               command=on_lang_change, bg=BG_PANEL, fg=TEXT_MAIN, selectcolor=BG_INPUT,
               activebackground=BG_PANEL, activeforeground=TEXT_MAIN,
               font=(FONT_FAMILY, 10)).pack(side=tk.LEFT)

# Volume
volume_card = settings_card(settings_body, "Speech Volume")
volume_row = tk.Frame(volume_card, bg=BG_PANEL)
volume_row.pack(fill=tk.X)

volume_scale = tk.Scale(volume_row, from_=0, to=100, orient=tk.HORIZONTAL, bg=BG_PANEL,
                          fg=TEXT_MAIN, troughcolor=BG_INPUT, highlightthickness=0,
                          activebackground=ACCENT, font=(FONT_FAMILY, 8),
                          command=lambda v: set_volume(int(v) / 100), length=280)
volume_scale.set(int(speech_volume * 100))
volume_scale.pack(side=tk.LEFT)

volume_value_label = tk.Label(volume_row, text=f"{int(speech_volume * 100)}%", bg=BG_PANEL,
                                fg=ACCENT, font=(FONT_FAMILY, 10, "bold"))
volume_value_label.pack(side=tk.LEFT, padx=12)

# Quick actions
actions_card = settings_card(settings_body, "Quick Actions")
actions_row = tk.Frame(actions_card, bg=BG_PANEL)
actions_row.pack(anchor="w")


def quick_action(cmd_text):
    threading.Thread(target=processCommand, args=(cmd_text,), daemon=True).start()


qa_specs = [
    ("Get News", "news"),
    ("Open Camera", "open camera"),
    ("Play on YouTube", None),  # handled separately
]

styled_button(actions_row, "Get News", ACCENT_SOFT, BG_PANEL_ALT,
              lambda: quick_action("news"), fg=TEXT_MAIN).pack(side=tk.LEFT, padx=(0, 10))
styled_button(actions_row, "Open Camera", ACCENT_SOFT, BG_PANEL_ALT,
              lambda: quick_action("open camera"), fg=TEXT_MAIN).pack(side=tk.LEFT, padx=(0, 10))


def prompt_play_song():
    song = simpledialog.askstring("Play a song", "What would you like to play?")
    if song:
        quick_action(f"play {song}")


styled_button(actions_row, "Play a Song", ACCENT_SOFT, BG_PANEL_ALT,
              prompt_play_song, fg=TEXT_MAIN).pack(side=tk.LEFT)


# ============================================================
# PAGE 4 — HISTORY
# ============================================================

history_page = tk.Frame(content_area, bg=BG_APP)
pages["history"] = history_page

hist_header = tk.Frame(history_page, bg=BG_APP)
hist_header.pack(fill=tk.X, padx=24, pady=(24, 10))
tk.Label(hist_header, text="Conversation History", bg=BG_APP, fg=TEXT_MAIN,
          font=(FONT_FAMILY, 16, "bold")).pack(side=tk.LEFT)


def clear_history_clicked():
    if messagebox.askyesno("Clear history", "This will permanently delete all saved conversation history. Continue?"):
        clear_history()


styled_button(hist_header, "Clear History", DANGER, "#ef4444", clear_history_clicked
              ).pack(side=tk.RIGHT)

history_container = tk.Frame(history_page, bg=BG_PANEL)
history_container.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 24))

history_text = tk.Text(history_container, wrap=tk.WORD, bg=BG_PANEL, fg=TEXT_MUTED,
                         font=("Consolas", 9), relief="flat", padx=16, pady=16,
                         insertbackground=TEXT_MAIN, state=tk.DISABLED)
history_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
history_vsb = ttk.Scrollbar(history_container, orient="vertical", command=history_text.yview)
history_vsb.pack(side=tk.RIGHT, fill=tk.Y)
history_text.configure(yscrollcommand=history_vsb.set)


def refresh_history_view():
    history_text.config(state=tk.NORMAL)
    history_text.delete("1.0", tk.END)
    if not conversation_history:
        history_text.insert(tk.END, "No conversation history yet.")
    else:
        history_text.insert(tk.END, "\n".join(conversation_history))
    history_text.see(tk.END)
    history_text.config(state=tk.DISABLED)


# ============================================================
# INITIAL SETUP
# ============================================================

load_conversation_history()
refresh_history_view()
refresh_reminders_view()
show_page("chat")

threading.Thread(target=reminder_checker, daemon=True).start()

update_status("Initializing Jarvis...", busy=True)
add_chat_bubble("Hi, I'm Jarvis. Type a command below or hit Voice to talk to me.", "Jarvis")
threading.Thread(target=lambda: (speak("Initializing Jarvis."),
                                  root.after(0, lambda: update_status("Ready. Say 'Jarvis' to activate."))),
                  daemon=True).start()

root.mainloop()