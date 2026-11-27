# Argos — notes de travail

Visionneuse de photos GTK4 / libadwaita en Python, dans la famille d'Odysseus, Ithaca, Nausicaa, Calypso,
Télémaque et Sirène (même ossature : i18n, réglages, fonctionnalités, vrais dialogues, tests, paquet ; reprise de
Sirène). Idée de Tof : remplacer gThumb / FastStone par une visionneuse simple au look GNOME, en .deb.
Nom : Argos le géant aux cent yeux (et le chien d'Ulysse, le seul à le reconnaître à son retour).

## Règles

- Répondre à Tof en français, tutoiement, avis francs. Messages de commit en français.
- Dossiers des dépôts chez Tof (la casse compte, dans les commandes à lui donner) : ~/argos, ~/Calypso,
  ~/Ithaca, ~/Nausicaa, ~/Odysseus, ~/Sirene, ~/Telemaque. Toujours donner les commandes complètes, `git pull` compris.
- Cible : Zorin OS 18.1 (Ubuntu 24.04, GNOME 46, X11, GTK 4.14, libadwaita 1.5, Python 3.12). Pas d'API plus
  récente (pas d'Adw.Dialog, Adw.AlertDialog, Adw.Spinner…). Paquets apt seulement.
- Travail directement sur main (comme les autres applications de Tof). Publier seulement sur « on publie ».
- Le `python3` du bac à sable n'a pas `gi` : utiliser `python3.12`.
- Tout texte visible passe par `tr()` / `ntr()`. Après modification : `python3 tools/update-pot.py`, compléter
  `data/locale/en.po` et `de.po`, puis `msgfmt -c --statistics` et `msgcmp`.
- Dialogues : de vraies fenêtres (`common.AlertDialog`, `common.DialogWindow`, `Adw.PreferencesWindow`,
  `Adw.AboutWindow`). Thème du système : `data/base.css` ne fait que de la mise en page ; seule couleur fixe, le
  noir autour de la photo en plein écran (et le voile du recadrage).
- Chaque fonctionnalité facultative est dans `argos/features.py` (wheel_browse, loop, preload, edit, videos, annotate) et se
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
- Présentation de gThumb (captures comparatives de Tof, 22/11/2026) : grille de PHOTOS seulement (les dossiers sont
  dans l'arbre ; dossier sans image = « Aucune image »), cases de 128 px (`grid_size`, défaut de gThumb), bande de
  96 px ; vignette préparée à la taille de la case par `thumbs` (un chargeur par taille : `shared(px)`, résultat
  (texture, photo)), JAMAIS plus grande que l'image (icône de 48 px = 48 px), centrée ; photo (pas de transparence)
  dans un cadre blanc + ombre (`picture.argos-photo`, exception voulue aux couleurs du thème : le « tirage »), icône
  sans cadre. Arbre : PAS `single_click_activate` (il sélectionnait la ligne sous le pointeur : la sélection
  suivait la souris) — choisir une ligne (`notify::selected-item`) ouvre le dossier, `reveal` sélectionne sans
  rouvrir (`_select` en silence, `_current`) ; un clic déplie aussi la branche (demande de Tof, comme gThumb).
- Sélection MULTIPLE dans la grille (demande de Tof) : Gtk.MultiSelection + cadre à la souris (Ctrl / Maj + clic,
  Ctrl+A fournis par GTK) ; `Grid.selected_entries()` (Gtk.BitsetIter : (ok, iter, pos) puis iter.next() → (ok, pos)).
  `FileMenu._targets()` : la sélection de la grille (clic droit hors sélection = elle seule), la photo affichée dans
  la visionneuse ; plusieurs → menu réduit (`_multi_menu` : copier / déplacer vers, corbeille, supprimer) ; Suppr /
  Maj+Suppr marchent dans la grille ; `_removed_many` tient tout à jour ; barre d'état « N sélectionnées (X Mo) ».
- Outils image d'Odysseus (demande de Tof) : « Convertir / redimensionner… » (Ctrl+E, clic droit sur une ou
  plusieurs photos, menu principal) = `backend/imagetools.py` (copie de `odysseus/backend/images.py` : une passe
  d'ImageMagick, sélecteur `[0]`, `-auto-orient`, sortie cachée puis `edit._place_new`, remplacer = même format
  `os.replace` / autre format nouvelle image puis original à la corbeille ; Job réduit : progression, annulation
  qui tue ImageMagick) + `ui/image_dialog.py` (dialogue d'Odysseus, progression et Annuler DANS le dialogue,
  fermer = annuler ; derniers choix dans les réglages `image_*`). Traductions reprises des catalogues d'Odysseus.
- Annoter (idée d'un utilisateur allemand ; reprise de `odysseus/ui/annotate.py` — Argos reste INDÉPENDANT
  d'Odysseus, choix de Tof) : `ui/annotate.py`, fonctionnalité `annotate` (cairo : python3-gi-cairo en Depends),
  touche A, bouton crayon de l'en-tête (`annotate_button`, suit la fonctionnalité en direct), clic droit, menu
  principal ; une fenêtre par photo (`_annotating`), retouches en attente d'abord (`guard`). Mêmes outils et
  libellés qu'Odysseus (« Annoter l'image… », traductions reprises de ses catalogues). Différences : pas de couleur
  fixe derrière l'image ; enregistrement SÛR `edit.write_surface` (nom caché, fsync, remplacement — lien symbolique :
  la photo visée —, ou nom libre) au lieu d'écrire par-dessus : PNG par cairo, autres formats par GdkPixbuf
  (`surface_pixbuf` : RGB par tranches d'octets, `Gdk.pixbuf_get_from_surface` est déprécié et garde l'alpha) ;
  Ctrl+S = la photo elle-même (original dans ~/.cache/argos/undo → « Annuler » du toast d'Argos,
  `MainWindow.annotation_saved`), métadonnées reprises par GExiv2 (`_copy_metadata` : orientation remise à
  « normale », les pixels sont déjà tournés ; vignette EXIF retirée) ; format que GdkPixbuf n'écrit pas (GIF, HEIC,
  AVIF, SVG ; WebP sans webp-pixbuf-loader) → « Enregistrer sous » « nom (annotée).png ». Testé : `test_annotate`,
  étape « Annoter » d'ui_smoke ; capture `tools/screenshot.py … --annotate` (site/img/annotate.png, Mount Everest).
  Retours de l'utilisateur allemand (0.4.0), corrigés aussi dans Odysseus : Ctrl+Z sans effet tant que le curseur était
  dans le champ « Épaisseur » (le Gtk.Text de la SpinButton prenait Ctrl+Z pour lui) → raccourcis en CAPTURE
  (Gtk.ShortcutController LOCAL + PropagationPhase.CAPTURE) et le canevas prend le focus quand on dessine (vérifié par
  de vraies touches xdotool sous openbox). Une fois : « A window is shown after it has been destroyed » puis gel en
  fermant (non reproduit) → fenêtre détruite en idle APRÈS la question, une seule question à la fois (`_close_asked`,
  remis à faux si l'enregistrement échoue ou si « Enregistrer sous » est abandonné).
- Vidéos (demande de Tof, comme gThumb ; fonctionnalité `videos`, active par défaut, grisée sans le module GStreamer de
  GTK — `Gtk.MediaFile.new()` = GtkNoMediaFile) : `folder.VIDEO_EXTS` / `is_video`, `scan(videos=)` (clé du cache des
  dossiers, `_scan_options`[3] ; interrupteur → relecture en direct, `_videos_shown`). Miniatures : module repris
  d'Odysseus (ffmpegthumbnailer sinon ffmpeg, cache freedesktop partagé), `ThumbLoader._make` en « video/ », image
  qui remplit la case ; ▶ sur la vignette (`_Cell.badge`, `.argos-video-badge`). `loader.load` : `Picture(video=True)`
  sans texture, dimensions / durée / codecs par ffprobe (`probe_video`, rotation comprise). Visionneuse :
  `Gtk.Video` (autoplay) en overlay au-dessus de l'ImageView (`_play` / `_stop_video` : arrêtée en changeant
  d'élément — dans `show_index`, le son s'arrête tout de suite —, en revenant à la grille, à la fermeture) ; erreur
  du flux → page « Lecture impossible » en clair (message de GStreamer dans l'infobulle) + gstreamer1.0-libav.
  Espace / K = lecture / pause (`toggle_video`), molette = élément suivant (contrôleur DISCRETE sur la vidéo, après
  le volume du lecteur), double-clic = plein écran, clic droit = menu. Pas de retouche (`editable`, `_sync_edit`
  grise pivoter / zoom / annoter ; menu sans arrière-plan, imprimer, copier l'image, convertir, annoter).
  Diaporama : photos seulement (`_step_index(photos_only)` ; PIÈGE : dans `_slideshow_tick`, `_slideshow_id` vient
  d'être remis à 0 → `slideshow_running()` est faux : `_go(1, photos_only=True)` explicite). Barre d'état : durée ;
  grille : « 12 photos, 1 vidéo » (`count_text`). Recommends : libgtk-4-media-gstreamer, gstreamer1.0-plugins-good, gstreamer1.0-plugins-bad (h265parse),
  gstreamer1.0-libav, ffmpeg. Lanceur : PAS de types vidéo (c'est Nausicaa). PIÈGE vu chez Tof (MKV HEVC 10 bits, « not-negotiated »
  alors que gst-play-1.0 le lisait) : les décodeurs MATÉRIELS de GStreamer (vah265dec de plugins-bad, vaapi…) sortent
  des images en mémoire de la carte graphique que le lecteur de GTK 4.14 n'accepte pas → `app.software_video_decoding`
  met leur rang à NONE dans GST_PLUGIN_FEATURE_RANK au démarrage (processus d'Argos seulement ; réglage de
  l'utilisateur gardé, après, donc gagnant). Confirmé chez Tof avec la variable posée à la main. Testé : `test_videos`, `videos_step`
  d'ui_smoke (webm VP9 fait par ffmpeg : lu, pause, durée, suivante = arrêt, diaporama qui saute, interrupteur).
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
  PIÈGE (vu en refaisant les captures du site) : le dossier lu APRÈS l'affichage relançait `show_index` sur la même
  photo → relue, zoom / rotation / recadrage commencés entre-temps remis à zéro. Maintenant, photo déjà demandée
  (`_shown_path`) : seulement sa place dans le dossier. Testé : « dossier lu après un zoom » d'ui_smoke.
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
- Taille des vignettes (retour d'un utilisateur : utile à ceux qui voient mal ; l'ancien réglage en pixels, appliqué
  au redémarrage, n'était pas trouvé) : Préférences → Affichage = Petites / Moyennes / Grandes / Très grandes
  (`gallery.GRID_SIZES` 96 / 128 / 192 / 256, `nearest_size` ramène un ancien réglage), EN DIRECT (`Grid.set_size` :
  nouvelle fabrique de cases, colonnes recalculées, sélection gardée ; fenêtre → `settings.connect`) ; aussi
  Ctrl + molette sur la grille (`_on_grid_wheel`, en capture) et + / − (`_zoom` : photo dans la visionneuse,
  vignettes dans la grille — d'où zoom-in / zoom-out hors de `_viewer_actions`). Le choix des Préférences suit.
- Plein écran (demande de Tof, capture) : flèches ‹ › et pointeur CACHÉS au repos — avant, la souris étant toujours
  « sur l'image », `overlay:hover` les laissait affichées. Classe `shown` posée par la fenêtre (`_show_chrome`) :
  à l'entrée en plein écran, à la souris près d'un BORD (15 %, 120 px au moins : `_on_view_motion`, faux mouvements
  de GTK ignorés), à chaque photo suivante / précédente (`go` : clavier, molette, flèches) ; retirée après
  `CHROME_MS` (2 s, pas tant que le pointeur est SUR une flèche). Souris au milieu : pointeur seul. Pointeur caché =
  `ImageView.set_pointer_hidden` (« none » dans `_idle_cursor`, jamais pendant un recadrage). CSS :
  `window.argos-fullscreen overlay > .argos-arrow.shown` (même spécificité que la règle :hover, placée après).
- Diaporama (demande de Tof) : F5, menu principal, clic droit (depuis CETTE photo) — plein écran, photo suivante
  toutes les `slideshow_delay` s (Préférences → Affichage, 2 à 15 s), Espace / P = pause / reprise, ← → naviguent
  (minuteur relancé), Échap ou sortie du plein écran = arrêt ; fin sans « Revenir au début » (`loop`) : arrêt +
  toast. Depuis la grille : la photo sélectionnée, sinon la première. Testé : ui_smoke (minuteur réduit).
  Transition (demande de Tof) : réglage `slideshow_transition` (Préférences → Affichage : glissement par défaut,
  fondu enchaîné, sans) ; diaporama ET navigation à la main (clavier, molette, flèches : demande de Tof) — sens du
  pas posé par `_go` (`_slide_step`), appliqué au `_loaded` de la photo ; une nouvelle photo pendant une transition
  coupe celle-ci (molette rapide). `ImageView.set_picture(transition=)` fige l'image d'avant en nœud de rendu
  (Gtk.Snapshot → `to_node`) puis Adw.TimedAnimation (glissement 900 ms en EASE_IN_OUT_CUBIC — 450 ms en EASE_OUT, trop brusque pour Tof —, fondu 600 ms ; animations coupées dans le système → aucune) :
  l'ancienne sort d'un côté, la nouvelle entre de l'autre (`translate`), ou `push_cross_fade`.
- « En changeant de photo » (Préférences → Affichage, demande de l'utilisateur allemand, comme gThumb ; réglage
  `zoom_change`) : « Ajuster à la fenêtre » (défaut, comme avant) ou « Garder le zoom » — le zoom choisi passe à la
  photo suivante (`_loaded`, après `set_picture`) ; une photo ajustée laisse la suivante ajustée. PAS de zoom retenu
  photo par photo (choix de Tof : déroutant). Testé dans ui_smoke.
- « Ouvrir Argos sur » (même retour ; l'arbre des dossiers RESTE, choix de Tof, masquable par F9) : réglages
  `startup` (last par défaut / pictures / folder) + `startup_folder`, Préférences → Général → Comportement
  (choisir « Un dossier choisi » sans dossier ouvre le sélecteur), `app.start_folder` (disparu → Images).
- `app.py` : instance unique ; `argos PHOTO` → son dossier, photo affichée ; `argos DOSSIER` → grille ; seul →
  `start_folder()` (dernier dossier par défaut, sinon Images). Nom du processus « argos » (prctl, l'icône du Moniteur système en dépend).

## AVIF / HEIC en haute définition (retour de Tof : gros dossiers d'AVIF très lents ; tests `test_heif`)

- PIÈGE : le loader heif de GdkPixbuf (heif-gdk-pixbuf) DÉCODE L'IMAGE ENTIÈRE pour `get_file_info` (0,67 s pour
  un AVIF de 24 Mpx, autant qu'un vrai décodage ; AV1 ne sait pas décoder en plus petit). Avant : 3 décodages par
  photo dans la visionneuse (`format_name` + contrôle de taille + lecture), et un par vignette de la grille ET de la
  bande À CHAQUE affichage, miniature en cache ou pas (`ThumbLoader._make` voulait la taille d'origine), plus deux
  dans le panneau d'infos. Maintenant `loader.image_size` : AVIF / HEIC lus dans l'EN-TÊTE (boîtes ISOBMFF
  « ispe », la plus grande = image entière — tuiles et vignette d'un HEIC de téléphone sont plus petites ; « irot »
  impair = largeur / hauteur échangées), GdkPixbuf pour le reste (rapide) ; `format_name` d'après l'extension pour
  ces formats. JAMAIS `get_file_info` sur une photo ailleurs que par `image_size`.
  Mesuré (12 AVIF 6000 × 4000, un fil) : visionneuse 1,76 → 0,74 s ; grille 1re visite 14,9 → 7,5 s ; retour sur le
  dossier 7,0 → 0,02 s. Le reste de la 1re visite = un décodage par miniature, inévitable (cache freedesktop
  partagé avec Nautilus ensuite).
- Cache de la visionneuse : 7 photos ET `CACHE_BYTES` (640 Mo de pixels) au plus — 7 AVIF de 48 Mpx décodés
  pesaient 1,3 Go ; la dernière lue reste toujours.
- Banc d'essai : `apt install heif-gdk-pixbuf libheif-examples libheif-plugin-dav1d libheif-plugin-aomenc` (heif-enc
  -A fabrique les AVIF ; `convert … plasma:fractal` pour une image de photo).

## Audit de novembre 2026 (tests `test_audit`, ils échouent sur l'ancien code ; « deux Ctrl+S » dans ui_smoke)

- ImageMagick lit « %d », « %o », « %x »… d'un chemin comme un GABARIT, à l'entrée comme à la sortie, et « %% »
  n'y échappe pas : « 50%off.jpg », dossier « Taux 5%o » → pivoter, recadrer, « Convertir / redimensionner »
  répondaient « no images defined ». `edit.magick_paths(src, out)` (repris d'Odysseus) : avec un « % », ImageMagick
  ne voit que des liens aux noms sûrs dans un dossier privé `argos-img-*` ; utilisé par `edit.apply`, `_quality`
  et `imagetools.process`. PIÈGE de test : `convert` d'un test qui FABRIQUE une photo au nom piégé se fait piéger
  aussi — la créer sous un nom sûr ailleurs puis la déplacer (`test_audit.photo`).
- Photo ouverte par un lien symbolique : enregistrer (et « Annuler ») remplaçait le LIEN par un fichier ;
  maintenant la vraie photo est remplacée (`realpath` dans `edit.apply` / `restore`), le lien reste.
- Enregistrer pendant un enregistrement (Ctrl+S deux fois, « Enregistrer ? » en changeant de photo pendant
  l'écriture d'une grosse photo) : `pending()` restait vrai, un second fil appliquait la rotation à la photo DÉJÀ
  pivotée. `MainWindow._saving` : un seul à la fois, les suites attendent (`_after_save`), pivoter / recadrer
  ignorés pendant l'écriture ; échec : on reste sur la photo, retouches gardées.
- settings.json en `ensure_ascii` : un dernier dossier au nom non UTF-8 bloquait tout enregistrement des réglages.
- Renommer en changeant la seule casse sur une clé FAT / exFAT (« photo.JPG » → « photo.jpg ») : refusé
  (« existe déjà », même fichier) → `samefile` dans `rename`.

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
   dark (Mount Everest.jpg --dark), light (Andes and the Pacific.jpg), crop (Lake
   Powell, Utah.jpg --dark --crop), info (Antennae galaxies.jpg --dark --info), grid (dossier --dark --info --select="Antennae galaxies.jpg" --delay=4000),
   slide (Aurora over Florida.jpg --dark --no-strip --slide=0.4), convert (dossier --dark --multi=4 --convert) ;
   le README montre dark + grid / slide / convert. Photos : images-api.nasa.gov.
   Aucune ligne de plus de 512 caractères dans site/index.html : lintian la prend pour du code minifié
   (`source-is-missing [site/index.html]`, vu à la 0.2.3 : pied de page à 577).
4. Essai chez Tof.
