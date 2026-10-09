i18n = import('i18n')

desktop = i18n.merge_file(
  input: 'io.github.sugarycandybar.Hosty.desktop.in',
  output: 'io.github.sugarycandybar.Hosty.desktop',
  type: 'desktop',
  po_dir: '../../po',
  install: true,
  install_dir: get_option('datadir') / 'applications',
)

metainfo = i18n.merge_file(
  input: 'io.github.sugarycandybar.Hosty.metainfo.xml.in',
  output: 'io.github.sugarycandybar.Hosty.metainfo.xml',
  po_dir: '../../po',
  install: true,
  install_dir: get_option('datadir') / 'metainfo',
)

if get_option('beta')
  install_data(
    'io.github.sugarycandybar.Hosty.Devel.svg',
    install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps',
    rename: 'io.github.sugarycandybar.Hosty.svg',
  )
else
  install_data(
    'io.github.sugarycandybar.Hosty.svg',
    install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps'
  )
endif

install_data(
  'io.github.sugarycandybar.Hosty-symbolic.svg',
  install_dir: get_option('datadir') / 'icons/hicolor/symbolic/apps'
)

install_data(
  'io.github.sugarycandybar.Crucible.svg',
  install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps'
)

install_data(
  'io.github.sugarycandybar.Carabiner.svg',
  install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps'
)

install_data(
  'sliders-horizontal-symbolic.svg',
  install_dir: get_option('datadir') / 'icons/hicolor/scalable/actions'
)

install_data(
  'plug-symbolic.svg',
  install_dir: get_option('datadir') / 'icons/hicolor/scalable/actions'
)
