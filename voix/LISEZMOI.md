# Voix clonées

Chaque voix de ce dossier est un court extrait audio (10 à 20 s) accompagné du texte prononcé,
décrits dans `voix.json`. VoixLivre les imite grâce au modèle
[Qwen3-TTS-12Hz-1.7B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base).

## Voix fournies

Femmes d'environ 30 ans, françaises natives, créées avec
[Qwen3-TTS-12Hz-1.7B-VoiceDesign](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)
à partir d'une description écrite (reprise dans le champ `source` de `voix.json`). Pour chaque
voix, le meilleur de 5 essais a été retenu (intelligibilité vérifiée par Whisper, débit, hauteur).

| Voix | Timbre | Hauteur moyenne |
|---|---|---|
| Léa | velouté, un peu grave, riche | ≈ 185 Hz |
| Clara | serein, médium, chaleureux | ≈ 205 Hz |
| Margot | clair, médium-aigu, net | ≈ 230 Hz |

Toutes trois sont confiantes, avec une lecture envoûtante : intime, légèrement soufflée, lente et
mélodieuse.

## Format

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
