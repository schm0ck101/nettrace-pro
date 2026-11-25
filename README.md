# NetTrace Pro — documentation

![SCHMOCK](assets/schmock.png)

Créé par **SCHMOCK** — <https://schmock.fr>

NetTrace Pro est un logiciel de diagnostic réseau pour Windows 10 et 11, inspiré
de PingPlotter. Il mesure en continu la latence, le jitter et les pertes de
paquets vers une ou plusieurs destinations, et pour **chaque routeur** du
chemin (traceroute continu). Il fonctionne entièrement en local : aucun
compte, aucun abonnement, aucune donnée envoyée à un tiers (sauf la requête
facultative qui affiche votre IP publique).

---

## 1. Installation

### Avec l'installateur (recommandé)
1. Lancez `NetTracePro-Setup-1.0.0.exe`.
2. Suivez l'assistant. Par défaut, l'installation se fait **pour votre
   compte uniquement, sans droits administrateur**, dans
   `%LOCALAPPDATA%\Programs\NetTrace Pro`.
3. Options : icône sur le Bureau, lancement au démarrage de Windows.

Désinstallation : *Paramètres Windows › Applications › NetTrace Pro*.

### Version portable
`NetTracePro-Portable.exe` est un fichier unique qui se lance sans
installation (le premier démarrage prend quelques secondes de plus, le temps
de décompresser).

### Depuis les sources
Prérequis : Python 3.12 ou plus récent.
```
run.bat
```
Le premier lancement crée l'environnement `.venv` et installe les
dépendances (`requirements.txt`).

> **Droits administrateur : inutiles.** Les mesures utilisent l'API ICMP
> native de Windows (la même que `ping` et `tracert`).

---

## 2. Prise en main

### Surveiller une destination
1. Page **Surveillance** (ou `Ctrl+N`).
2. Saisissez une adresse IP (`8.8.8.8`), un nom de domaine (`www.google.com`)
   ou une adresse de serveur (`https://exemple.fr:8080` est accepté, seul le
   nom est gardé).
3. Choisissez :
   - **Traceroute continu** (tous les sauts) ou **Ping seul** (la destination) ;
   - IPv4/IPv6 automatique, IPv4 ou IPv6 ;
   - l'intervalle : 0,5 s, 1 s, 2 s, 5 s ou 10 s ;
   - surveillance continue, ou un nombre fixe d'échantillons.
4. Cliquez **Démarrer**. La destination s'ouvre dans un onglet.

Vous pouvez ouvrir autant d'onglets que nécessaire : chaque destination a
ses propres graphiques et statistiques. Le **Tableau de bord** les résume
toutes, avec un bouton *Surveiller tous les favoris*.

### Pendant la surveillance
| Commande | Effet |
|---|---|
| **Pause / Reprendre** | suspend les mesures sans perdre les données |
| **Arrêter / Redémarrer** | termine la session (elle reste dans l'historique) |
| **Intervalle** | modifiable en direct |
| **Export CSV / Rapport PDF** | exporte la session en cours |
| Fermer l'onglet (`Ctrl+W`) | arrête la surveillance |

---

## 3. Lire les résultats

### Cartes de statistiques
Actuel, moyenne, minimum, maximum, jitter, pertes, paquets reçus/envoyés —
calculés sur la **période d'analyse** (par défaut les 100 derniers
échantillons). La ligne « session » donne la valeur sur toute la session.

### Tableau des sauts
Une ligne par routeur entre votre PC et la destination : numéro du saut,
pertes, IP, nom DNS, latence moyenne/min/max/actuelle, jitter, réponses
reçues/envoyées, **état** et un mini‑graphique :
- barre rouge = pourcentage de pertes ;
- trait gris = plage min–max ; point coloré = moyenne ; trait vertical = dernière mesure.

Cliquez sur un en‑tête pour trier. Cliquez sur une ligne pour afficher le
graphique **de ce saut** (bouton « Graphique : … ✕ » pour revenir à la
destination).

### Comment interpréter un saut « en rouge » ?
Beaucoup de routeurs limitent ou dépriorisent les réponses ICMP qui leur
sont adressées. Une perte ou une latence élevée **sur un saut intermédiaire
n'est un problème que si elle se retrouve sur les sauts suivants et sur la
destination**. NetTrace Pro applique cette règle automatiquement :

| État affiché | Signification |
|---|---|
| OK / Destination OK | rien à signaler |
| Pertes ICMP non significatives (gris) | le routeur filtre ou limite l'ICMP ; les sauts suivants vont bien : **sans impact** |
| Ne répond pas à l'ICMP (sans impact) | routeur silencieux (« * »), normal |
| Réponse ICMP lente (sans impact) | le routeur répond lentement mais le trafic qui le traverse ne ralentit pas |
| **Perte de paquets x %** | les pertes commencent à ce saut et se propagent jusqu'à la destination |
| **Hausse de latence (+x ms)** | la latence augmente à ce saut et reste élevée ensuite |
| **Jitter important** | variation forte se répercutant sur la destination |
| **Saut qui ne répond plus** | plus aucune réponse à partir de ce saut |
| … — dégradation persistante | le problème existe aussi sur la longue durée (300 derniers échantillons) |

> À 0,5 s d'intervalle, beaucoup de routeurs (Linux notamment) ne
> renvoient qu'environ une réponse par seconde : leurs pertes affichées
> sont alors élevées mais marquées « non significatives ». C'est normal.

### Graphiques
- **Latence** : courbe colorée selon les seuils (vert, jaune, orange, rouge),
  barres rouges verticales = paquets perdus, pointillés = seuils.
- **Pertes de paquets** : % de pertes par tranche de temps.
- **Jitter** : jitter glissant, avec le seuil d'alerte.
- **Comparatif par saut** : latence moyenne (barre) + min/max, et pertes par
  saut (gris = sans impact).

Navigation : **molette** = zoom dans le temps, **glisser** = se déplacer
dans l'historique, **clic** = analyser la période jusqu'à cet instant (le
tableau et les cartes se recalculent), **double‑clic** ou bouton
**● Direct** = revenir aux mesures en temps réel. La liste *Graphique*
règle la durée affichée (1 min à 24 h, ou tout).

### Couleurs
| Couleur | Signification (seuils par défaut, modifiables) |
|---|---|
| 🟢 Vert | connexion normale (< 100 ms) |
| 🟡 Jaune | latence élevée (≥ 100 ms) |
| 🟠 Orange | forte dégradation (≥ 200 ms) ou pertes ≥ 5 % |
| 🔴 Rouge | problème important (≥ 400 ms), pertes ≥ 15 %, interruption |

---

## 4. Alertes et événements
NetTrace Pro détecte et journalise :
- **interruption de connexion** (3 pertes consécutives par défaut) et son
  rétablissement, avec la durée de coupure ;
- latence élevée / forte dégradation et retour à la normale ;
- pertes de paquets ;
- jitter important ;
- changement de route (nouveau routeur sur un saut, longueur de route modifiée) ;
- échec de résolution DNS (nouvel essai automatique toutes les 10 s).

Chaque événement apparaît dans une **notification** en bas à droite de la
fenêtre, dans la page **Événements** (filtrable, exportable en CSV) et, si la
fenêtre est en arrière‑plan, dans une **notification Windows**. Les alertes
n'ont lieu qu'aux changements d'état (pas de rafale).

---

## 5. Historique, comparaison, exports
Chaque session est enregistrée automatiquement (base SQLite locale).

Page **Historique** :
- **Ouvrir** : recharge la session complète dans un onglet, avec graphiques
  et tableau (lecture seule) ;
- **Comparer** :
  - plusieurs sessions sélectionnées → courbes superposées + tableau comparatif ;
  - une seule session → découpage par 15 min / heure / 6 h / jour pour
    comparer les périodes (idéal pour repérer la saturation du soir) ;
- **Exporter CSV** : deux fichiers — tous les échantillons (une colonne par
  saut) et le résumé par saut. Format Excel français (`;`, virgule décimale) ;
- **Rapport PDF** : synthèse, graphiques, tableau des sauts, événements ;
- **Renommer / notes**, **Supprimer**.

Les sessions plus anciennes que la durée de conservation (90 jours par
défaut) sont purgées au démarrage.

---

## 6. Réseau local
Nom du poste, IPv4 et IPv6 locales, passerelle (détectée par une sonde TTL 1),
IP publiques IPv4/IPv6 (service ipify.org, actualisé toutes les 10 min),
disponibilité IPv4/IPv6, et un outil de **résolution DNS** (A/AAAA + nom inverse).

---

## 7. Paramètres
- **Mesures** : intervalle par défaut, délai d'attente (2000 ms), taille des
  paquets (32 octets), nombre max. de sauts (30), version IP, mode, période
  d'analyse, résolution DNS des sauts.
- **Seuils d'alerte** : latence (jaune/orange/rouge), pertes, jitter, nombre
  de pertes consécutives = interruption, fenêtre d'évaluation.
- **Interface** : thème sombre/clair (aussi via le bouton en bas de la barre
  latérale), durée affichée, échelle fixe, notifications, réduction dans la
  zone de notification, signal sonore.
- **Historique** : enregistrement, conservation, mémoire par destination.
- **Favoris** : destinations du démarrage rapide.

Raccourcis : `Ctrl+N` nouvelle destination, `Ctrl+W` fermer l'onglet, `F5` actualiser.

---

## 8. Où sont mes données ?
`%APPDATA%\NetTracePro\`
- `settings.json` — paramètres
- `history.db` — historique (SQLite)
- `nettrace.log` — journal technique (en cas de problème)

Bouton *Ouvrir le dossier des données* dans les Paramètres.

---

## 9. Pour les développeurs

### Architecture
```
main.py                     point d'entrée
nettrace/
  app.py                    démarrage, gestion globale des erreurs
  config.py                 paramètres (JSON) et valeurs par défaut
  events.py                 modèle d'événement réseau
  logger.py                 journal fichier + bus d'événements Qt
  net/                      MOTEUR RÉSEAU (sans interface)
    icmp.py                 ICMP natif Windows (IcmpSendEcho2 / Icmp6SendEcho2, TTL)
    tracer.py               traceroute continu, sessions, pool de threads
    stats.py                tampon circulaire numpy, statistiques, jitter
    analysis.py             diagnostic saut par saut
    alerts.py               détection d'anomalies (machine à états)
    dns.py                  résolution directe/inverse (cache)
    netinfo.py              IP locales et publiques
    monitor.py              gestion des surveillances simultanées
  storage/
    history.py              historique SQLite (thread d'écriture, WAL)
    export.py               CSV et rapport PDF
  ui/                       INTERFACE (PySide6)
    main_window.py          fenêtre, barre latérale, onglets, zone de notification
    target_view.py          vue d'une destination
    hop_table.py            tableau des sauts + graphique intégré
    charts.py               graphiques temps réel (pyqtgraph)
    dashboard.py, history_view.py, events_view.py, netinfo_view.py, settings_view.py
    notifications.py        notifications visuelles
    theme.py                thèmes clair/sombre, icônes
tests/
  test_core.py              tests unitaires (pytest)
  smoke_ui.py               test fonctionnel de l'interface avec mesures réelles
  stress_ui.py              test d'endurance (fuites mémoire, threads)
```

### Choix techniques
- **API ICMP Windows (ctypes)** plutôt que Scapy : pas de droits
  administrateur, pas de pilote Npcap, TTL réglable, IPv4 et IPv6.
- **Traceroute « à la PingPlotter »** : à chaque intervalle, une sonde par
  TTL est envoyée en parallèle ; les cycles peuvent se chevaucher, un saut
  lent ne retarde jamais les autres.
- **PyQtGraph** plutôt que Matplotlib : rendu Qt natif, fluide avec des
  dizaines de milliers de points, zoom/défilement intégrés.
- **numpy** : tampon circulaire de taille fixe par destination (mémoire
  constante pendant des heures), statistiques vectorisées.
- **SQLite** en mode WAL avec un thread d'écriture qui regroupe les
  insertions : le disque ne ralentit jamais les mesures.
- **PDF via Qt** (QTextDocument + QPdfWriter) : aucune dépendance de plus.
- L'interface ne fait aucun appel réseau : elle relit les données toutes les
  0,5 à 1 s (minuterie Qt) ; les mesures tournent dans des threads.

### Compiler
```
powershell -ExecutionPolicy Bypass -File build.ps1
```
Produit dans `dist\` : `NetTracePro\NetTracePro.exe` (dossier autonome),
`NetTracePro-Portable.exe` et, si Inno Setup 6 est installé
(`winget install JRSoftware.InnoSetup`), `NetTracePro-Setup-1.0.0.exe`.

### Tests
```
.venv\Scripts\python -m pytest -q tests\test_core.py
.venv\Scripts\python tests\smoke_ui.py <dossier_captures> 20
.venv\Scripts\python tests\stress_ui.py <dossier> 600
```

### Limites connues
- Windows uniquement (API ICMP de `iphlpapi.dll`).
- Mesures ICMP uniquement (pas de sondes TCP/UDP) : certains serveurs de jeu
  ou pare‑feu bloquent l'ICMP — ils apparaîtront « sans réponse » alors que
  le service fonctionne.
- La précision est d'environ 0,1 ms (horloge haute résolution, recoupée avec
  la valeur de l'API qui est à la milliseconde).
