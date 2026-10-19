# VoixLivre

> Écouter ses ebooks lus à voix haute, sur son propre ordinateur.

VoixLivre ouvre un livre **EPUB**, **TXT** ou **PDF** et le lit avec une voix de synthèse naturelle,
générée localement sur votre carte graphique par le modèle
[Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice). Pendant l'écoute, un
dictionnaire explique les mots difficiles du passage lu. Aucun compte, aucun abonnement, aucun envoi de
vos livres sur Internet.

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Utilisation](#utilisation)
- [Dictionnaire](#dictionnaire)
- [Voix](#voix)
- [Activité Discord](#activité-discord)
- [Bon à savoir](#bon-à-savoir)
- [Contribuer](#contribuer)
- [Soutenir le projet](#soutenir-le-projet)
- [Licence](#licence)

## Fonctionnalités

**Lecture**

- Le texte s'affiche pendant l'écoute ; le passage en cours est surligné et reste au milieu de la page
- Texte remis en paragraphes propres pour une lecture fluide : phrases recollées, numéros de page et
  appels de note retirés de ce qui est lu, coupures placées aux pauses naturelles
- Liste des chapitres ; un double-clic sur un chapitre ou une phrase y lance la lecture
- Lecture continue sans blanc et sauts instantanés, grâce à une génération par lots mise en cache
- Images et notes de l'ebook affichées à leur place, jamais lues : aperçu de la note au survol de
  l'appel, façon Obsidian (épinglé au clic), liste des notes en fin de chapitre

**Dictionnaire**

- Panneau à droite qui suit la lecture : les mots difficiles du passage lu remontent en tête avec
  leur définition courte ; un clic affiche la définition longue
- Mots soulignés en vert dans le texte, définition au survol, épinglée au clic
- Concepts, mots savants, termes grecs, latins et allemands, personnes citées, œuvres, lieux et
  événements — voir [Dictionnaire](#dictionnaire)

**Bibliothèque**

- Au démarrage : couverture, titre, auteur, progression et date de dernière lecture de chaque livre
- Ajout de plusieurs livres d'un coup, retrait par clic droit (le fichier n'est jamais supprimé)
- Marque-page posé automatiquement là où l'on s'arrête, reprise en un clic
- Surlignages personnels, enregistrés par livre

**Confort**

- Interface aux couleurs de [MontLivre](https://github.com/Simon256px/MontLivre), mode jour / nuit
  mémorisé
- Activité Discord facultative, avec un mode discret — voir [Activité Discord](#activité-discord)

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
python voixlivre.py                  # ouvre la bibliothèque
python voixlivre.py monlivre.epub    # ouvre directement un livre
```

Sous Windows, un double-clic sur `VoixLivre.bat` lance l'application sans fenêtre de console.
Le petit livre `exemple.epub` est fourni pour un premier essai.

| Geste                                   | Action                                              |
| --------------------------------------- | --------------------------------------------------- |
| `Espace`                                | lecture / pause                                     |
| `←` / `→`                               | passage précédent / suivant                         |
| double-clic sur une phrase              | lire à partir de cette phrase                       |
| survol d'un mot souligné en vert        | définition du dictionnaire                          |
| survol d'un appel de note (orange)      | aperçu de la note                                   |
| clic sur un mot ou un appel de note     | garder la bulle ouverte                             |
| sélection puis « Surligner »            | surligner un passage                                |
| clic droit dans le texte                | surligner, retirer un surlignage, lire à partir d'ici, placer le marque-page |
| clic droit sur un livre (bibliothèque)  | ouvrir, retirer de la bibliothèque                  |

## Dictionnaire

Le panneau **Dictionnaire**, à droite de la page, accompagne la lecture : à chaque passage lu, les
mots qui méritent une explication remontent en tête, marqués d'une barre verte, avec une définition
courte. Un clic sur une carte déplie la définition longue ; les 30 derniers mots restent consultables
en faisant défiler le panneau. Le bouton « Dictionnaire » masque ou réaffiche le panneau.

Les dictionnaires ne sont pas générés pendant la lecture : ils sont **rédigés à l'avance**, une fois
par livre, puis enregistrés dans le dossier [`dictionnaires/`](dictionnaires/). VoixLivre reconnaît
le livre à l'empreinte de son fichier, ou à son titre (la version PDF d'un livre profite ainsi du
dictionnaire de sa version EPUB).

| Livre                                                    | Entrées |
| -------------------------------------------------------- | ------- |
| Bernard Stiegler, *Qu'appelle-t-on panser ? 2. La leçon de Greta Thunberg* | 519 |

Les définitions sont originales : aucun extrait des livres n'y est reproduit. Pour un livre sans
dictionnaire, le panneau l'indique simplement.

<details>
<summary>Format d'un fichier de dictionnaire</summary>

```json
{
  "livre": { "titre": "Titre du livre", "auteur": "Auteur", "sha1": ["empreinte du fichier"] },
  "entrees": [
    {
      "terme": "Pharmakon",
      "type": "concept",
      "formes": ["pharmakon", "pharmaka"],
      "court": "Définition en une phrase.",
      "long": "Définition développée."
    }
  ]
}
```

- `type` : `concept`, `mot`, `personne`, `oeuvre`, `evenement` ou `lieu`.
- `formes` : toutes les graphies à reconnaître dans le texte. Une forme avec majuscule (nom propre)
  respecte la casse ; une forme en minuscules est aussi reconnue en début de phrase. En cas de
  chevauchement, la forme la plus longue l'emporte (« Augustin Berque » avant « Augustin »).

</details>

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

VoixLivre peut afficher sur votre profil Discord que vous écoutez un livre : titre, auteur,
progression et temps écoulé depuis l'ouverture de l'application, avec un lien vers ce dépôt. Cliquez
sur « Discord » puis cochez « Afficher sur mon profil Discord ce que j'écoute ».

Les textes sont personnalisables (`{titre}`, `{auteur}`, `{chapitre}`, `{progression}`, `{voix}`),
et le mode discret affiche seulement « Écoute un livre », sans dévoiler lequel. La fonction est
désactivée par défaut. L'activité apparaît sur votre carte de profil et dans les listes de membres
pendant l'écoute ; le bouton lien n'est visible que par les autres.

## Bon à savoir

- Manon (voix « Sohee » du modèle) n'est pas francophone native : un style contenant « sans accent »
  réduit l'accent.
- Les voix clonées reproduisent le ton de leur extrait : le champ « Style » est alors désactivé.
- La première utilisation d'une voix clonée charge un second modèle (environ 20 s).
- Le message « SoX could not be found » affiché au démarrage est sans conséquence.
- La progression, les marque-pages, les surlignages et les réglages sont enregistrés dans
  `~/.voixlivre.json` ; le journal technique dans `~/.voixlivre.log`.
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
