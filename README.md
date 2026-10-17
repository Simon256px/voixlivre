# VoixLivre

> Écouter ses ebooks lus à voix haute, sur son propre ordinateur.

VoixLivre ouvre un livre **EPUB**, **TXT** ou **PDF** et le lit avec une voix de synthèse naturelle,
générée localement sur votre carte graphique par le modèle
[Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice). Aucun compte, aucun
abonnement, aucun envoi de vos livres sur Internet.

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Utilisation](#utilisation)
- [Voix](#voix)
- [Activité Discord](#activité-discord)
- [Bon à savoir](#bon-à-savoir)
- [Contribuer](#contribuer)
- [Soutenir le projet](#soutenir-le-projet)
- [Licence](#licence)

## Fonctionnalités

**Lecture**

- Le texte s'affiche pendant l'écoute, le passage en cours est surligné et reste au milieu de la page
- Liste des chapitres ; un double-clic sur un chapitre ou une phrase y lance la lecture
- Lecture continue sans blanc et sauts instantanés, grâce à une génération par lots mise en cache
- Images et notes de l'ebook affichées à leur place, jamais lues : aperçu de la note au survol de
  l'appel (épinglé au clic), liste des notes en fin de chapitre
- Dictionnaire qui suit la lecture : panneau à droite avec la définition courte des mots difficiles
  du passage lu (concepts, mots savants, personnes, œuvres, lieux), la définition longue au clic ;
  mots soulignés en vert dans le texte, définition au survol. Les dictionnaires sont rédigés à
  l'avance, un fichier par livre dans `dictionnaires/`

**Bibliothèque**

- Au démarrage : couverture, titre, auteur, progression et date de dernière lecture de chaque livre
- Marque-page posé automatiquement là où l'on s'arrête, reprise en un clic
- Surlignages personnels, enregistrés par livre

**Confort**

- Mode jour / nuit, mémorisé
- Raccourcis clavier :

  | Touche    | Action                      |
  | --------- | --------------------------- |
  | `Espace`  | lecture / pause             |
  | `←` / `→` | passage précédent / suivant |

## Prérequis

|                 |                                                                                                                                                       |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Système         | Windows, Linux ou macOS                                                                                                                               |
| Python          | 3.10 ou plus récent                                                                                                                                   |
| Carte graphique | NVIDIA fortement recommandée : environ 6 Go de mémoire vidéo, 12 Go avec les voix clonées. Sur processeur seul, la génération est trop lente pour une écoute continue. |
| Disque          | environ 10 Go pour les deux modèles, téléchargés automatiquement au premier usage                                                                     |

## Installation

```bash
git clone https://github.com/Simon256px/voixlivre.git
cd voixlivre
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
```

La première commande `pip` installe PyTorch pour CUDA 12.8 ; adaptez l'adresse à votre configuration
si besoin (voir [pytorch.org](https://pytorch.org/get-started/locally/)).

## Utilisation

```bash
python voixlivre.py                  # puis « Ouvrir un livre… »
python voixlivre.py monlivre.epub    # ouvre directement un livre
```

Sous Windows, un double-clic sur `VoixLivre.bat` lance l'application sans fenêtre de console.

Le petit livre `exemple.epub` est fourni pour un premier essai.

## Voix

Quatre voix sont proposées :

| Voix       | Origine              | Particularité                                     |
| ---------- | -------------------- | ------------------------------------------------- |
| **Léa**    | créée pour VoixLivre | française native, timbre velouté et un peu grave  |
| **Clara**  | créée pour VoixLivre | française native, timbre serein et chaleureux     |
| **Margot** | créée pour VoixLivre | française native, timbre clair et net             |
| **Manon**  | intégrée au modèle   | choix du style de lecture ; légèrement accentuée  |

Vous pouvez ajouter n'importe quelle voix à partir d'un extrait audio de 10 à 20 secondes et du texte
prononcé : la marche à suivre est dans [voix/LISEZMOI.md](voix/LISEZMOI.md).

## Activité Discord

VoixLivre peut afficher sur votre profil Discord ce que vous écoutez : titre, auteur, progression et
temps écoulé, avec un lien vers ce dépôt. Cliquez sur « Discord » puis cochez « Afficher sur mon
profil Discord ce que j'écoute ».

Les textes sont personnalisables, et le mode discret affiche seulement « Écoute un livre », sans
dévoiler lequel. La fonction est désactivée par défaut.

## Bon à savoir

- Manon (voix « Sohee » du modèle) n'est pas francophone native : un style contenant « sans accent »
  réduit l'accent.
- Les voix clonées reproduisent le ton de leur extrait : le champ « Style » est alors désactivé.
- La première utilisation d'une voix clonée charge un second modèle (environ 20 s).
- Le message « SoX could not be found » affiché au démarrage est sans conséquence.
- La progression et les réglages sont enregistrés dans `~/.voixlivre.json`.
- Tout reste sur votre ordinateur ; seule l'activité Discord, si vous l'activez, est partagée. Voir
  les [conditions d'utilisation](CONDITIONS.md) et la
  [politique de confidentialité](CONFIDENTIALITE.md).

## Contribuer

Signalements de bugs, idées et propositions de code sont les bienvenus : lisez
[CONTRIBUTING.md](CONTRIBUTING.md) puis ouvrez une
[issue](https://github.com/Simon256px/voixlivre/issues).

## Soutenir le projet

VoixLivre est gratuit et le restera. S'il vous est utile, vous pouvez me soutenir sur
[ko-fi.com/simon256px](https://ko-fi.com/simon256px).

## Licence

Le code de VoixLivre est distribué sous [licence MIT](LICENSE).

Composants tiers, soumis à leurs propres licences :

- Modèles [Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) — Apache 2.0.
  Les voix Léa, Clara et Margot ont été créées avec Qwen3-TTS VoiceDesign.
- Polices [Archivo](https://github.com/Omnibus-Type/Archivo) et
  [Literata](https://github.com/googlefonts/literata) — SIL Open Font License 1.1 (`assets/fonts/`).
- Icône et identité visuelle : [MontLivre](https://github.com/Simon256px/MontLivre).
