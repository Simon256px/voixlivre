"""VoixLivre : écouter un ebook (EPUB, TXT, PDF) avec Qwen3-TTS CustomVoice.

Lancement :  python voixlivre.py [livre.epub]
"""
import json
import queue
import re
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import sounddevice as sd

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
SPEAKERS = ["Serena", "Vivian", "Ryan", "Aiden", "Eric", "Dylan", "Uncle_Fu", "Ono_Anna", "Sohee"]
# Styles proposés dans la liste « Style » (le champ reste modifiable à la main).
# Aucune voix du modèle n'est francophone native : préciser « sans accent » aide beaucoup.
STYLES = [
    "Français natif de France, sans aucun accent étranger, diction claire et neutre.",
    "Narrateur de livre audio français, voix posée et chaleureuse, sans accent.",
    "Lecture neutre et naturelle en français standard, rythme régulier, sans accent.",
    "Voix douce et calme pour une lecture du soir, français sans accent.",
    "Conteur expressif et vivant, qui fait vivre les dialogues, français sans accent.",
    "Voix grave et solennelle, lecture lente et articulée, français sans accent.",
    "Lecture dynamique et un peu plus rapide, français standard sans accent.",
    "Conteur pour enfants, joyeux et animé, français sans accent.",
    "Ton sérieux et informatif, comme un documentaire, français sans accent.",
    "Voix intime et murmurée, pour le suspense, français sans accent.",
    "",
]
LANGUAGES = ["French", "English", "German", "Spanish", "Italian", "Portuguese",
             "Russian", "Chinese", "Japanese", "Korean", "Auto"]
PROGRESS_FILE = Path.home() / ".voixlivre.json"
MAX_CHARS = 300      # taille max d'un segment envoyé au modèle
BATCH = 4            # segments générés ensemble (≈3× plus rapide que le temps réel sur GPU)
PREFETCH = 8         # segments préparés à l'avance


# ---------------------------------------------------------------- lecture du livre

def _clean(text):
    text = re.sub(r"[ \t\xa0​]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def load_epub(path):
    from ebooklib import epub, ITEM_DOCUMENT
    from bs4 import BeautifulSoup

    book = epub.read_epub(str(path), options={"ignore_ncx": True})
    chapters = []
    for idref, _ in book.spine:
        item = book.get_item_with_id(idref)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        text = _clean(soup.get_text("\n"))
        if len(text) < 40:
            continue
        head = soup.find(["h1", "h2", "h3"])
        title = head.get_text(" ", strip=True) if head else text.split("\n", 1)[0]
        chapters.append((title[:70] or f"Section {len(chapters) + 1}", text))
    return chapters


def load_txt(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    parts = re.split(r"\n(?=\s*(?:chapitre|chapter|partie|livre)\b[^\n]{0,60}\n)", text, flags=re.I)
    chapters = []
    for p in parts:
        p = _clean(p)
        if p:
            chapters.append((p.split("\n", 1)[0][:70], p))
    return chapters


def load_pdf(path, pages_per_section=10):
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(pg.extract_text() or "") for pg in reader.pages]
    chapters = []
    for start in range(0, len(pages), pages_per_section):
        # recolle les lignes coupées à l'intérieur des paragraphes
        text = "\n".join(pages[start:start + pages_per_section])
        text = re.sub(r"-\n(\w)", r"\1", text)
        text = re.sub(r"(?<![.!?:»])\n(?!\n)", " ", text)
        text = _clean(text)
        if text:
            end = min(start + pages_per_section, len(pages))
            chapters.append((f"Pages {start + 1}-{end}", text))
    return chapters


def load_book(path):
    ext = Path(path).suffix.lower()
    loader = {".epub": load_epub, ".txt": load_txt, ".pdf": load_pdf}.get(ext)
    if loader is None:
        raise ValueError(f"Format non pris en charge : {ext}")
    chapters = loader(path)
    if not chapters:
        raise ValueError("Aucun texte lisible trouvé dans ce fichier.")
    return chapters


def split_chunks(text):
    """Découpe un chapitre en segments [(texte, fin_de_paragraphe)]."""
    chunks = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        pieces = []
        for sentence in re.split(r"(?<=[.!?…])\s+", para):
            while len(sentence) > MAX_CHARS:
                cut = max(sentence.rfind(sep, 0, MAX_CHARS) for sep in (", ", "; ", " : ", " — "))
                if cut < MAX_CHARS // 3:
                    cut = sentence.rfind(" ", 0, MAX_CHARS)
                if cut <= 0:
                    cut = MAX_CHARS
                pieces.append(sentence[:cut + 1].strip())
                sentence = sentence[cut + 1:].strip()
            if sentence:
                pieces.append(sentence)
        current = ""
        for piece in pieces:
            if current and len(current) + len(piece) + 1 > MAX_CHARS:
                chunks.append((current, False))
                current = piece
            else:
                current = f"{current} {piece}".strip()
        if current:
            chunks.append((current, True))
    return chunks


# ---------------------------------------------------------------- moteur audio

def _trim(wav, sr, threshold=0.01):
    """Retire les silences en début et fin de segment."""
    loud = np.flatnonzero(np.abs(wav) > threshold)
    if loud.size == 0:
        return wav
    pad = int(sr * 0.05)
    return wav[max(loud[0] - pad, 0):loud[-1] + pad]


class _Aborted(Exception):
    """Levée dans le modèle pour interrompre une génération devenue inutile."""


class Narrator:
    """Un thread génère l'audio en avance (par lots, mis en cache), un autre le joue.

    Les méthodes publiques sont appelées depuis l'interface ; tout l'état partagé
    est protégé par `self.cond`.
    """

    def __init__(self, post):
        self.post = post                      # callback vers l'interface
        self.model = None
        self.cond = threading.Condition()
        self.chunks = []                      # chunks[chapitre] -> [(texte, fin_de_paragraphe)]
        self.offsets = []                     # index global du 1er segment de chaque chapitre
        self.voice = {"speaker": SPEAKERS[0], "language": LANGUAGES[0], "instruct": ""}
        self.pos = (0, 0)                     # segment à lire
        self.active = False                   # lecture demandée
        self.paused = False
        self.cache = {}                       # (chapitre, segment) -> (audio, sr)
        self.gen_epoch = 0                    # change quand la génération en cours devient inutile
        self.running_epoch = None
        self.play_epoch = 0                   # change à chaque saut demandé
        threading.Thread(target=self._generator, daemon=True).start()
        threading.Thread(target=self._player, daemon=True).start()

    # -- modèle
    def load_model(self):
        import torch
        from qwen_tts import Qwen3TTSModel

        cuda = torch.cuda.is_available()
        self.post("status", "Chargement du modèle sur " + ("GPU…" if cuda else "CPU (lent)…"))
        model = Qwen3TTSModel.from_pretrained(
            MODEL_ID,
            device_map="cuda:0" if cuda else "cpu",
            dtype=torch.bfloat16 if cuda else torch.float32,
            attn_implementation="sdpa",
        )
        # qwen_tts ne permet pas d'annuler une génération : on l'interrompt à l'étape suivante
        model.model.talker.register_forward_pre_hook(self._abort_hook)
        model.generate_custom_voice(text="Bonjour.", language="French", speaker=SPEAKERS[0])  # préchauffage
        with self.cond:
            self.model = model
            self.cond.notify_all()
        self.post("model_ready", cuda)

    def _abort_hook(self, module, args):
        if self.running_epoch is not None and self.running_epoch != self.gen_epoch:
            raise _Aborted()

    def synthesize(self, texts, voice):
        """Génère plusieurs segments d'un coup (bien plus rapide qu'un par un)."""
        n = len(texts)
        kwargs = {"text": texts, "language": [voice["language"]] * n, "speaker": [voice["speaker"]] * n}
        if voice["instruct"].strip():
            kwargs["instruct"] = [voice["instruct"].strip()] * n
        wavs, sr = self.model.generate_custom_voice(**kwargs)
        return [_trim(np.asarray(w, dtype=np.float32).reshape(-1), sr) for w in wavs], sr

    # -- commandes
    def load(self, chunks):
        with self.cond:
            self.chunks = chunks
            self.offsets = [int(x) for x in np.cumsum([0] + [len(c) for c in chunks])]
            self.cache.clear()
            self.active = False
            self.gen_epoch += 1
            self.play_epoch += 1
            self.cond.notify_all()

    def play(self, chapter, index):
        with self.cond:
            if index >= len(self.chunks[chapter]):
                chapter, index = self._next(chapter, index)
            if chapter >= len(self.chunks):
                return
            self.pos = (chapter, index)
            self.active, self.paused = True, False
            self.play_epoch += 1
            if (chapter, index) not in self.cache:
                self.gen_epoch += 1           # le lot en cours ne contient pas ce passage
            self._evict()
            self.cond.notify_all()

    def set_paused(self, paused):
        with self.cond:
            self.paused = paused
            self.cond.notify_all()

    def stop(self):
        with self.cond:
            self.active = False
            self.play_epoch += 1
            self.cond.notify_all()

    def set_voice(self, voice):
        with self.cond:
            if voice == self.voice:
                return
            self.voice = dict(voice)
            self.cache.clear()
            self.gen_epoch += 1
            self.cond.notify_all()

    # -- positions
    def _next(self, chapter, index):
        index += 1
        while chapter < len(self.chunks) and index >= len(self.chunks[chapter]):
            chapter, index = chapter + 1, 0
        return chapter, index

    def _linear(self, p):
        return self.offsets[p[0]] + p[1]

    def _evict(self):
        here = self._linear(self.pos)
        for p in [p for p in self.cache if not here - 3 <= self._linear(p) <= here + PREFETCH + BATCH]:
            del self.cache[p]

    def _todo(self):
        """Prochain lot à générer, ou [] s'il vaut mieux attendre."""
        if not (self.active and self.model and self.chunks):
            return []
        missing, p, reached_end = [], self.pos, False
        for _ in range(PREFETCH):
            if p not in self.cache:
                missing.append(p)
            p = self._next(*p)
            if p[0] >= len(self.chunks):
                reached_end = True
                break
        # on attend d'avoir un lot complet, sauf si le passage courant manque
        if missing and (missing[0] == self.pos or len(missing) >= BATCH or reached_end):
            return missing[:BATCH]
        return []

    # -- threads
    def _generator(self):
        while True:
            with self.cond:
                while not (todo := self._todo()):
                    self.cond.wait()
                epoch = self.running_epoch = self.gen_epoch
                voice = dict(self.voice)
                texts = [self.chunks[c][i][0] for c, i in todo]
            try:
                wavs, sr = self.synthesize(texts, voice)
            except _Aborted:
                continue
            except Exception as exc:  # noqa: BLE001
                with self.cond:
                    self.active = False
                self.post("error", f"Erreur de synthèse : {exc}")
                continue
            finally:
                self.running_epoch = None
            with self.cond:
                if epoch == self.gen_epoch:
                    for (c, i), wav in zip(todo, wavs):
                        # courte pause après chaque segment, plus longue en fin de paragraphe
                        gap = 0.5 if self.chunks[c][i][1] else 0.2
                        self.cache[(c, i)] = (np.concatenate([wav, np.zeros(int(sr * gap), np.float32)]), sr)
                self.cond.notify_all()

    def _player(self):
        stream = None
        while True:
            with self.cond:
                waiting = False
                while not (self.active and not self.paused and self.pos in self.cache):
                    if self.active and not self.paused and not waiting:
                        self.post("buffering", None)
                        waiting = True
                    self.cond.wait()
                pos, epoch = self.pos, self.play_epoch
                wav, sr = self.cache[pos]
            if stream is None or stream.samplerate != sr:
                if stream is not None:
                    stream.close()
                stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32")
                stream.start()
            self.post("playing", pos)
            block, i, interrupted = int(sr * 0.1), 0, False
            while i < len(wav):
                with self.cond:
                    while self.paused and self.play_epoch == epoch:
                        self.cond.wait()
                    if self.play_epoch != epoch:
                        interrupted = True
                        break
                stream.write(wav[i:i + block])
                i += block
            if interrupted:
                continue
            with self.cond:
                if self.play_epoch == epoch:
                    nxt = self._next(*pos)
                    if nxt[0] >= len(self.chunks):
                        self.active = False
                        self.post("finished", None)
                    else:
                        self.pos = nxt
                        self._evict()
                    self.cond.notify_all()


# ---------------------------------------------------------------- interface

class App:
    def __init__(self, root, initial=None):
        self.root = root
        self.ui_q = queue.Queue()
        self.narrator = Narrator(lambda kind, data: self.ui_q.put((kind, data)))
        self.book_path = None
        self.chapters = []
        self.current = (0, 0)
        self.playing = False
        self.paused = False
        self.displayed_chapter = None
        self.progress = self._load_progress()

        root.title("VoixLivre")
        root.geometry("980x640")
        root.minsize(700, 450)
        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._poll)

        threading.Thread(target=self._load_model_safe, daemon=True).start()
        if initial:
            root.after(200, lambda: self.open_book(initial))

    # -- construction
    def _build_ui(self):
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Big.TButton", font=("Segoe UI", 13), padding=(10, 4))

        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Button(top, text="Ouvrir un livre…", command=self.choose_file).pack(side="left")
        self.title_var = tk.StringVar(value="Aucun livre ouvert")
        ttk.Label(top, textvariable=self.title_var, font=("Segoe UI", 11, "bold")).pack(side="left", padx=12)

        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10)

        left = ttk.Frame(body)
        ttk.Label(left, text="Chapitres").pack(anchor="w")
        self.chap_list = tk.Listbox(left, activestyle="none", borderwidth=0, highlightthickness=1,
                                    font=("Segoe UI", 10), exportselection=False)
        self.chap_list.pack(fill="both", expand=True, pady=(2, 0))
        self.chap_list.bind("<Double-Button-1>", lambda e: self.play_chapter())
        self.chap_list.bind("<<ListboxSelect>>", lambda e: self._show_selected_chapter())
        body.add(left, weight=1)

        right = ttk.Frame(body)
        self.text = tk.Text(right, wrap="word", font=("Georgia", 12), padx=18, pady=12,
                            borderwidth=0, highlightthickness=1, spacing2=3, cursor="arrow")
        scroll = ttk.Scrollbar(right, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set, state="disabled")
        self.text.tag_configure("current", background="#fff1b8")
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        body.add(right, weight=4)

        controls = ttk.Frame(self.root, padding=(10, 8))
        controls.pack(fill="x")
        self.btn_prev = ttk.Button(controls, text="⏮", width=4, style="Big.TButton", command=self.prev_chunk)
        self.btn_play = ttk.Button(controls, text="▶", width=4, style="Big.TButton", command=self.toggle_play)
        self.btn_next = ttk.Button(controls, text="⏭", width=4, style="Big.TButton", command=self.next_chunk)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            b.pack(side="left", padx=2)
            b.state(["disabled"])

        ttk.Label(controls, text="Voix").pack(side="left", padx=(20, 4))
        self.speaker_var = tk.StringVar(value=self.progress.get("_voice", {}).get("speaker", SPEAKERS[0]))
        ttk.Combobox(controls, textvariable=self.speaker_var, values=SPEAKERS, width=10,
                     state="readonly").pack(side="left")
        ttk.Label(controls, text="Langue").pack(side="left", padx=(12, 4))
        self.lang_var = tk.StringVar(value=self.progress.get("_voice", {}).get("language", LANGUAGES[0]))
        ttk.Combobox(controls, textvariable=self.lang_var, values=LANGUAGES, width=10,
                     state="readonly").pack(side="left")
        ttk.Label(controls, text="Style").pack(side="left", padx=(12, 4))
        self.instruct_var = tk.StringVar(value=self.progress.get("_voice", {}).get(
            "instruct", STYLES[0]))
        ttk.Combobox(controls, textvariable=self.instruct_var, values=STYLES).pack(
            side="left", fill="x", expand=True)
        for var in (self.speaker_var, self.lang_var, self.instruct_var):
            var.trace_add("write", lambda *_: self._voice_changed())
        self._apply_voice()

        self.status_var = tk.StringVar(value="Démarrage…")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(10, 0, 10, 6),
                  foreground="#666").pack(fill="x")

        self.root.bind("<space>", lambda e: None if isinstance(e.widget, (tk.Entry, ttk.Entry)) else self.toggle_play())
        self.root.bind("<Left>", lambda e: self.prev_chunk())
        self.root.bind("<Right>", lambda e: self.next_chunk())

    # -- modèle
    def _load_model_safe(self):
        try:
            self.narrator.load_model()
        except Exception as exc:  # noqa: BLE001
            self.ui_q.put(("error", f"Impossible de charger le modèle :\n{exc}"))

    # -- livre
    def choose_file(self):
        path = filedialog.askopenfilename(
            title="Choisir un livre",
            filetypes=[("Livres", "*.epub *.txt *.pdf"), ("Tous les fichiers", "*.*")])
        if path:
            self.open_book(path)

    def open_book(self, path):
        self.stop()
        self._save_progress()
        try:
            chapters = load_book(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("VoixLivre", f"Lecture du fichier impossible :\n{exc}")
            return
        self.book_path = str(Path(path).resolve())
        self.chapters = chapters
        self.narrator.load([split_chunks(text) for _, text in chapters])
        self.title_var.set(Path(path).stem)
        self.chap_list.delete(0, "end")
        for title, _ in chapters:
            self.chap_list.insert("end", title)

        saved = self.progress.get(self.book_path, [0, 0])
        ch = min(saved[0], len(chapters) - 1)
        idx = min(saved[1], max(len(self.narrator.chunks[ch]) - 1, 0))
        self.current = (ch, idx)
        self._show_chapter(ch)
        self._highlight(ch, idx)
        self._update_buttons()
        if saved != [0, 0]:
            self.status_var.set(f"Reprise au chapitre « {chapters[ch][0]} ».")

    def _show_selected_chapter(self):
        sel = self.chap_list.curselection()
        if sel and not self.playing:
            self._show_chapter(sel[0])

    def _show_chapter(self, ch):
        self.displayed_chapter = ch
        self.chap_list.selection_clear(0, "end")
        self.chap_list.selection_set(ch)
        self.chap_list.see(ch)
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for i, (chunk, end_para) in enumerate(self.narrator.chunks[ch]):
            tag = f"c{i}"
            t.insert("end", chunk + ("\n\n" if end_para else " "), (tag,))
            t.tag_bind(tag, "<Double-Button-1>", lambda e, c=ch, j=i: self.jump(c, j))
        t.configure(state="disabled")
        t.yview_moveto(0)

    def _highlight(self, ch, idx):
        if self.displayed_chapter != ch:
            self._show_chapter(ch)
        self.text.tag_remove("current", "1.0", "end")
        ranges = self.text.tag_ranges(f"c{idx}")
        if ranges:
            self.text.tag_add("current", ranges[0], ranges[1])
            self.text.see(ranges[0])

    # -- lecture
    def _ready(self):
        return self.narrator.model is not None and bool(self.chapters)

    def toggle_play(self):
        if not self._ready():
            return
        if not self.playing:
            self.jump(*self.current)
        else:
            self.paused = not self.paused
            self.narrator.set_paused(self.paused)
            self.btn_play.configure(text="▶" if self.paused else "⏸")
            self.status_var.set("En pause" if self.paused else "Lecture")

    def jump(self, ch, idx):
        if not self._ready():
            return
        self.current = (ch, idx)
        self._highlight(ch, idx)
        self.playing, self.paused = True, False
        self.btn_play.configure(text="⏸")
        self.narrator.play(ch, idx)

    def play_chapter(self):
        sel = self.chap_list.curselection()
        if sel:
            self.jump(sel[0], 0)

    def stop(self):
        self.narrator.stop()
        self.playing = self.paused = False
        self.btn_play.configure(text="▶")

    def next_chunk(self):
        if self.chapters:
            ch, idx = self.narrator._next(*self.current)
            if ch < len(self.chapters):
                self._move(ch, idx)

    def prev_chunk(self):
        if not self.chapters:
            return
        ch, idx = self.current
        if idx > 0:
            idx -= 1
        elif ch > 0:
            ch -= 1
            idx = max(len(self.narrator.chunks[ch]) - 1, 0)
        self._move(ch, idx)

    def _move(self, ch, idx):
        if self.playing:
            self.jump(ch, idx)
        else:
            self.current = (ch, idx)
            self._highlight(ch, idx)

    def _apply_voice(self):
        self._voice_job = None
        self.narrator.set_voice({"speaker": self.speaker_var.get(), "language": self.lang_var.get(),
                                 "instruct": self.instruct_var.get()})

    def _voice_changed(self):
        # petit délai pour ne pas relancer la génération à chaque lettre tapée dans « Style »
        if getattr(self, "_voice_job", None):
            self.root.after_cancel(self._voice_job)
        self._voice_job = self.root.after(700, self._apply_voice)

    def _update_buttons(self):
        state = ["!disabled"] if self._ready() else ["disabled"]
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            b.state(state)

    # -- évènements des threads
    def _poll(self):
        try:
            while True:
                kind, data = self.ui_q.get_nowait()
                if kind == "status":
                    self.status_var.set(data)
                elif kind == "model_ready":
                    self.status_var.set("Modèle prêt" + ("" if data else " (CPU : la génération sera lente)")
                                        + (" — appuyez sur ▶" if self.chapters else " — ouvrez un livre"))
                    self._update_buttons()
                elif kind == "playing":
                    self.current = data
                    self._highlight(*data)
                    ch = data[0]
                    self.status_var.set(f"Lecture — {self.chapters[ch][0]}  "
                                        f"({data[1] + 1}/{len(self.narrator.chunks[ch])})")
                    self._save_progress()
                elif kind == "buffering":
                    if self.playing and not self.paused:
                        self.status_var.set("Génération de la suite…")
                elif kind == "finished":
                    self.stop()
                    self.status_var.set("Fin du livre.")
                elif kind == "error":
                    self.stop()
                    self.status_var.set("Erreur")
                    messagebox.showerror("VoixLivre", data)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    # -- progression
    def _load_progress(self):
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_progress(self):
        if self.book_path:
            self.progress[self.book_path] = list(self.current)
        self.progress["_voice"] = dict(self.narrator.voice)
        try:
            PROGRESS_FILE.write_text(json.dumps(self.progress, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def on_close(self):
        if getattr(self, "_voice_job", None):
            self._apply_voice()
        self.narrator.stop()
        self._save_progress()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == "__main__":
    main()
