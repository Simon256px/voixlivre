# Contribuer à VoixLivre

Merci de votre intérêt pour VoixLivre. Toute aide est la bienvenue : signaler un bug, proposer une
idée, corriger une faute ou envoyer du code.

## Signaler un bug

Ouvrez une [issue](https://github.com/Simon256px/voixlivre/issues) en précisant :

- ce que vous avez fait, ce que vous attendiez et ce qui s'est passé ;
- votre système (Windows, Linux, macOS), votre version de Python et votre carte graphique ;
- le format du livre concerné (EPUB, TXT, PDF), sans joindre de livre protégé par le droit
  d'auteur ;
- le message d'erreur complet s'il y en a un (lancez `python voixlivre.py` dans un terminal pour le
  voir).

## Proposer une idée

Ouvrez une issue qui décrit le besoin avant la solution : à quoi servirait la fonction, et dans quelle
situation. VoixLivre vise une interface simple et sobre ; les idées qui ajoutent peu de réglages ont
le plus de chances d'être retenues.

## Proposer du code

1. Forkez le dépôt et créez une branche à partir de `main`.
2. Installez l'application (voir le [README](README.md#installation)).
3. Faites votre modification, puis vérifiez à la main qu'elle fonctionne : ouvrir `exemple.epub`,
   lancer la lecture, changer de voix, passer en mode nuit, fermer et rouvrir le livre.
4. Ouvrez une pull request qui explique ce qui change et pourquoi.

### Style du code

- Tout le code tient dans `voixlivre.py` : gardez-le ainsi, sauf bonne raison.
- Textes de l'interface, commentaires et messages de commit en français.
- Suivez le style du code existant (PEP 8, noms explicites, commentaires brefs).
- N'ajoutez une dépendance à `requirements.txt` que si elle est vraiment nécessaire.
- Respectez l'identité visuelle de [MontLivre](https://github.com/Simon256px/MontLivre) : polices
  Archivo et Literata, palette existante.

### Messages de commit

Une phrase courte au présent qui décrit le changement, par exemple `Corrige le démarrage sans
console` ou `Discord : mode discret`.

## Ajouter une voix

Les voix fournies doivent pouvoir être librement redistribuées. Une voix proposée pour le dépôt doit
être créée par synthèse (par exemple avec Qwen3-TTS VoiceDesign) ou provenir d'un enregistrement dont
vous détenez les droits, avec le consentement de la personne enregistrée. Indiquez sa provenance et sa
licence dans le champ `source` de `voix/voix.json` (voir [voix/LISEZMOI.md](voix/LISEZMOI.md)).

## Respect

Restez courtois et bienveillant dans les issues et les pull requests. Les échanges irrespectueux
seront supprimés.

## Licence

En proposant une contribution, vous acceptez qu'elle soit distribuée sous la [licence MIT](LICENSE)
du projet.

## Soutenir le projet

Pas le temps de contribuer ? Vous pouvez aussi soutenir VoixLivre sur
[ko-fi.com/simon256px](https://ko-fi.com/simon256px).
