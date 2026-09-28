"""VoixLivre : écouter un ebook (EPUB, TXT, PDF) avec Qwen3-TTS.

Lancement :  python voixlivre.py [livre.epub]
"""
import json
import queue
import re
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import sounddevice as sd

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
CLONE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"         # pour les voix clonées du dossier voix/
VOICES_DIR = Path(__file__).resolve().parent / "voix"
MAX_REF_SECONDS = 20                                       # extrait de référence d'une voix clonée
# Voix intégrées au modèle proposées dans la liste : nom affiché -> nom dans le modèle (qui connaît
# aussi Serena, Vivian, Ryan, Aiden, Eric, Dylan, Uncle_Fu et Ono_Anna). Les voix clonées de voix/
# s'y ajoutent.
BUILTIN_VOICES = {"Manon": "Sohee"}
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
    text = re.sub(r"[ \t\xa0\u2009\u202f\u200b\ufeff]+", " ", text.replace("\xad", ""))
    return re.sub(r"\s*\n\s*", "\n", text).strip()


_CLOSING_START = re.compile(r"^[,.;:!?)\]»…’%]")                # suite d'une phrase coupée
_LOWER_START = re.compile(r"^[a-zà-ÿœæ]")
_SENTENCE_END = re.compile(r"[.!?…]\W*$")
_OPEN_END = re.compile(r"[’'(\[«,-]$|\b(?:de|du|des|la|le|les|l|d|un|une|et|à|au|aux|en|par|pour|sur|dans|"
                       r"que|qu’ici|qui|dont|où|comme)$", re.I)                     # phrase inachevée
_JUNK_LINE = re.compile(r"^(?:\d{1,4}\W*|[\W_]+)$")             # n° de page, « * * * », ponctuation seule


def tidy_text(text):
    """Remet le texte en paragraphes propres et complets pour une lecture fluide.

    Recolle les lignes coupées au milieu d'une phrase, supprime les numéros de page
    et séparateurs isolés, normalise espaces et ponctuation.
    """
    paragraphs = []
    for line in _clean(text).split("\n"):
        line = re.sub(r"^[•·▪◦■□►➢✓*]\s*", "", line.strip())      # puces de liste
        if not line or _JUNK_LINE.match(line):
            continue
        if paragraphs and (_CLOSING_START.match(line) or _OPEN_END.search(paragraphs[-1])
                           or (_LOWER_START.match(line) and not _SENTENCE_END.search(paragraphs[-1]))):
            sep = "" if re.match(r"^[,.;:!?)\]»…%]", line) or paragraphs[-1].endswith(("’", "'", "(", "[")) else " "
            paragraphs[-1] += sep + line
        else:
            paragraphs.append(line)
    out = []
    for p in paragraphs:
        p = re.sub(r"\s*(?:\[\s*(?:…|\.\.\.)\s*\]|\(\s*(?:…|\.\.\.)\s*\))\s*", " ", p)  # coupures […]
        p = re.sub(r"\s+([,.)\]])", r"\1", p)
        p = re.sub(r"([(\[])\s+", r"\1", p)
        p = re.sub(r"«\s*", "« ", p)
        p = re.sub(r"\s*»", " »", p)
        p = re.sub(r"\s{2,}", " ", p).strip()
        if p:
            out.append(p)
    return "\n".join(out)


_BLOCK_TAGS = ["p", "div", "section", "article", "header", "footer", "blockquote", "li", "ul", "ol",
               "dd", "dt", "dl", "pre", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "hr"]
_NOTE_CLASS = re.compile(r"^(?:defnotes?|notes?|footnotes?|endnotes?|rearnotes?|ntb|nt|apnb|noteref|"
                         r"note[-_]?\w*|\w*[-_]notes?)$", re.I)
_NOTE_TYPE = re.compile(r"note|annotation", re.I)
_NOTE_MARK = re.compile(r"^\W*\d{1,3}\W*$|^\W*[*†‡§]+\W*$|^\W*[ivxlc]{1,6}\W*$", re.I)
_BOILERPLATE = re.compile(r"ISBN|©|copyright|tous droits|achevé de numériser|édition électronique|"
                          r"tenu informé des parutions", re.I)


def _epub_toc_titles(book):
    """Associe chaque fichier du livre au titre que lui donne la table des matières."""
    titles = {}

    def walk(entries):
        for entry in entries:
            if isinstance(entry, tuple):
                section, children = entry
                if getattr(section, "href", None):
                    titles.setdefault(section.href.split("#")[0], section.title)
                walk(children)
            elif getattr(entry, "href", None):
                titles.setdefault(entry.href.split("#")[0], entry.title)

    walk(book.toc or [])
    return titles


def _epub_html_to_text(html):
    from bs4 import BeautifulSoup, NavigableString, Comment

    soup = BeautifulSoup(html, "html.parser")
    root = soup.body or soup
    # en HTML, les retours à la ligne du code source ne sont que des espaces
    for node in list(root.find_all(string=True)):
        if isinstance(node, Comment):
            node.extract()
        elif isinstance(node, NavigableString) and not node.find_parent("pre"):
            node.replace_with(re.sub(r"\s+", " ", str(node)))
    for tag in root(["script", "style", "aside", "img", "svg", "figure", "nav", "rt", "rp"]):
        tag.decompose()
    # notes de bas de page et appels de note : on ne les lit pas
    for tag in list(root.find_all(True)):
        if tag.decomposed:
            continue
        kind = " ".join(str(tag.get(a, "")) for a in ("epub:type", "role"))
        classes = tag.get("class") or []
        if _NOTE_TYPE.search(kind) or any(_NOTE_CLASS.match(c) for c in classes):
            tag.decompose()
        elif tag.name in ("sup", "a") and _NOTE_MARK.match(tag.get_text()):
            tag.decompose()
    # une pause d'intonation après chaque titre
    for head in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if not re.search(r"[.!?…:]\W*$", head.get_text().strip()):
            head.append(".")
    for br in root.find_all("br"):
        br.replace_with("\n")
    # les balises de bloc séparent les paragraphes, les balises en ligne (<i>, <span>…) non
    for tag in root.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.append("\n")
    return tidy_text(root.get_text(""))


def load_epub(path):
    from ebooklib import epub, ITEM_DOCUMENT

    book = epub.read_epub(str(path), options={"ignore_ncx": False})
    toc = _epub_toc_titles(book)
    chapters = []
    for idref, _ in book.spine:
        item = book.get_item_with_id(idref)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue
        text = _epub_html_to_text(item.get_content())
        if len(text) < 40 or (len(text) < 600 and _BOILERPLATE.search(text)):
            continue
        name = item.get_name()
        title = toc.get(name) or next((t for h, t in toc.items() if name.endswith(h) or h.endswith(name)), None)
        title = _clean(title or text.split("\n", 1)[0]).rstrip(".")
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
    text = text.replace("\r\n", "\n")
    # les lignes vides séparent les paragraphes ; les retours à la ligne simples sont recollés
    if re.search(r"\n\s*\n", text):
        text = re.sub(r"(?<!\n)\n(?!\s*\n)", " ", text)
    parts = re.split(r"\n(?=\s*(?:chapitre|chapter|partie|livre)\b[^\n]{0,60}\n)", text, flags=re.I)
    chapters = []
    for p in parts:
        p = tidy_text(p)
        if p:
            chapters.append((p.split("\n", 1)[0][:70], p))
    return chapters


def load_pdf(path, pages_per_section=10):
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(pg.extract_text() or "") for pg in reader.pages]
    chapters = []
    for start in range(0, len(pages), pages_per_section):
        # recolle les mots coupés et les lignes coupées à l'intérieur des paragraphes
        text = "\n".join(pages[start:start + pages_per_section])
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
        text = re.sub(r"(?<![.!?:»…])\n(?!\n)", " ", text)
        text = tidy_text(text)
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


_ABBREV_END = re.compile(r"(?:^|[\s(’'])(?:[A-ZÀ-Ý]|\d{1,4}|[IVXLC]{1,5}|M|MM|Mme|Mlle|Mgr|Dr|Pr|St|Ste|"
                         r"p|pp|cf|Cf|éd|vol|chap|coll|trad|n°|av|apr|env|op|cit|ibid|id|fig|art|al)\.$")

_CONTINUATION = re.compile(r"^(?:[,;:)\]»]|[–—]\s*[,;:)]|[a-zà-ÿœæ])")   # « ? –, parle… » n'est pas une phrase

# Où couper une phrase trop longue, du meilleur au moins bon endroit :
# (motif, position de coupe dans le motif) — la ponctuation reste à la fin du premier morceau,
# un tiret d'incise ou « et / mais / car… » passent au début du suivant.
_CUTS = [
    (re.compile(r"[;:](?=\s)"), 1),
    (re.compile(r"\s[–—]\s"), 0),
    (re.compile(r",(?=\s)"), 1),
    (re.compile(r"\s(?:et|mais|car|donc|or|puis|ou|parce que|lorsque|tandis que|alors que)\s"), 0),
    (re.compile(r"\s"), 0),
]


def _best_cut(sentence):
    """Position où couper une phrase trop longue pour garder une intonation naturelle."""
    lo, hi = MAX_CHARS // 2, MAX_CHARS
    for pattern, offset in _CUTS:
        spots = [m.start() + offset for m in pattern.finditer(sentence, 0, hi) if m.start() >= lo]
        if spots:
            return spots[-1]
    return hi


# ---------------------------------------------------------------- bibliothèque

COVERS_DIR = Path.home() / ".voixlivre" / "couvertures"
COVER_SIZE = (132, 198)                         # vignette affichée dans la bibliothèque


def _clean_stem(path):
    """Nom de fichier sans les mentions entre parenthèses ou crochets (auteur, site…)."""
    stem = Path(path).stem
    return re.sub(r"\s*[(\[][^)\]]*[)\]]", "", stem).strip() or stem


def _epub_cover_bytes(book):
    """Image de couverture d'un EPUB, en essayant les différentes façons de la déclarer."""
    import ebooklib

    images = list(book.get_items_of_type(ebooklib.ITEM_IMAGE)) + list(book.get_items_of_type(ebooklib.ITEM_COVER))
    for meta in book.get_metadata("OPF", "cover"):                      # EPUB 2 : <meta name="cover">
        item = book.get_item_with_id((meta[1] or {}).get("content", ""))
        if item is not None:
            return item.get_content()
    for item in images:                                                 # EPUB 3 : properties="cover-image"
        if "cover-image" in (getattr(item, "properties", None) or []):
            return item.get_content()
    for item in images:                                                 # nom de fichier évocateur
        if "cover" in item.get_name().lower() or "couv" in item.get_name().lower():
            return item.get_content()
    for idref, _ in book.spine[:2]:                                     # image de la première page
        doc = book.get_item_with_id(idref)
        if doc is None:
            continue
        match = re.search(rb"""<(?:img|image)[^>]+(?:src|href)=["']([^"']+)""", doc.get_content())
        if match:
            name = match.group(1).decode("utf-8", "ignore").split("/")[-1]
            for item in images:
                if item.get_name().split("/")[-1] == name:
                    return item.get_content()
    return None


def book_info(path):
    """Titre, auteur et image de couverture (octets ou None) d'un livre."""
    ext = Path(path).suffix.lower()
    title, author, cover = _clean_stem(path), "", None
    try:
        if ext == ".epub":
            from ebooklib import epub

            book = epub.read_epub(str(path), options={"ignore_ncx": True})
            title = (book.get_metadata("DC", "title") or [(title,)])[0][0] or title
            author = ", ".join(a[0] for a in book.get_metadata("DC", "creator") if a and a[0])
            cover = _epub_cover_bytes(book)
        elif ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            meta = reader.metadata or {}
            title = str(meta.get("/Title") or "").strip() or title
            author = str(meta.get("/Author") or "").strip()
            images = reader.pages[0].images if reader.pages else []
            cover = images[0].data if len(images) else None
    except Exception:  # noqa: BLE001 — un livre abîmé garde son nom de fichier et une couverture générée
        pass
    title = re.sub(r"^(?:Microsoft Word|Microsoft PowerPoint)\s*-\s*", "", re.sub(r"\s+", " ", str(title)).strip())
    # métadonnées fantaisistes (URL, HTML, nom de fichier technique…) : on garde le nom du fichier
    if not title or len(title) > 150 or re.search(r"[<>{}]|^[a-z]+:|\.(?:docx?|pdf|epub|indd)$", title, re.I):
        title = _clean_stem(path)
    author = re.sub(r"\s+", " ", author).strip()
    if len(author) > 100 or re.search(r"[<>{}]|^[a-z]+:", author, re.I):
        author = ""
    return {"title": title, "author": author, "cover": cover}


def save_cover(path, data):
    """Enregistre la vignette de couverture dans le cache ; renvoie le nom du fichier ou ""."""
    if not data:
        return ""
    import hashlib
    import io

    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        # recadrage au format de la vignette, sans déformer
        ratio = COVER_SIZE[0] / COVER_SIZE[1]
        w, h = img.size
        if w / h > ratio:
            nw = int(h * ratio)
            img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = int(w / ratio)
            img = img.crop((0, 0, w, nh))
        img = img.resize(COVER_SIZE, Image.LANCZOS)
        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16] + ".png"
        img.save(COVERS_DIR / name)
        return name
    except Exception:  # noqa: BLE001 — image illisible : couverture générée à la place
        return ""


def split_chunks(text):
    """Découpe un chapitre en segments [(texte, fin_de_paragraphe)]."""
    chunks = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        # découpe en phrases, puis recolle les fausses fins de phrase (« M. », « p. 12 », « 1. »…)
        sentences = []
        for s in re.split(r"(?<=[.!?…])\s+", para):
            if sentences and (_ABBREV_END.search(sentences[-1]) or len(sentences[-1]) < 12
                              or _CONTINUATION.match(s)):
                sentences[-1] += " " + s
            else:
                sentences.append(s)
        pieces = []
        for sentence in sentences:
            while len(sentence) > MAX_CHARS + 60:          # marge : pas de petit bout isolé en fin
                cut = _best_cut(sentence)
                pieces.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                pieces.append(sentence)
        current = ""
        for piece in pieces:
            # un bout trop court lu seul aurait une intonation fausse : on le garde avec la suite
            if current and len(current) >= 25 and len(current) + len(piece) + 1 > MAX_CHARS:
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


def load_cloned_voices():
    """Voix clonées décrites dans voix/voix.json : {nom: {"fichier", "texte", "source"}}."""
    try:
        entries = json.loads((VOICES_DIR / "voix.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {e["nom"]: e for e in entries if (VOICES_DIR / e["fichier"]).exists()}


def add_cloned_voice(name, audio_path, text):
    """Copie un extrait audio dans voix/ (mono, 20 s max) et l'ajoute à voix.json."""
    import librosa
    import soundfile as sf

    wav, sr = librosa.load(str(audio_path), sr=None, mono=True, duration=MAX_REF_SECONDS)
    VOICES_DIR.mkdir(exist_ok=True)
    stem = re.sub(r"[^\w-]+", "_", name.lower()).strip("_") or "voix"
    target = VOICES_DIR / f"{stem}.flac"
    n = 2
    while target.exists():
        target, n = VOICES_DIR / f"{stem}_{n}.flac", n + 1
    sf.write(str(target), wav, sr)
    try:
        entries = json.loads((VOICES_DIR / "voix.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entries = []
    entries.append({"nom": name, "fichier": target.name, "texte": text.strip(),
                    "source": f"Ajoutée depuis {Path(audio_path).name}"})
    (VOICES_DIR / "voix.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")


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
        self.clone_model = None               # chargé à la première voix clonée utilisée
        self.clones = load_cloned_voices()
        self.clone_prompts = {}               # nom -> empreinte de la voix, calculée une fois
        self.clone_lock = threading.Lock()    # préchargement et génération peuvent se croiser
        self.cond = threading.Condition()
        self.chunks = []                      # chunks[chapitre] -> [(texte, fin_de_paragraphe)]
        self.offsets = []                     # index global du 1er segment de chaque chapitre
        self.voice = {"speaker": next(iter(BUILTIN_VOICES)), "language": LANGUAGES[0], "instruct": ""}
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
        model.generate_custom_voice(text="Bonjour.", language="French",               # préchauffage
                                    speaker=next(iter(BUILTIN_VOICES.values())))
        with self.cond:
            self.model = model
            self.cond.notify_all()
        self.post("model_ready", cuda)

    def _abort_hook(self, module, args):
        if self.running_epoch is not None and self.running_epoch != self.gen_epoch:
            raise _Aborted()

    def _clone_prompt(self, name):
        """Charge (une seule fois) le modèle de clonage et l'empreinte de la voix `name`."""
        with self.clone_lock:
            return self._clone_prompt_locked(name)

    def preload_clone(self, name):
        """Prépare en arrière-plan une voix clonée pour que la lecture démarre sans attendre."""
        try:
            self._clone_prompt(name)
            self.post("voice_ready", name)
        except Exception:  # noqa: BLE001 — la génération réessaiera et affichera l'erreur
            pass

    def _clone_prompt_locked(self, name):
        if self.clone_model is None:
            import torch
            from qwen_tts import Qwen3TTSModel

            self.post("status", "Chargement du modèle des voix clonées (une seule fois)…")
            cuda = torch.cuda.is_available()
            model = Qwen3TTSModel.from_pretrained(
                CLONE_MODEL_ID,
                device_map="cuda:0" if cuda else "cpu",
                dtype=torch.bfloat16 if cuda else torch.float32,
                attn_implementation="sdpa",
            )
            model.model.talker.register_forward_pre_hook(self._abort_hook)
            self.clone_model = model
        if name not in self.clone_prompts:
            import librosa

            entry = self.clones[name]
            wav, sr = librosa.load(str(VOICES_DIR / entry["fichier"]), sr=None, mono=True,
                                   duration=MAX_REF_SECONDS)
            text = entry.get("texte", "").strip()
            # sans le texte de l'extrait, on ne garde que le timbre (un peu moins fidèle)
            self.clone_prompts[name] = self.clone_model.create_voice_clone_prompt(
                ref_audio=(wav, sr), ref_text=text or None, x_vector_only_mode=not text)[0]
        return self.clone_prompts[name]

    def synthesize(self, texts, voice):
        """Génère plusieurs segments d'un coup (bien plus rapide qu'un par un)."""
        n = len(texts)
        # durée audio plafonnée d'après le texte le plus long (≥ 7 caractères/s, + 4 s) : un segment
        # qui divague est coupé au lieu de bloquer tout le lot (le modèle produit 12 trames/s)
        limit = {"max_new_tokens": int(12 * (max(map(len, texts)) / 7 + 4))}
        if voice["speaker"] in self.clones:
            prompt = self._clone_prompt(voice["speaker"])
            wavs, sr = self.clone_model.generate_voice_clone(
                text=texts, language=[voice["language"]] * n, voice_clone_prompt=[prompt] * n, **limit)
        else:
            speaker = BUILTIN_VOICES.get(voice["speaker"], voice["speaker"])
            kwargs = {"text": texts, "language": [voice["language"]] * n, "speaker": [speaker] * n}
            if voice["instruct"].strip():
                kwargs["instruct"] = [voice["instruct"].strip()] * n
            wavs, sr = self.model.generate_custom_voice(**kwargs, **limit)
        return [_trim(np.asarray(w, dtype=np.float32).reshape(-1), sr) for w in wavs], sr

    def _synthesize_checked(self, texts, voice):
        """Comme synthesize, mais régénère une fois les ratés (babillage ou audio quasi vide).

        Le modèle échantillonne au hasard : très rarement, il produit un audio bien trop long
        pour le texte (il divague) ou presque vide. On le détecte à la durée.
        """
        wavs, sr = self.synthesize(texts, voice)

        def suspicious(text, wav):
            seconds = len(wav) / sr
            return seconds > len(text) / 7 + 2 or seconds < len(text) / 40

        bad = [k for k, (t, w) in enumerate(zip(texts, wavs)) if suspicious(t, w)]
        if bad:
            retry, _ = self.synthesize([texts[k] for k in bad], voice)
            for k, wav in zip(bad, retry):
                expected = len(texts[k]) / 15 * sr              # ≈ 15 caractères par seconde
                if abs(len(wav) - expected) < abs(len(wavs[k]) - expected):
                    wavs[k] = wav
        return wavs, sr

    def reload_clones(self):
        with self.cond:
            self.clones = load_cloned_voices()
            self.clone_prompts.clear()

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
        # passage courant manquant (démarrage, saut) : petit lot pour entendre la voix au plus vite
        if missing and missing[0] == self.pos:
            return missing[:2]
        # sinon on attend d'avoir un lot complet, plus efficace
        if missing and (len(missing) >= BATCH or reached_end):
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
                wavs, sr = self._synthesize_checked(texts, voice)
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

# Identité visuelle de MontLivre (https://simon256px.github.io/MontLivre/)
YOLK, OCHRE, VIOLET = "#ffa51e", "#ff5500", "#7d00ff"      # accents, identiques de jour et de nuit
ON_ACCENT = "#010101"       # texte posé sur l'orange ou le jaune : toujours noir (valeur distincte de COAL)
# Couleurs de base selon le mode. COAL = encre et bordures, CLOUD = fond de la fenêtre,
# PAPER = page du livre, MARKER = surlignage personnel (violet MontLivre éclairci ou assombri).
THEMES = {
    "jour": {"COAL": "#000000", "ASH": "#a0a0a0", "CLOUD": "#e1e1e1", "PAPER": "#f2f0ea",
             "MUTED": "#5c5c5c", "INK": "#16130f", "MARKER": "#dcc4ff"},
    "nuit": {"COAL": "#e8e6e1", "ASH": "#5e5e5e", "CLOUD": "#141414", "PAPER": "#1d1c19",
             "MUTED": "#a3a3a3", "INK": "#e6e1d6", "MARKER": "#4a2d78"},
}
THEME = "jour"
COAL, ASH, CLOUD, PAPER, MUTED, INK, MARKER = (THEMES["jour"][k] for k in
                                               ("COAL", "ASH", "CLOUD", "PAPER", "MUTED", "INK", "MARKER"))


def set_theme(name):
    """Change les couleurs de base ; les widgets créés ensuite prennent automatiquement les nouvelles."""
    global THEME
    THEME = name if name in THEMES else "jour"
    globals().update(THEMES[THEME])
ASSETS = Path(__file__).resolve().parent / "assets"


def load_fonts():
    """Rend Archivo et Literata (dossier assets/fonts) disponibles pour cette application seulement."""
    if sys.platform != "win32":
        return
    import ctypes

    for font in (ASSETS / "fonts").glob("*.ttf"):
        ctypes.windll.gdi32.AddFontResourceExW(str(font), 0x10, 0)      # FR_PRIVATE


def _fonts(root):
    import tkinter.font as tkfont

    have = set(tkfont.families(root))
    pick = lambda *names: next((n for n in names if n in have), names[-1])
    sans, black, serif = pick("Archivo", "Segoe UI"), pick("Archivo Black", "Segoe UI Black"), \
        pick("Literata 12pt", "Georgia")
    return {
        "title": (black, 30), "big": (black, 17), "small": (sans, 8, "bold"), "button": (sans, 9, "bold"),
        "ui": (sans, 10), "text": (serif, 13), "symbol": ("Segoe UI Symbol", 13),
    }


def _count(text_widget, start, end, what):
    """Text.count renvoie selon les cas un entier, un tuple ou None : on ramène tout à un entier."""
    value = text_widget.count(start, end, "update", what)
    if isinstance(value, tuple):
        value = value[0]
    return value or 0


def _track(text):
    """Capitales espacées, à la manière des petits titres de MontLivre (Tk n'a pas de letter-spacing)."""
    return " ".join(text.upper()).replace("   ", "  ")


class FlatButton(tk.Frame):
    """Bouton plat façon MontLivre : bordure noire de 2 px, pas d'arrondi, survol inversé."""

    @staticmethod
    def colors(kind):
        """(fond, texte, fond survolé, texte survolé), lus au moment de peindre : suivent le mode jour/nuit."""
        return {"solid": (COAL, CLOUD, OCHRE, ON_ACCENT),
                "ghost": (CLOUD, COAL, COAL, CLOUD),
                "accent": (OCHRE, ON_ACCENT, COAL, OCHRE)}[kind]

    def __init__(self, parent, text, command, kind="solid", font=None, width=None, padx=16, pady=7):
        super().__init__(parent, bg=COAL, padx=2, pady=2)
        self.kind, self.command, self.disabled, self.hover = kind, command, False, False
        self.label = tk.Label(self, text=text, font=font, width=width, padx=padx, pady=pady, cursor="hand2")
        self.label.pack(fill="both", expand=True)
        for w in (self, self.label):
            w.bind("<Button-1>", lambda e: None if self.disabled else self.command())
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
        self._paint()

    def _set_hover(self, on):
        self.hover = on
        self._paint()

    def _paint(self):
        bg, fg, hbg, hfg = self.colors(self.kind)
        if self.disabled:
            self.configure(bg=ASH)
            self.label.configure(bg=CLOUD, fg=ASH, cursor="arrow")
        else:
            self.configure(bg=COAL)
            self.label.configure(bg=hbg if self.hover else bg, fg=hfg if self.hover else fg, cursor="hand2")

    # compatibilité avec l'API des boutons ttk utilisée par l'application
    def configure(self, cnf=None, **kw):
        if "text" in kw:
            return self.label.configure(text=kw.pop("text"))
        return super().configure(cnf, **kw)

    config = configure

    def cget(self, key):
        return self.label.cget(key) if key == "text" else super().cget(key)

    def state(self, spec):
        self.disabled = "disabled" in spec
        self._paint()

    def instate(self, spec):
        return self.disabled == ("disabled" in spec)


class App:
    def __init__(self, root, initial=None):
        self.root = root
        self.ui_q = queue.Queue()
        self.narrator = Narrator(lambda kind, data: self.ui_q.put((kind, data)))
        self.book_path = None
        self.book_title = "AUCUN LIVRE"
        self.chapters = []
        self.current = (0, 0)
        self.playing = False
        self.paused = False
        self.displayed_chapter = None
        self.bookmark = None              # (chapitre, segment) où l'on s'est arrêté
        self.highlights = {}              # "chapitre" -> [[début, fin, texte], …] en caractères
        self._scroll_job = None
        self.progress = self._load_progress()
        set_theme(self.progress.get("_theme", "jour"))      # mode jour/nuit mémorisé

        root.title("VoixLivre")
        root.geometry("1180x800")
        root.minsize(900, 620)
        self._build_ui()
        root.after(50, self._dark_titlebar)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll_job = root.after(100, self._poll)

        threading.Thread(target=self._load_model_safe, daemon=True).start()
        if initial:
            root.after(200, lambda: self.open_book(initial))
        else:
            root.after(200, self.show_library)          # au démarrage : la bibliothèque

    def _apply_ttk_styles(self):
        """Styles ttk (listes déroulantes, ascenseurs) aux couleurs du mode jour ou nuit."""
        root, f = self.root, self.fonts
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=PAPER, background=CLOUD, foreground=COAL,
                        arrowcolor=COAL, bordercolor=COAL, lightcolor=PAPER, darkcolor=PAPER,
                        padding=5, arrowsize=13)
        style.map("TCombobox",
                  fieldbackground=[("disabled", CLOUD), ("readonly", PAPER)],
                  foreground=[("disabled", ASH)], bordercolor=[("disabled", ASH)],
                  arrowcolor=[("disabled", ASH)], background=[("active", YOLK)],
                  selectbackground=[("readonly", PAPER), ("!readonly", YOLK)],
                  selectforeground=[("readonly", COAL), ("!readonly", ON_ACCENT)])
        style.configure("Vertical.TScrollbar", background=CLOUD, troughcolor=PAPER, bordercolor=PAPER,
                        lightcolor=CLOUD, darkcolor=CLOUD, arrowcolor=COAL, gripcount=0)
        style.map("Vertical.TScrollbar", background=[("active", YOLK)])
        root.option_add("*TCombobox*Listbox.background", PAPER)
        root.option_add("*TCombobox*Listbox.foreground", COAL)
        root.option_add("*TCombobox*Listbox.selectBackground", COAL)
        root.option_add("*TCombobox*Listbox.selectForeground", CLOUD)
        root.option_add("*TCombobox*Listbox.font", f["ui"])

    # -- construction
    def _build_ui(self):
        root = self.root
        f = self.fonts = _fonts(root)
        root.configure(bg=CLOUD)
        try:
            icon = tk.PhotoImage(file=str(ASSETS / "icon.png"))
            self._icons = (icon.subsample(16), icon.subsample(32))       # 64 px et 32 px
            root.iconphoto(True, self._icons[0])
        except tk.TclError:
            self._icons = None

        self._apply_ttk_styles()
        small = lambda parent, text, **kw: tk.Label(parent, text=_track(text), font=f["small"],
                                                     bg=kw.pop("bg", CLOUD), fg=kw.pop("fg", MUTED), **kw)

        # en-tête : icône, sur-titre, titre du livre en très grandes capitales, filet noir
        head = tk.Frame(root, bg=CLOUD, padx=28, pady=16)
        head.pack(fill="x")
        brand = tk.Frame(head, bg=CLOUD)
        brand.pack(fill="x")
        if self._icons:
            tk.Label(brand, image=self._icons[1], bg=CLOUD).pack(side="left", padx=(0, 10))
        small(brand, "VoixLivre — lecture à voix haute").pack(side="left")
        # bascule jour / nuit : l'icône montre le mode vers lequel on passe
        self.btn_theme = FlatButton(brand, "☾" if THEME == "jour" else "☀", self.toggle_theme, "ghost",
                                    f["symbol"], width=2, pady=2)
        self.btn_theme.pack(side="right", padx=(10, 0))
        FlatButton(brand, _track("Ajouter une voix"), self.add_voice, "ghost", f["button"]).pack(side="right")
        self.btn_library = FlatButton(brand, _track("Bibliothèque"), self.toggle_library, "ghost", f["button"])
        self.btn_library.pack(side="left", padx=(24, 0))
        FlatButton(brand, _track("Ouvrir un livre"), self.choose_file, "solid", f["button"]).pack(
            side="right", padx=(0, 10))
        self.title_var = tk.StringVar(value="AUCUN LIVRE")
        tk.Label(head, textvariable=self.title_var, font=f["title"], bg=CLOUD, fg=COAL, anchor="w").pack(
            fill="x", pady=(12, 8))
        tk.Frame(head, bg=COAL, height=2).pack(fill="x")

        # pied : bandeau orange de chiffres clés, commandes, état
        foot = tk.Frame(root, bg=CLOUD, padx=28)
        foot.pack(side="bottom", fill="x", pady=(0, 14))
        self.status_var = tk.StringVar(value="Démarrage…")
        tk.Label(foot, textvariable=self.status_var, font=f["ui"], bg=CLOUD, fg=MUTED, anchor="w").pack(
            side="bottom", fill="x", pady=(8, 0))

        band = tk.Frame(foot, bg=COAL, padx=2, pady=2)
        band.pack(side="bottom", fill="x", pady=(12, 0))
        self.stat_vars = {}
        for col, (key, label) in enumerate((("chapter", "Chapitre"), ("progress", "Progression"),
                                            ("voice", "Voix"))):
            cell = tk.Frame(band, bg=OCHRE, padx=16, pady=8)
            cell.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 2, 0))
            band.columnconfigure(col, weight=1, uniform="stats")
            small(cell, label, bg=OCHRE, fg=ON_ACCENT).pack(anchor="w")
            self.stat_vars[key] = tk.StringVar(value="—")
            tk.Label(cell, textvariable=self.stat_vars[key], font=f["big"], bg=OCHRE, fg=ON_ACCENT,
                     anchor="w").pack(anchor="w")

        controls = tk.Frame(foot, bg=CLOUD)
        controls.pack(side="bottom", fill="x", pady=(14, 0))
        self.btn_prev = FlatButton(controls, "⏮", self.prev_chunk, "ghost", f["symbol"], width=3, pady=3)
        self.btn_play = FlatButton(controls, "▶", self.toggle_play, "accent", f["symbol"], width=4, pady=3)
        self.btn_next = FlatButton(controls, "⏭", self.next_chunk, "ghost", f["symbol"], width=3, pady=3)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            b.pack(side="left", padx=(0, 6))
            b.state(["disabled"])

        def field(label, pad=18):
            small(controls, label).pack(side="left", padx=(pad, 6))

        field("Voix", 22)
        voices = self._voice_names()
        saved = self.progress.get("_voice", {}).get("speaker")
        self.speaker_var = tk.StringVar(value=saved if saved in voices else voices[0])
        self.speaker_box = ttk.Combobox(controls, textvariable=self.speaker_var, values=voices, width=12,
                                        state="readonly", height=25, font=f["ui"])
        self.speaker_box.pack(side="left")
        field("Langue")
        self.lang_var = tk.StringVar(value=self.progress.get("_voice", {}).get("language", LANGUAGES[0]))
        self.lang_box = ttk.Combobox(controls, textvariable=self.lang_var, values=LANGUAGES, width=10,
                                     state="readonly", font=f["ui"])
        self.lang_box.pack(side="left")
        field("Style")
        # les voix clonées ne suivent pas de consigne de style : on le signale à côté du champ
        self.style_note = tk.Label(controls, font=f["small"], bg=CLOUD, fg=OCHRE)
        self.style_note.pack(side="left", padx=(0, 6))
        self.instruct_var = tk.StringVar(value=self.progress.get("_voice", {}).get("instruct") or STYLES[0])
        self.style_box = ttk.Combobox(controls, textvariable=self.instruct_var, values=STYLES, font=f["ui"])
        self.style_box.pack(side="left", fill="x", expand=True)
        for var in (self.speaker_var, self.lang_var, self.instruct_var):
            var.trace_add("write", lambda *_: self._voice_changed())
        self._apply_voice()

        # corps : chapitres à gauche, page du livre à droite (carte papier à ombre portée)
        self._build_library(root)
        body = self.reader_view = tk.Frame(root, bg=CLOUD, padx=28, pady=6)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        small(body, "Chapitres").grid(row=0, column=0, sticky="w", pady=(0, 6))
        left = tk.Frame(body, bg=COAL, padx=2, pady=2)
        left.grid(row=1, column=0, sticky="ns", padx=(0, 22), pady=(0, 8))
        inner = tk.Frame(left, bg=CLOUD, padx=8, pady=8)
        inner.pack(fill="both", expand=True)
        self.chap_list = tk.Listbox(inner, activestyle="none", borderwidth=0, highlightthickness=0,
                                    font=f["ui"], width=32, bg=CLOUD, fg=COAL, selectbackground=COAL,
                                    selectforeground=CLOUD, exportselection=False)
        self.chap_list.pack(fill="both", expand=True)
        self.chap_list.bind("<Double-Button-1>", lambda e: self.play_chapter())
        self.chap_list.bind("<<ListboxSelect>>", lambda e: self._show_selected_chapter())

        small(body, "Lecture").grid(row=0, column=1, sticky="w", pady=(0, 6))
        tools = tk.Frame(body, bg=CLOUD)
        tools.grid(row=0, column=1, sticky="e", padx=(0, 8), pady=(0, 6))
        self.btn_mark = FlatButton(tools, _track("Surligner"), self.add_highlight, "ghost", f["small"],
                                   padx=10, pady=3)
        self.btn_mark.pack(side="left", padx=(0, 8))
        self.btn_mark.state(["disabled"])
        self.btn_bookmark = FlatButton(tools, _track("Aller au marque-page"), self.goto_bookmark, "ghost",
                                       f["small"], padx=10, pady=3)
        self.btn_bookmark.pack(side="left")
        self.btn_bookmark.state(["disabled"])
        holder = tk.Frame(body, bg=CLOUD)
        holder.grid(row=1, column=1, sticky="nsew")
        tk.Frame(holder, bg=COAL).place(x=8, y=8, relwidth=1, relheight=1, width=-8, height=-8)  # ombre
        card = tk.Frame(holder, bg=COAL, padx=2, pady=2)
        card.place(x=0, y=0, relwidth=1, relheight=1, width=-8, height=-8)
        page = tk.Frame(card, bg=PAPER)
        page.pack(fill="both", expand=True)
        bar = tk.Frame(page, bg=PAPER, padx=14, pady=8)
        bar.pack(fill="x")
        self.bar_title, self.bar_count = tk.StringVar(), tk.StringVar()
        tk.Label(bar, textvariable=self.bar_title, font=f["small"], bg=PAPER, fg=MUTED).pack(side="left")
        tk.Label(bar, textvariable=self.bar_count, font=f["small"], bg=PAPER, fg=MUTED).pack(side="right")
        tk.Frame(page, bg=COAL, height=1).pack(fill="x")
        self.text = tk.Text(page, wrap="word", font=f["text"], padx=40, pady=24, bg=PAPER, fg=INK,
                            borderwidth=0, highlightthickness=0, spacing1=2, spacing2=5, cursor="xterm",
                            selectbackground=COAL, selectforeground=PAPER, inactiveselectbackground=ASH)
        scroll = ttk.Scrollbar(page, command=self.text.yview)
        self.text.configure(yscrollcommand=lambda *a: (scroll.set(*a), self._place_ribbon()),
                            state="disabled")
        # fond violet clair + soulignement violet : reste visible même sous le jaune du passage lu
        self.text.tag_configure("marker", background=MARKER, underline=True, underlinefg=VIOLET)
        self.text.tag_configure("current", background=YOLK, foreground=ON_ACCENT)
        self.text.tag_raise("current", "marker")        # le passage lu reste visible sur un surlignage
        self.text.tag_raise("sel")
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.bind("<Configure>", lambda e: self._place_ribbon())
        # un texte non modifiable ne prend pas le focus seul : sans lui, la sélection resterait grise
        self.text.bind("<Button-1>", lambda e: self.text.focus_set(), add="+")
        self.text.bind("<ButtonRelease-1>", lambda e: self._selection_changed(), add="+")
        self.text.bind("<KeyRelease>", lambda e: self._selection_changed(), add="+")
        self.text.bind("<Button-3>", self._context_menu)

        # marque-page : ruban orange dans la marge gauche, face à l'endroit où l'on s'est arrêté
        self.ribbon = tk.Canvas(self.text, width=16, height=24, bg=PAPER, highlightthickness=0,
                                cursor="hand2")
        self.ribbon.create_polygon(2, 1, 14, 1, 14, 22, 8, 16, 2, 22, fill=OCHRE, outline=COAL, width=2)
        self.ribbon.bind("<Button-1>", lambda e: self.goto_bookmark())
        self.menu = tk.Menu(root, tearoff=0, bg=PAPER, fg=COAL, activebackground=COAL,
                            activeforeground=CLOUD, font=f["ui"], bd=1, relief="solid")

        root.bind("<space>", lambda e: None if isinstance(e.widget, (tk.Entry, ttk.Entry)) else self.toggle_play())
        root.bind("<Left>", lambda e: self.prev_chunk())
        root.bind("<Right>", lambda e: self.next_chunk())

    # -- bibliothèque : les livres que l'on lit, rouverts en un clic
    def _build_library(self, root):
        f = self.fonts
        self.library = self.progress.setdefault("_library", {})
        self._migrate_library()
        self._cover_images = []
        self._library_cols = 0
        view = self.library_view = tk.Frame(root, bg=CLOUD, padx=28, pady=6)
        top = tk.Frame(view, bg=CLOUD)
        top.pack(fill="x", pady=(0, 10))
        self.library_count = tk.StringVar()
        tk.Label(top, textvariable=self.library_count, font=f["small"], bg=CLOUD, fg=MUTED).pack(side="left")
        FlatButton(top, _track("Ajouter des livres"), self.add_books, "ghost", f["small"],
                   padx=10, pady=3).pack(side="right")

        frame = tk.Frame(view, bg=CLOUD)
        frame.pack(fill="both", expand=True)
        self.library_canvas = tk.Canvas(frame, bg=CLOUD, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(frame, command=self.library_canvas.yview)
        self.library_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.library_canvas.pack(side="left", fill="both", expand=True)
        self.library_grid = tk.Frame(self.library_canvas, bg=CLOUD)
        self.library_canvas.create_window(0, 0, window=self.library_grid, anchor="nw")
        self.library_grid.bind("<Configure>", lambda e: self.library_canvas.configure(
            scrollregion=self.library_canvas.bbox("all")))
        self.library_canvas.bind("<Configure>", lambda e: self._layout_library())
        # molette : seulement quand la souris survole la bibliothèque
        wheel = lambda e: self.library_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        self.library_canvas.bind("<Enter>", lambda e: root.bind_all("<MouseWheel>", wheel))
        self.library_canvas.bind("<Leave>", lambda e: root.unbind_all("<MouseWheel>"))
        self.library_menu = tk.Menu(root, tearoff=0, bg=PAPER, fg=COAL, activebackground=COAL,
                                    activeforeground=CLOUD, font=f["ui"], bd=1, relief="solid")
        self.library_mode = False

    def _migrate_library(self):
        """Les livres déjà lus avant l'arrivée de la bibliothèque y sont ajoutés."""
        for path, entry in list(self.progress.items()):
            if not path.startswith("_") and path not in self.library and Path(path).is_file():
                self._library_add(path, save=False)

    def _library_add(self, path, opened=False, save=True):
        path = str(Path(path).resolve())
        entry = self.library.get(path)
        if entry is None:
            info = book_info(path)
            entry = self.library[path] = {"title": info["title"], "author": info["author"],
                                          "cover": save_cover(path, info["cover"]),
                                          "added": time.time(), "opened": 0, "percent": 0}
        if opened:
            entry["opened"] = time.time()
        if save:
            self._save_progress()
        return entry

    def add_books(self):
        paths = filedialog.askopenfilenames(
            title="Ajouter des livres à la bibliothèque",
            filetypes=[("Livres", "*.epub *.txt *.pdf"), ("Tous les fichiers", "*.*")])
        added = 0
        for path in paths:
            if Path(path).suffix.lower() not in (".epub", ".txt", ".pdf"):
                continue
            self.status_var.set(f"Ajout de « {Path(path).name} »…")
            self.root.update_idletasks()
            known = str(Path(path).resolve()) in self.library
            self._library_add(path, save=False)
            added += not known
        self._save_progress()
        if paths:
            self.status_var.set("Ces livres sont déjà dans la bibliothèque." if not added else
                                f"{added} livre{'s' if added > 1 else ''} ajouté{'s' if added > 1 else ''} "
                                "à la bibliothèque.")
        self._refresh_library()

    def remove_from_library(self, path):
        entry = self.library.get(path)
        if not entry or not messagebox.askyesno(
                "VoixLivre", f"Retirer « {entry['title']} » de la bibliothèque ?\n\n"
                             "Le fichier du livre n'est pas supprimé, et la progression est conservée "
                             "si vous le rajoutez."):
            return
        if entry.get("cover"):
            try:
                (COVERS_DIR / entry["cover"]).unlink()
            except OSError:
                pass
        del self.library[path]
        self._save_progress()
        self._refresh_library()

    def toggle_library(self):
        if self.library_mode and self.chapters:
            self.show_reader()
        else:
            self.show_library()

    def show_library(self):
        self.library_mode = True
        self.reader_view.pack_forget()
        self.library_view.pack(fill="both", expand=True)
        self.title_var.set("BIBLIOTHÈQUE")
        self.btn_library.configure(text=_track("Retour à la lecture") if self.chapters else _track("Bibliothèque"))
        self._refresh_library()

    def show_reader(self):
        self.library_mode = False
        self.library_view.pack_forget()
        self.reader_view.pack(fill="both", expand=True)
        self.title_var.set(self.book_title)
        self.btn_library.configure(text=_track("Bibliothèque"))
        self.root.after_idle(lambda: self._highlight(*self.current, animate=False) if self.chapters else None)

    def _library_books(self):
        """Livres triés : derniers ouverts d'abord, puis derniers ajoutés."""
        return sorted(self.library.items(), key=lambda kv: (kv[1].get("opened", 0), kv[1].get("added", 0)),
                      reverse=True)

    def _refresh_library(self):
        self._library_cols = 0                    # force la reconstruction de la grille
        self._layout_library()

    def _layout_library(self):
        if not self.library_mode:
            return
        # la mesure des cartes peut redéclencher un redimensionnement : pas de mise en page imbriquée,
        # on la refait simplement juste après
        if getattr(self, "_laying_out", False):
            return
        self._laying_out = True
        try:
            self._layout_library_now()
        finally:
            self._laying_out = False

    def _layout_library_now(self):
        card_w = getattr(self, "_card_w", COVER_SIZE[0] + 28)   # affinée d'après les cartes réelles
        cols = max(1, (self.library_canvas.winfo_width() + 18) // (card_w + 18))
        if cols == self._library_cols:
            return
        self._library_cols = cols
        for child in self.library_grid.winfo_children():
            child.destroy()
        self._cover_images.clear()
        books = self._library_books()
        n = len(books)
        self.library_count.set(_track(f"{n} livre{'s' if n > 1 else ''}"))
        if not books:
            tk.Label(self.library_grid, text="Votre bibliothèque est vide.\n\nOuvrez un livre ou cliquez sur "
                     "« Ajouter des livres » : il apparaîtra ici avec sa couverture et votre progression.",
                     font=self.fonts["ui"], bg=CLOUD, fg=MUTED, justify="left").grid(row=0, column=0, sticky="w")
            return
        cards = []
        for k, (path, entry) in enumerate(books):
            card = self._library_card(path, entry)
            card.grid(row=k // cols, column=k % cols, padx=(0, 18), pady=(0, 18), sticky="nsew")
            cards.append(card)
        # largeur réelle des cartes mesurée un peu plus tard, une fois Tk les a dimensionnées
        # (jamais d'update_idletasks ici : il relancerait la mise en page en boucle)
        self._layout_job = self.root.after(80, self._check_card_width, cards, card_w)

    def _check_card_width(self, cards, card_w):
        self._layout_job = None
        alive = [c for c in cards if c.winfo_exists()]
        real = max((c.winfo_reqwidth() for c in alive), default=0)
        if real > card_w:                        # une carte plus large que prévu : on recompte les colonnes
            self._card_w = real
            self._library_cols = 0
            self._layout_library()

    def _library_card(self, path, entry):
        f = self.fonts
        missing = not Path(path).is_file()
        current = path == self.book_path
        card = tk.Frame(self.library_grid, bg=OCHRE if current else COAL, padx=2, pady=2, cursor="hand2")
        inner = tk.Frame(card, bg=PAPER, padx=12, pady=12)
        inner.pack(fill="both", expand=True)

        photo = None
        if entry.get("cover") and (COVERS_DIR / entry["cover"]).is_file():
            try:
                photo = tk.PhotoImage(file=str(COVERS_DIR / entry["cover"]))
            except tk.TclError:
                photo = None
        if photo:
            self._cover_images.append(photo)
            cover = tk.Label(inner, image=photo, bg=COAL, bd=0, highlightthickness=1, highlightbackground=COAL)
        else:                                     # couverture générée, façon affiche MontLivre
            cover = tk.Frame(inner, bg=OCHRE, width=COVER_SIZE[0], height=COVER_SIZE[1],
                             highlightthickness=1, highlightbackground=COAL)
            cover.pack_propagate(False)
            tk.Label(cover, text=entry["title"].upper()[:60], font=(f["big"][0], 11), bg=OCHRE, fg=ON_ACCENT,
                     wraplength=COVER_SIZE[0] - 16, justify="left", anchor="nw").pack(fill="both", padx=8, pady=8)
            tk.Label(cover, text=_track(Path(path).suffix.lstrip(".")), font=f["small"], bg=OCHRE,
                     fg=ON_ACCENT).pack(side="bottom", anchor="w", padx=8, pady=8)
        cover.pack()

        width = COVER_SIZE[0]
        if current or missing:
            tk.Label(inner, text=_track("Introuvable" if missing else "En lecture"), font=f["small"],
                     bg=PAPER, fg=OCHRE if not missing else MUTED).pack(anchor="w", pady=(8, 0))
        title = entry["title"] if len(entry["title"]) <= 70 else entry["title"][:68] + "…"
        tk.Label(inner, text=title, font=f["button"], bg=PAPER, fg=ASH if missing else COAL,
                 wraplength=width, justify="left", anchor="w").pack(anchor="w", pady=(8, 0))
        if entry.get("author"):
            tk.Label(inner, text=entry["author"][:50], font=f["ui"], bg=PAPER, fg=MUTED, wraplength=width,
                     justify="left", anchor="w").pack(anchor="w")
        pct = max(0, min(int(entry.get("percent", 0)), 100))
        bar = tk.Frame(inner, bg=CLOUD, width=width, height=6, highlightthickness=1, highlightbackground=COAL)
        bar.pack(anchor="w", pady=(10, 4))
        if pct:
            tk.Frame(bar, bg=OCHRE, height=4).place(x=0, y=0, relheight=1, relwidth=pct / 100)
        opened = entry.get("opened")
        when = time.strftime("Lu le %d/%m/%Y", time.localtime(opened)) if opened else "Pas encore ouvert"
        meta = tk.Frame(inner, bg=PAPER, width=width)
        meta.pack(fill="x")
        tk.Label(meta, text=f"{pct} %", font=f["button"], bg=PAPER, fg=COAL).pack(side="left")
        tk.Label(meta, text=when, font=(f["ui"][0], 8), bg=PAPER, fg=MUTED).pack(side="right")

        def bind_all(widget):
            widget.bind("<Button-1>", lambda e: self._open_from_library(path))
            widget.bind("<Button-3>", lambda e: self._library_context(e, path))
            widget.bind("<Enter>", lambda e: card.configure(bg=OCHRE))
            widget.bind("<Leave>", lambda e: card.configure(bg=OCHRE if current else COAL))
            for child in widget.winfo_children():
                bind_all(child)

        bind_all(card)
        return card

    def _open_from_library(self, path):
        if not Path(path).is_file():
            if messagebox.askyesno("VoixLivre", f"Le fichier est introuvable :\n{path}\n\n"
                                                "Le retirer de la bibliothèque ?"):
                entry = self.library.get(path)
                if entry and entry.get("cover"):
                    try:
                        (COVERS_DIR / entry["cover"]).unlink()
                    except OSError:
                        pass
                self.library.pop(path, None)
                self._save_progress()
                self._refresh_library()
            return
        if path == self.book_path:
            self.show_reader()
        elif self.open_book(path):
            self.show_reader()

    def _library_context(self, event, path):
        m = self.library_menu
        m.delete(0, "end")
        m.add_command(label="Ouvrir", command=lambda: self._open_from_library(path))
        m.add_command(label="Retirer de la bibliothèque", command=lambda: self.remove_from_library(path))
        m.tk_popup(event.x_root, event.y_root)

    def _book_percent(self):
        if not self.chapters:
            return 0
        ch, idx = self.current
        chunks = self.narrator.chunks
        done = sum(len(c) for c in chunks[:ch]) + idx
        return 100 * done // max(sum(len(c) for c in chunks), 1)

    # -- mode jour / nuit
    _COLOR_OPTIONS = ("background", "foreground", "highlightbackground", "highlightcolor",
                      "selectbackground", "selectforeground", "inactiveselectbackground",
                      "activebackground", "activeforeground", "disabledforeground", "insertbackground")

    def toggle_theme(self):
        old = THEMES[THEME]
        set_theme("nuit" if THEME == "jour" else "jour")
        mapping = {old[k].lower(): THEMES[THEME][k] for k in old}
        self._recolor(self.root, mapping)
        self._apply_ttk_styles()
        for box in (self.speaker_box, self.lang_box, self.style_box):   # listes déjà déroulées une fois
            try:
                listbox = f"{self.root.tk.call('ttk::combobox::PopdownWindow', box)}.f.l"
                self.root.tk.call(listbox, "configure", "-background", PAPER, "-foreground", COAL,
                                  "-selectbackground", COAL, "-selectforeground", CLOUD)
            except tk.TclError:
                pass
        self.text.tag_configure("marker", background=MARKER)
        self.btn_theme.configure(text="☾" if THEME == "jour" else "☀")
        self._repaint_buttons(self.root)
        self._mark_bookmark_chapter()
        self._library_cols = 0                    # cartes de la bibliothèque recréées aux bonnes couleurs
        self._layout_library()
        self._dark_titlebar()
        self.progress["_theme"] = THEME
        self._save_progress()
        self.status_var.set("Mode nuit." if THEME == "nuit" else "Mode jour.")

    def _recolor(self, widget, mapping):
        """Remplace, dans tout l'arbre de widgets, chaque couleur de l'ancien mode par celle du nouveau."""
        for option in self._COLOR_OPTIONS:
            try:
                value = str(widget.cget(option))
            except (tk.TclError, ValueError):
                continue
            if value.lower() in mapping:
                try:
                    widget.configure({option: mapping[value.lower()]})
                except tk.TclError:
                    pass
        if isinstance(widget, tk.Canvas):
            for item in widget.find_all():
                for option in ("fill", "outline"):
                    try:
                        value = str(widget.itemcget(item, option))
                    except tk.TclError:
                        continue
                    if value.lower() in mapping:
                        widget.itemconfigure(item, {option: mapping[value.lower()]})
        for child in widget.winfo_children():
            self._recolor(child, mapping)

    def _repaint_buttons(self, widget):
        if isinstance(widget, FlatButton):
            widget._paint()
        for child in widget.winfo_children():
            self._repaint_buttons(child)

    def _dark_titlebar(self):
        """Barre de titre Windows sombre en mode nuit (Windows 10 20H1 et plus récent)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1 if THEME == "nuit" else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
            # oblige Windows à redessiner le cadre tout de suite
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)    # NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED
        except Exception:  # noqa: BLE001 — simple confort visuel
            pass

    def _update_stats(self, ch, idx):
        """Bandeau orange et barre de la page : chapitre, progression, voix."""
        self.stat_vars["voice"].set(self.speaker_var.get())
        if not self.chapters:
            return
        chunks = self.narrator.chunks
        done = sum(len(c) for c in chunks[:ch]) + idx
        total = max(sum(len(c) for c in chunks), 1)
        self.stat_vars["chapter"].set(f"{ch + 1} / {len(self.chapters)}")
        self.stat_vars["progress"].set(f"{100 * done // total} %")
        self.bar_title.set(_track(self.chapters[ch][0][:48]))
        self.bar_count.set(f"{idx + 1} / {len(chunks[ch])}")

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
        if path and self.open_book(path):
            self.show_reader()

    def open_book(self, path):
        if self.book_path:
            self._set_bookmark()
        self.stop()
        self._save_progress()
        try:
            chapters = load_book(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("VoixLivre", f"Lecture du fichier impossible :\n{exc}")
            return False
        self.book_path = str(Path(path).resolve())
        self.chapters = chapters
        self.narrator.load([split_chunks(text) for _, text in chapters])
        entry = self._library_add(self.book_path, opened=True, save=False)
        title = entry["title"]
        self.book_title = title.upper()[:44] + ("…" if len(title) > 44 else "")
        if not self.library_mode:
            self.title_var.set(self.book_title)
        self.chap_list.delete(0, "end")
        for title, _ in chapters:
            self.chap_list.insert("end", title)

        entry = self.progress.get(self.book_path) or {}
        if isinstance(entry, list):                       # ancien format : juste la position
            entry = {"pos": entry}
        self.highlights = {k: v for k, v in entry.get("highlights", {}).items() if isinstance(v, list)}
        self.bookmark = self._valid_pos(entry.get("bookmark") or entry.get("pos"))
        ch, idx = self._valid_pos(entry.get("pos")) or (0, 0)
        self.current = (ch, idx)
        self.displayed_chapter = None
        self._highlight(ch, idx, animate=False)
        self._update_buttons()
        self._mark_bookmark_chapter()
        if (ch, idx) != (0, 0):
            self.status_var.set(f"Reprise au marque-page : « {chapters[ch][0]} ».")
        self._save_progress()
        return True

    def _valid_pos(self, pos):
        """(chapitre, segment) sauvegardé, ramené dans les limites du livre (il a pu changer)."""
        try:
            ch = min(max(int(pos[0]), 0), len(self.chapters) - 1)
            return ch, min(max(int(pos[1]), 0), max(len(self.narrator.chunks[ch]) - 1, 0))
        except (TypeError, ValueError, IndexError):
            return None

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
        self._apply_highlights(ch)
        self._selection_changed()
        self._place_ribbon()

    def _highlight(self, ch, idx, animate=True):
        if self.displayed_chapter != ch:
            self._show_chapter(ch)
            animate = False                    # nouveau chapitre : on se place directement
        self.text.tag_remove("current", "1.0", "end")
        ranges = self.text.tag_ranges(f"c{idx}")
        if ranges:
            self.text.tag_add("current", ranges[0], ranges[1])
            self._center(ranges[0], ranges[1], animate)
        self._update_stats(ch, idx)

    # -- défilement : le passage lu reste au milieu de la page
    def _center(self, start, end, animate=True):
        t = self.text
        total = _count(t, "1.0", "end", "ypixels")
        view = t.winfo_height()
        if total <= 0 or view <= 1:
            t.see(start)
            return
        top, bottom = _count(t, "1.0", start, "ypixels"), _count(t, "1.0", end, "ypixels")
        if bottom - top < view * 0.8:
            target = (top + bottom) / 2 - view / 2
        else:                                  # passage plus haut que la page : on montre son début
            target = top - view * 0.1
        target = min(max(target, 0), max(total - view, 0)) / total
        if self._scroll_job:
            self.root.after_cancel(self._scroll_job)
            self._scroll_job = None
        if not animate:
            t.yview_moveto(target)
            return
        origin, steps = t.yview()[0], 10

        def step(k=1):
            ease = 1 - (1 - k / steps) ** 3        # départ rapide, arrivée en douceur
            t.yview_moveto(origin + (target - origin) * ease)
            self._scroll_job = self.root.after(16, step, k + 1) if k < steps else None

        step()

    # -- surlignage personnel, mémorisé par livre et par chapitre
    def _offset(self, index):
        return _count(self.text, "1.0", index, "chars")

    def _apply_highlights(self, ch):
        """Pose les surlignages du chapitre ; les retrouve par leur texte si le découpage a changé."""
        t = self.text
        t.tag_remove("marker", "1.0", "end")
        kept = []
        for start, end, snippet in self.highlights.get(str(ch), []):
            a, b = f"1.0+{start}c", f"1.0+{end}c"
            if t.get(a, b) != snippet:
                found = t.search(snippet, "1.0", "end", exact=True) if snippet else ""
                if not found:
                    continue                   # texte introuvable : surlignage abandonné
                start = self._offset(found)
                end = start + len(snippet)
                a, b = f"1.0+{start}c", f"1.0+{end}c"
            t.tag_add("marker", a, b)
            kept.append([start, end, snippet])
        if kept or str(ch) in self.highlights:
            self.highlights[str(ch)] = kept

    def _selection_changed(self):
        has_sel = bool(self.text.tag_ranges("sel"))
        self.btn_mark.state(["!disabled"] if has_sel and self.chapters else ["disabled"])

    def add_highlight(self):
        sel = self.text.tag_ranges("sel")
        if not sel or self.displayed_chapter is None:
            return
        start, end = self._offset(sel[0]), self._offset(sel[1])
        # fusion avec les surlignages qui se touchent ou se chevauchent
        ranges = [(s, e) for s, e, _ in self.highlights.get(str(self.displayed_chapter), [])]
        merged = []
        for s, e in sorted(ranges + [(start, end)]):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        self.highlights[str(self.displayed_chapter)] = [
            [s, e, self.text.get(f"1.0+{s}c", f"1.0+{e}c")] for s, e in merged]
        self.text.tag_remove("sel", "1.0", "end")
        self._apply_highlights(self.displayed_chapter)
        self._selection_changed()
        self._save_progress()
        self.status_var.set("Passage surligné.")

    def remove_highlight(self, index):
        pos = self._offset(index)
        key = str(self.displayed_chapter)
        self.highlights[key] = [h for h in self.highlights.get(key, []) if not h[0] <= pos < h[1]]
        self._apply_highlights(self.displayed_chapter)
        self._save_progress()
        self.status_var.set("Surlignage retiré.")

    def _context_menu(self, event):
        if not self.chapters:
            return
        index = self.text.index(f"@{event.x},{event.y}")
        m = self.menu
        m.delete(0, "end")
        m.add_command(label="Surligner la sélection", command=self.add_highlight,
                      state="normal" if self.text.tag_ranges("sel") else "disabled")
        m.add_command(label="Retirer ce surlignage", command=lambda: self.remove_highlight(index),
                      state="normal" if "marker" in self.text.tag_names(index) else "disabled")
        m.add_separator()
        chunk = next((int(n[1:]) for n in self.text.tag_names(index) if re.fullmatch(r"c\d+", n)), None)
        m.add_command(label="Lire à partir d'ici", state="disabled" if chunk is None or not self._ready()
                      else "normal", command=lambda: self.jump(self.displayed_chapter, chunk))
        m.add_command(label="Placer le marque-page ici", state="disabled" if chunk is None else "normal",
                      command=lambda: self._set_bookmark((self.displayed_chapter, chunk)))
        m.tk_popup(event.x_root, event.y_root)

    # -- marque-page : là où l'on s'est arrêté (pause, fermeture, changement de livre)
    def _set_bookmark(self, pos=None):
        if not self.chapters:
            return
        self.bookmark = tuple(pos or self.current)
        self._place_ribbon()
        self._mark_bookmark_chapter()
        self._save_progress()

    def goto_bookmark(self):
        if not self.bookmark:
            return
        if self.playing:
            self.jump(*self.bookmark)
        else:
            self.current = self.bookmark
            self._highlight(*self.bookmark)

    def _place_ribbon(self):
        bm = self.bookmark
        info = None
        if bm and bm[0] == self.displayed_chapter:
            ranges = self.text.tag_ranges(f"c{bm[1]}")
            info = self.text.dlineinfo(ranges[0]) if ranges else None
        if info:
            # « outside » : coordonnées depuis le bord du widget, marges internes (padx/pady) comprises
            self.ribbon.place(x=12, y=info[1] + max((info[3] - 24) // 2, 0), bordermode="outside")
        else:
            self.ribbon.place_forget()

    def _mark_bookmark_chapter(self):
        """Le chapitre du marque-page apparaît en orange dans la liste."""
        self.btn_bookmark.state(["!disabled"] if self.bookmark else ["disabled"])
        for i in range(self.chap_list.size()):
            mine = bool(self.bookmark) and i == self.bookmark[0]
            self.chap_list.itemconfigure(i, foreground=OCHRE if mine else COAL)

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
            self.status_var.set("En pause — marque-page posé" if self.paused else "Lecture")
            if self.paused:
                self._set_bookmark()

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

    def _voice_names(self):
        # les voix clonées (lectrices françaises natives) d'abord, puis les voix intégrées au modèle
        return list(self.narrator.clones) + list(BUILTIN_VOICES)

    def _preload_voice(self):
        name = self.speaker_var.get()
        ready = self.narrator.model is not None
        if ready and name in self.narrator.clones and name not in self.narrator.clone_prompts:
            threading.Thread(target=self.narrator.preload_clone, args=(name,), daemon=True).start()

    def _apply_voice(self):
        self._voice_job = None
        cloned = self.speaker_var.get() in self.narrator.clones
        # le modèle de clonage reproduit le ton de l'extrait et ne suit pas de consigne de style
        self.style_box.state(["disabled"] if cloned else ["!disabled"])
        self.style_note.configure(text=_track(f"({' / '.join(BUILTIN_VOICES)} uniquement)") if cloned else "")
        self.narrator.set_voice({"speaker": self.speaker_var.get(), "language": self.lang_var.get(),
                                 "instruct": "" if cloned else self.instruct_var.get()})
        self.stat_vars["voice"].set(self.speaker_var.get())
        self._preload_voice()

    def add_voice(self):
        from tkinter import simpledialog

        path = filedialog.askopenfilename(
            title="Extrait audio de la voix (10 à 20 secondes, une seule personne, sans musique)",
            filetypes=[("Audio", "*.wav *.mp3 *.flac *.ogg *.m4a"), ("Tous les fichiers", "*.*")])
        if not path:
            return
        name = simpledialog.askstring("Ajouter une voix", "Nom de la voix :", parent=self.root)
        if not name or not name.strip():
            return
        text = simpledialog.askstring(
            "Ajouter une voix",
            "Texte exact prononcé dans l'extrait (recommandé pour une voix fidèle ;\n"
            "laisser vide si inconnu) :", parent=self.root) or ""
        try:
            add_cloned_voice(name.strip(), path, text)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("VoixLivre", f"Impossible d'ajouter cette voix :\n{exc}")
            return
        self.narrator.reload_clones()
        self.speaker_box.configure(values=self._voice_names())
        self.speaker_var.set(name.strip())
        self.status_var.set(f"Voix « {name.strip()} » ajoutée.")

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
                    self._preload_voice()
                elif kind == "playing":
                    self.current = data
                    self._highlight(*data)
                    ch = data[0]
                    self.status_var.set(f"Lecture — {self.chapters[ch][0]}  "
                                        f"({data[1] + 1}/{len(self.narrator.chunks[ch])})")
                    self._save_progress()
                elif kind == "voice_ready":
                    if not self.playing:
                        self.status_var.set(f"Voix « {data} » prête — appuyez sur ▶")
                elif kind == "buffering":
                    if self.playing and not self.paused and not self.status_var.get().startswith("Chargement"):
                        self.status_var.set("Génération de la suite…")
                elif kind == "finished":
                    self._set_bookmark()
                    self.stop()
                    self.status_var.set("Fin du livre.")
                elif kind == "error":
                    self.stop()
                    self.status_var.set("Erreur")
                    messagebox.showerror("VoixLivre", data)
        except queue.Empty:
            pass
        self._poll_job = self.root.after(100, self._poll)

    # -- progression
    def _load_progress(self):
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_progress(self):
        if self.book_path:
            self.progress[self.book_path] = {
                "pos": list(self.current),
                "bookmark": list(self.bookmark) if self.bookmark else None,
                "highlights": {k: v for k, v in self.highlights.items() if v},
            }
            if self.book_path in self.library:
                self.library[self.book_path]["percent"] = self._book_percent()
        # réglages tels qu'affichés (le style reste mémorisé même s'il est inactif pour une voix clonée)
        self.progress["_voice"] = {"speaker": self.speaker_var.get(), "language": self.lang_var.get(),
                                   "instruct": self.instruct_var.get()}
        try:
            PROGRESS_FILE.write_text(json.dumps(self.progress, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def on_close(self):
        for job in (self._poll_job, self._scroll_job, getattr(self, "_voice_job", None),
                    getattr(self, "_layout_job", None)):
            if job:
                self.root.after_cancel(job)
        if getattr(self, "_voice_job", None):
            self._apply_voice()
        self.narrator.stop()
        if self.book_path:
            self._set_bookmark()
        self._save_progress()
        self.root.destroy()


def _fix_windowless_stdio():
    """Lancé par VoixLivre.bat (pythonw), le programme n'a pas de console : sys.stdout/stderr valent
    None et les poignées standard de Windows sont invalides. Le paquet `sox`, importé par qwen_tts,
    lance alors « sox -h » et échoue (WinError 50), ce qui empêche le modèle de se charger.
    On redirige donc la sortie vers un journal, ~/.voixlivre.log, qui sert aussi en cas de problème.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    import os

    log = open(Path.home() / ".voixlivre.log", "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        kernel32.SetStdHandle.argtypes = (ctypes.c_ulong, ctypes.c_void_p)
        nul = open(os.devnull, "rb")
        sys._voixlivre_stdin = nul                                   # garde le fichier ouvert
        kernel32.SetStdHandle(ctypes.c_ulong(-10 & 0xFFFFFFFF), msvcrt.get_osfhandle(nul.fileno()))
        for std in (-11, -12):                                       # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            kernel32.SetStdHandle(ctypes.c_ulong(std & 0xFFFFFFFF), msvcrt.get_osfhandle(log.fileno()))


def main():
    _fix_windowless_stdio()
    load_fonts()
    root = tk.Tk()
    App(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == "__main__":
    main()
