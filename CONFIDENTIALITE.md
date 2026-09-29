# Politique de confidentialité de VoixLivre

*Dernière mise à jour : 29 septembre 2026*

> **En bref (English summary below)** — VoixLivre fonctionne entièrement sur votre ordinateur.
> Il ne collecte, ne stocke sur un serveur ni ne vend aucune donnée personnelle. La seule
> information qui quitte l'application est l'activité Discord, **si et seulement si vous
> l'activez**, et elle contient uniquement ce que vous choisissez d'afficher.

## 1. Qui sommes-nous

VoixLivre est une application libre et gratuite de lecture de livres numériques à voix haute,
développée par **Simon256px**. Code source : <https://github.com/Simon256px/voixlivre>.

## 2. Données traitées et où elles restent

VoixLivre n'a **aucun serveur** et **aucun compte utilisateur**. Tout est enregistré
**uniquement sur votre ordinateur** :

| Donnée | Emplacement | Utilité |
|---|---|---|
| Position de lecture, marque-pages, surlignages, bibliothèque, réglages (voix, thème, Discord) | `~/.voixlivre.json` | Reprendre la lecture là où vous l'avez laissée |
| Vignettes des couvertures | `~/.voixlivre/couvertures/` | Afficher la bibliothèque |
| Journal technique (dont les états de connexion à Discord) | `~/.voixlivre.log` | Diagnostiquer un problème |
| Voix que vous ajoutez (extrait audio et texte) | dossier `voix/` de l'application | Imiter la voix choisie |

Vos livres ne sont jamais copiés ni envoyés nulle part : ils sont lus depuis leur emplacement.
La synthèse vocale est réalisée **sur votre ordinateur** par les modèles Qwen3-TTS.

Vous pouvez tout effacer à tout moment en supprimant les fichiers ci-dessus.

## 3. Ce qui peut sortir de votre ordinateur

- **Activité Discord (désactivée par défaut).** Si vous l'activez, VoixLivre envoie à
  l'application Discord **installée sur votre ordinateur** le texte de l'activité : selon vos
  réglages, le titre, l'auteur, le chapitre, la progression et la voix, ou seulement un texte
  neutre (« Écoute un livre ») en **mode discret**, ainsi que le temps d'écoute et un bouton
  lien. Discord l'affiche ensuite sur votre profil selon **ses propres règles de
  confidentialité** (<https://discord.com/privacy>). VoixLivre ne reçoit aucune donnée de
  Discord et n'accède ni à votre compte, ni à vos messages, ni à vos serveurs.
- **Téléchargements de l'application.** Au premier lancement, les modèles de voix sont
  téléchargés depuis Hugging Face (<https://huggingface.co/privacy>). L'icône affichée dans
  l'activité Discord est chargée par Discord depuis GitHub. Ces services voient une requête
  réseau ordinaire, comme pour n'importe quel téléchargement.

VoixLivre ne contient **aucune publicité, aucun traceur, aucune mesure d'audience**.

## 4. Vos droits

Comme aucune donnée n'est collectée par le développeur, il n'y a rien à consulter, rectifier ou
supprimer de notre côté : vos données sont entièrement entre vos mains, sur votre ordinateur.
Pour l'activité Discord, vous pouvez la désactiver ou passer en mode discret à tout moment
(bouton « Discord » de l'application).

## 5. Contact et modifications

Questions : <https://github.com/Simon256px/voixlivre/issues>.
Toute modification de cette politique est publiée dans ce fichier, dont l'historique est public.

---

## English summary

VoixLivre runs entirely on your computer. It has no server and no user accounts, and it does not
collect, sell or share personal data. Reading progress, bookmarks, highlights, library and
settings are stored locally (`~/.voixlivre.json`). Speech is synthesized locally. The only data
that can leave the app is the **Discord activity, which is off by default**: when enabled, the
text you choose (book title/author/progress, or a neutral text in discreet mode) is sent to the
Discord client on your computer and displayed according to Discord's privacy policy. Voice models
are downloaded once from Hugging Face. No ads, trackers or analytics. Contact:
<https://github.com/Simon256px/voixlivre/issues>.
