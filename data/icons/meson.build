application_id = 'io.github.sugarycandybar.Hosty'
iconsdir = get_option('datadir') / 'icons'

scalable_apps_dir = 'hicolor' / 'scalable' / 'apps'
symbolic_apps_dir = 'hicolor' / 'symbolic' / 'apps'
scalable_actions_dir = 'hicolor' / 'scalable' / 'actions'

if get_option('beta')
  install_data(
    scalable_apps_dir / (application_id + '.Devel.svg'),
    install_dir: iconsdir / scalable_apps_dir,
    rename: application_id + '.svg',
  )
else
  install_data(
    scalable_apps_dir / (application_id + '.svg'),
    install_dir: iconsdir / scalable_apps_dir,
  )
endif

install_data(
  symbolic_apps_dir / (application_id + '-symbolic.svg'),
  install_dir: iconsdir / symbolic_apps_dir,
)

install_data(
  scalable_apps_dir / 'io.github.sugarycandybar.Crucible.svg',
  scalable_apps_dir / 'io.github.sugarycandybar.Carabiner.svg',
  install_dir: iconsdir / scalable_apps_dir,
)

install_data(
  scalable_actions_dir / 'plug-symbolic.svg',
  scalable_actions_dir / 'sliders-horizontal-symbolic.svg',
  scalable_actions_dir / 'power-profile-performance-symbolic.svg',
  install_dir: iconsdir / scalable_actions_dir,
)
