# Fieldnotes AI — démarrage

Deux fonctions Azure indépendantes traitent les retours d’expérience dans `src/func-002/` et `src/func-003/`.

1. Créez un environnement Python compatible Azure Functions.
2. Installez les dépendances du sous-projet choisi : `pip install -r src/func-003/requirements.txt`.
3. Consultez son fichier `.env.sample` et renseignez les variables dans votre environnement local ou les paramètres de votre Function App.
4. Exécutez `func start` depuis le dossier de la fonction après installation d’Azure Functions Core Tools.

Les colonnes métier, index de recherche et déploiements LLM doivent être provisionnés dans votre propre environnement. Aucun corpus documentaire n’est livré.
