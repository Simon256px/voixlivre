# VoixLivre

Écouter un ebook (EPUB, TXT, PDF) lu par une voix de synthèse, dans une interface simple et sobre.
La voix est générée localement sur votre carte graphique avec le modèle
[Qwen3-TTS-12Hz-1.7B-CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice).

## Fonctionnalités

- Ouverture de livres **EPUB**, **TXT** et **PDF**, avec la liste des chapitres
- Texte affiché, passage en cours **surligné**, défilement automatique
- Double-clic sur un chapitre ou une phrase pour y lancer la lecture
- ⏮ ▶/⏸ ⏭ et raccourcis clavier : `Espace` (pause/lecture), `←` `→` (passage précédent/suivant)
- Choix de la **voix** (9 voix), de la **langue** et du **style** de lecture (liste de styles prêts à l'emploi, modifiable)
- **Reprise automatique** là où vous vous êtes arrêté dans chaque livre
- Génération par lots et mise en cache : lecture continue sans blanc, sauts instantanés

## Prérequis

- Windows, Linux ou macOS avec Python 3.10+
- Une carte graphique **NVIDIA** (≈ 6 Go de mémoire vidéo) est fortement recommandée : sur CPU, la génération est trop lente pour une écoute en continu
- ~5 Go d'espace disque pour le modèle (téléchargé automatiquement au premier lancement)

## Installation

```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
```

## Utilisation

```bash
python voixlivre.py              # puis « Ouvrir un livre… »
python voixlivre.py monlivre.epub
```

Sous Windows, double-cliquez simplement sur `VoixLivre.bat`.

Un petit livre de test, `exemple.epub`, est fourni.

## Remarques

- Aucune des voix du modèle n'est francophone native : choisir un style contenant « sans accent »
  réduit nettement l'accent, et certaines voix s'en sortent mieux que d'autres, à tester à l'oreille.
- Le message « SoX could not be found » affiché au démarrage est sans conséquence.
- La progression et les réglages sont enregistrés dans `~/.voixlivre.json`.
