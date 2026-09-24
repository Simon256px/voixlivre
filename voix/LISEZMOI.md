# Voix clonées

Chaque voix de ce dossier est un court extrait audio (10 à 20 s) accompagné du texte prononcé,
décrits dans `voix.json`. VoixLivre les imite grâce au modèle
[Qwen3-TTS-12Hz-1.7B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base).

Format d'une entrée de `voix.json` :

```json
{"nom": "Hélène — grave et feutrée", "fichier": "helene.flac",
 "texte": "texte exact prononcé dans l'extrait", "source": "provenance et licence"}
```

## Ajouter une voix

Bouton **« Ajouter une voix… »** dans VoixLivre : choisissez un extrait de 10 à 20 secondes
(une seule personne, sans musique ni écho) et tapez le texte exact prononcé.
N'utilisez que des voix dont vous avez le droit de vous servir, et indiquez leur source et leur
licence dans ce fichier.
