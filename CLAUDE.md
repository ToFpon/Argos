# Argos — notes de travail

Visionneuse de photos GTK4 / libadwaita en Python, dans la famille d'Odysseus, Ithaca, Nausicaa, Calypso,
Télémaque et Sirène (même ossature : i18n, réglages, fonctionnalités, vrais dialogues, tests, paquet ; reprise de
Sirène). Idée de Tof : remplacer gThumb / FastStone par une visionneuse simple au look GNOME, en .deb.
Nom : Argos le géant aux cent yeux (et le chien d'Ulysse, le seul à le reconnaître à son retour).

## Règles

- Répondre à Tof en français, tutoiement, avis francs. Messages de commit en français.
- Cible : Zorin OS 18.1 (Ubuntu 24.04, GNOME 46, X11, GTK 4.14, libadwaita 1.5, Python 3.12). Pas d'API plus
  récente (pas d'Adw.Dialog, Adw.AlertDialog, Adw.Spinner…). Paquets apt seulement.
- Travail directement sur main (comme les autres applications de Tof). Publier seulement sur « on publie ».
- Le `python3` du bac à sable n'a pas `gi` : utiliser `python3.12`.
- Tout texte visible passe par `tr()` / `ntr()`. Après modification : `python3 tools/update-pot.py`, compléter
  `data/locale/en.po` et `de.po`, puis `msgfmt -c --statistics` et `msgcmp`.
- Dialogues : de vraies fenêtres (`common.AlertDialog`, `common.DialogWindow`, `Adw.PreferencesWindow`,
  `Adw.AboutWindow`). Thème du système : `data/base.css` ne fait que de la mise en page ; seule couleur fixe, le
  noir autour de la photo en plein écran (et le voile du recadrage).
- Chaque fonctionnalité facultative est dans `argos/features.py` (wheel_browse, loop, preload, edit) et se
  consulte au moment de l'usage. `available` / `missing` : composant absent → interrupteur grisé.
- Toute fonction passée à `idle_add` / `timeout_add` renvoie False (sauf répétition voulue).

## Périmètre (choix de Tof, novembre 2026)

1. Regarder les photos d'abord : molette = photo suivante / précédente, Ctrl + molette = zoom autour du pointeur
   (comme FastStone), glisser pour se déplacer, double-clic = plein écran SIMPLE (rien autour, pas de panneaux
   sur les bords), préchargement des voisines.
2. Disposition de gThumb : en-tête (retour, pivoter, recadrer, zoom −/%/+, plein écran, propriétés, menu), bande
   de vignettes en bas, barre d'état « 4 / 858 · 1920 × 1080 · 1,9 Mo · JPEG · 56 % » avec un bouton pour masquer
   la bande ; le mode visualisation SANS plein écran compte (Tof l'aime dans gThumb).
3. Page d'accueil : grille des photos du dossier (+ sous-dossiers).
4. Retouche simple : pivoter, recadrer, enregistrer sous un autre format.
5. Formats : ceux de GdkPixbuf (JPEG, PNG, WebP, GIF animé, TIFF, BMP, SVG, HEIC / AVIF). PAS de RAW, d'import,
   d'étiquettes, de catalogues ni d'impression.

## Architecture

- `backend/folder.py` : `scan(dossier)` → (sous-dossiers, photos) par `os.scandir`, photo = extension connue
  (`IMAGE_EXTS`, jamais le contenu lu), ordre naturel de Nautilus (`name_key` : g_utf8_collate_key_for_filename
  par ctypes, en octets — repris d'Odysseus), tri nom / date / taille, inverse, cachés.
- `backend/loader.py` : `load(chemin)` → `Picture` (texture `Gdk.Texture` faite dans le fil, orientation EXIF
  appliquée, `PixbufAnimation` gardée pour un GIF animé, erreur lisible + paquet à installer si le format n'a pas
  de loader : webp-pixbuf-loader, heif-gdk-pixbuf…). `Loader` : un fil, la photo voulue d'abord puis les voisines
  (±1, +2), cache de 7. PIÈGE corrigé : le rappel d'une photo déjà en cache capturait une variable remise à None.
- `backend/thumbnails.py` : copie de celui d'Odysseus (freedesktop ~/.cache/thumbnails, partagé avec Nautilus,
  échecs dans fail/argos-0.1). `ui/thumbs.py` : deux fils, DERNIÈRE demande servie d'abord, case recyclée =
  demande abandonnée, 600 textures gardées.
- `backend/edit.py` : ImageMagick (`magick` sinon `convert`) — `[0]` + `-auto-orient` d'abord (ce qu'on voit est
  ce qu'on enregistre), `-rotate`, `-crop LxH+X+Y +repage` (coordonnées de l'image TOURNÉE), qualité JPEG / WebP
  de l'original (`identify %Q`, sinon 92), fond blanc pour JPEG / BMP. Sortie sous un nom caché `.argos-*` dans
  le dossier, fsync, puis `os.replace` (remplacer, droits gardés) ou nom libre « x (2).jpg » par lien dur (repli
  renommage sur FAT). `backup` / `restore` : original copié dans ~/.cache/argos/undo pour le « Annuler » du toast,
  balayé après un jour (`sweep_backups`, au démarrage).
- `ui/viewer.py` : `ImageView(Gtk.Widget)` dessiné à la main (`append_scaled_texture`, TRILINEAR en réduction,
  NEAREST à partir de 300 %). État : `fit` (ajusté, sans agrandir une petite image), `zoom`, centre `cx / cy`
  (fraction de l'image), `rotation` (quarts de tour d'affichage), `crop` en attente. `set_zoom(z, at)` garde le
  point sous le pointeur. Molette : signal « navigate » (fonctionnalité `wheel_browse`), Ctrl = zoom ; pavé
  tactile (ScrollUnit.SURFACE) cumulé jusqu'à un cran. Mode recadrage : image entière, cadre `sel` (poignées,
  bords, déplacer, tracer), `crop_ratio`, voile + tiers ; `finish_crop` → `crop` ; `rotate` fait tourner le cadre.
- `ui/window.py` : Gtk.Stack « browser » (grille, `Adw.StatusPage` vide / accès impossible) / « viewer »
  (Adw.ToolbarView : en-tête, Adw.OverlaySplitView avec `ui/info.py` à droite, bande + barre d'état en bas ;
  flèches « osd » au survol). Dossier lu dans un fil (`_scan_seq` : le plus récent gagne), Gio.FileMonitor
  (relecture 400 ms après le dernier événement ; photo affichée modifiée → relue). Retouche : `pending()` (rotation
  ou cadre sur une photo `editable()` — pas d'animation ni de SVG), bouton « Enregistrer » (Adw.SplitButton :
  Enregistrer sous…, Annuler les modifications), `guard(then)` = question « Enregistrer les modifications ? »
  avant de changer de photo, revenir à la grille ou fermer. Clavier en capture (`_on_key`) : ← → Espace Retour
  arrière Page↑↓ Début Fin, Échap (recadrage → plein écran → grille), Entrée applique le recadrage.
- Grille et bande « moteur d'Odysseus » (demande de Tof) : modèle VIRTUEL `gallery.EntryList` (Gio.ListModel, `Item`
  créés à la demande, pour les cases affichées seulement), `max_columns` = vrai nombre de colonnes d'après la largeur
  (`columns_for` dans `do_size_allocate` — déclaré dans CHAQUE classe, pas dans le mixin `_SizedGrid`), modèle branché
  seulement une fois la grille dimensionnée (`set_model` → `_attach_pending`). Mesuré sur 8 000 images : grille en
  1,1 s au lieu de 9,4, 129 cases au lieu de 1 281, retour par le cache 0,02 s au lieu de 8. Miniatures du cache
  décodées UNE fois (`ThumbLoader._cached`, date vérifiée sur le même pixbuf), 3 fils. Nom des dossiers : taille
  normale, deux lignes au plus (demande de Tof).
- SVG (vu chez Tof : icône 48 × 48 minuscule puis pixelisée au zoom) : `ImageView.vector` = chemin du SVG →
  ajusté À la fenêtre (agrandi, comme gThumb), et redessiné net à la taille affichée (`_hires` :
  `Pixbuf.new_from_file_at_scale` dans un fil, 150 ms après le dernier changement de zoom, 6000 px au plus) ; en
  attendant, filtre LINEAR (flou plutôt que pixels). Testé dans ui_smoke.
- Cache des dossiers (demande de Tof : revenir sur un dossier rechargeait tout) : `_folder_cache` (30 dossiers,
  clé = chemin, avec les options de tri / cachés) → grille affichée tout de suite, relue en arrière-plan ; relecture
  identique (`_signature` : chemins, tailles, dates) → modèle gardé tel quel (défilement, vignettes).
- Photo ouverte de l'extérieur (Odysseus, « Ouvrir avec ») : visionneuse TOUT DE SUITE, sans transition, photo lue
  en même temps que le dossier (liste provisoire d'une photo) — vu chez Tof : la grille passait une fraction de
  seconde avant. Grille : nom sous les DOSSIERS seulement (demande de Tof : place pour les photos ; nom dans
  l'infobulle, la barre d'état, les propriétés). Roue de chargement de la grille : Gtk.Spinner 32 px centré (étirée, elle remplissait tout).
- Clic droit sur une photo, visionneuse ET grille (`ui/filemenu.py`, mixin `FileMenu` ; menu de gThumb demandé par
  Tof, sans commentaire / étiquettes / catalogue) : plein écran, définir comme arrière-plan (copie dans
  ~/.local/share/backgrounds sous un nom UNIQUE « argos-NOM-ms.ext » — vu chez Tof : même nom = même adresse, GNOME
  ne rechargeait rien ; ordre options / uri / uri-dark puis `Gio.Settings.sync()`, ancienne copie argos- retirée ;
  PyGObject : `gs.props.settings_schema` (pas gs.settings_schema) ; caché sans le schéma ; testé en mémoire),
  imprimer (Gtk.PrintOperation, page tournée d'après la photo, Ctrl+P), ouvrir avec (applications du type, notre
  lanceur exclu, « Autre application… » = Gtk.AppChooserDialog), copier l'image (texture + fichier, Ctrl+C), copier /
  déplacer vers (Gtk.FileDialog, GIO dans un fil, jamais d'écrasement : `edit.free_name`), renommer (F2, boîte à la
  largeur du nom, extension non sélectionnée), corbeille, supprimer (Maj+Suppr, question de Nautilus). Cible = `_ctx`
  (oubliée 300 ms après la fermeture du menu : l'action part après), sinon la photo affichée / sélectionnée ;
  `_removed` / `_renamed` tiennent listes, cache des dossiers et photo affichée à jour.
  PIÈGE GTK 4.14 (Nausicaa) vu ici : menu accroché à la vue ou au Gtk.GridView = tronqué avec barre de défilement ;
  accroché à un Gtk.Overlay (`view.get_parent()`, `grid_overlay`) = entier. `translate_coordinates` : (x, y) ou
  (ok, x, y) selon PyGObject.
- `ui/sidebar.py` (demande de Tof, comme gThumb) : arbre des dossiers à gauche de la grille (Adw.OverlaySplitView,
  F9 / bouton, réglage `sidebar`, repliée sous 600 sp). Racines : Dossier personnel, Images, Bureau,
  Téléchargements, disques montés (Gio.VolumeMonitor, suivis), Ordinateur (/). Gtk.TreeListModel : GTK demande les
  enfants de CHAQUE ligne affichée (flèche) → lectures par UN fil, dernière demande d'abord (un fil par ligne en
  lançait des centaines). `reveal(chemin)` déplie jusqu'au dossier ouvert et le sélectionne, branche par branche
  au fil des lectures (`_advance`, rappelé seulement par une branche sur le chemin).
- PIÈGE vu chez Tof (vignettes mélangées dans la grille) : `lambda cb=cb: cb(tex)` dans la boucle du fil des
  miniatures capturait `tex`, déjà remplacée par la suivante quand l'idle passait → toujours lier
  (`tex=tex`). Testé dans ui_smoke (une miniature distincte par photo).
- Bande de vignettes (demande de Tof) : GRILLE (Gtk.GridView, passe à la ligne) dans un Gtk.Paned vertical sous la
  photo (`strip_paned`, `resize_end_child=False` : sa hauteur ne change pas avec la fenêtre), hauteur retenue
  (`strip_height`, au glisser seulement), posée quand le séparateur a une taille (« notify::max-position » :
  au « map » il n'en a pas encore), masquée en plein écran.
- Grille : barre d'état en bas (« 14 photos (3,4 Mo) · « x » sélectionnée (1,7 Mo) ») et bouton (i) / touche I =
  propriétés de l'élément sélectionné SOUS l'arbre des dossiers (Gtk.Paned, réglage `browser_info`), comme gThumb
  (demande de Tof) ; `InfoPanel.show_path` lit dimensions et format dans un fil.
  Rien de sélectionné : le dossier ouvert. PIÈGE vu chez Tof (arbre écrasé par le panneau) : posé au « map », le
  séparateur n'avait pas de taille (position 0) → posé sur « notify::max-position » (`_place_side`), proportion
  retenue au glisser (`info_ratio`), arbre de 120 px au moins.
- `ui/info.py` : propriétés (fichier) + prise de vue par GExiv2 (date, appareil, objectif, exposition, ouverture,
  ISO, focale, flash, position), dans un fil.
- `app.py` : instance unique ; `argos PHOTO` → son dossier, photo affichée ; `argos DOSSIER` → grille ; seul →
  dernier dossier (sinon Images). Nom du processus « argos » (prctl, l'icône du Moniteur système en dépend).

## Tests

```sh
python3.12 -m unittest discover -s tests            # moteur (dossier, chargement, retouche par ImageMagick)
xvfb-run -a -s "-screen 0 1400x1000x24" python3.12 tests/ui_smoke.py   # doit finir par « Aucune erreur. »
LANGUAGE=en … ; LANGUAGE=de …
python3 -m pyflakes argos tests tools
```

ui_smoke lance openbox s'il existe (plein écran). Captures : `xvfb-run -a -s "-screen 0 1400x1000x24" python3.12
tools/screenshot.py SORTIE.png CHEMIN [--dark] [--size=LxH] [--zoom=F] [--info] [--crop] [--rotate] [--fullscreen]
[--no-strip] [--prefs=PAGE]` (réglages jetables).

## À faire (feuille de route)

1. FAIT : grille, visionneuse, bande, barre d'état, propriétés, plein écran, retouche, tests.
2. FAIT : traductions en / de (128 textes), application par défaut pour les images (`defaultapp.py` de Calypso,
   TYPES = images du .desktop ; `test_defaultapp`).
3. FAIT (0.1.0, à publier sur « on publie ») : README, paquet `argos-viewer` (nom choisi par Tof ; « argos » est
   PRIS dans Debian forky : client GTK de Mopidy ; argos-viewer libre chez Debian et Ubuntu, vérifié le 21/11/2026),
   commande et processus `argos-viewer` (l'Exec du .desktop et le nom du processus doivent concorder : icône du
   Moniteur système), code privé dans /usr/share/argos-viewer, identifiant io.github.argos.Argos inchangé. Repris de
   Calypso : debian/ (rules, bin, install, manpages), `.github/workflows/release-deb.yml` (commit « Version… »),
   `tools/ppa.sh` (ppa:nourpon/argos, noble puis resolute), `tools/site.sh` (dossier Argos des pages perso Free).
   Recommends : imagemagick, gir1.2-gexiv2-0.10, webp-pixbuf-loader, heif-gdk-pixbuf, librsvg2-common. Vérifié :
   dpkg-buildpackage, lintian sans remarque, appstreamcli, installé puis lancé (python3.12).
   Site (`site/index.html`, anglais, tons dorés ; Odysseus bleu, Ithaca violet, Nausicaa orange, Calypso turquoise,
   Télémaque vert, Sirène corail), https://tofmicro.pages-perso.free.fr/Argos/ ; version dans la pastille et
   l'accroche, à tenir à jour. Captures de `site/img/` en anglais, sombre sauf light.png, sur des photos de la NASA
   (domaine public, crédit en bas du site et du README ; Wikimedia refusait nos requêtes : 429) copiées dans
   /home/tof/Photos (chemin court dans les propriétés) : `HOME=/home/tof LANGUAGE=en xvfb-run -a -s "-screen 0
   1400x1000x24" python3.12 tools/screenshot.py site/img/NOM.png CHEMIN --size=1200x780 --delay=3000 [options]` —
   dark (Mount Everest.jpg --dark), light (Andes and the Pacific.jpg), grid (dossier --dark --info), crop (Lake
   Powell, Utah.jpg --dark --crop), info (Antennae galaxies.jpg --dark --info). Photos : images-api.nasa.gov.
4. Essai chez Tof.
